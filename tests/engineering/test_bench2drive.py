"""原生工程入口的结果选择与覆盖保护；无需 CARLA / 模型导入。"""
import json

import pytest

import engineering.bench2drive as b2d


def result(root, attempt, score, status="Completed"):
    path = root / f"attempt_{attempt:02d}" / "official_checkpoint.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"_checkpoint": {"records": [{
        "status": status, "scores": {"score_composed": score,
                                     "score_route": score, "score_penalty": 1.0}
    }]}}))
    return path


def plan(root):
    root.mkdir()
    value = {"output": str(root), "runtime_files": {}, "paths": {},
             "scope": "NEW_SINGLE_ROUTE_QUALIFICATION", "offscreen": True,
             "routes": [{"route_id": "1711", "arm_order": ["A0", "A1"]}]}
    (root / "PLAN.json").write_text(json.dumps(value))
    return value


def test_first_low_score_is_kept_instead_of_best_later_result(tmp_path):
    first = result(tmp_path, 1, 0, "Failed - Route timeout")
    result(tmp_path, 2, 100)
    path, record = b2d.first_authoritative(tmp_path)
    assert path == first
    assert record["scores"]["score_composed"] == 0


@pytest.mark.parametrize("status", ["Failed - Agent crashed", "Failed - Agent couldn't be set up"])
def test_infrastructure_shell_does_not_replace_real_result(tmp_path, status):
    result(tmp_path, 1, 0, status)
    final = result(tmp_path, 2, 17, "Failed - Vehicle blocked")
    assert b2d.first_authoritative(tmp_path)[0] == final


def test_missing_arm_remains_missing_in_collection(tmp_path):
    root = tmp_path / "plan"
    plan(root)
    result(root / "results/1711/A0", 1, 0, "Failed - Route timeout")
    output = tmp_path / "summary"
    assert b2d.collect(root, output) == 2
    sources = json.loads((output / "RESULT_SOURCES.json").read_text())
    assert sources["complete"] is False
    assert sources["missing"] == [{"route_id": "1711", "arm": "A1", "status": "MISSING"}]
    assert (output / "official_merge/A0/1711.json").is_file()
    assert not (output / "official_merge/A1/1711.json").exists()


def test_completed_native_result_cannot_be_rerun(tmp_path, monkeypatch):
    root = tmp_path / "plan"
    plan(root)
    original = result(root / "results/1711/A0", 1, 1)
    original_bytes = original.read_bytes()
    monkeypatch.setattr(b2d, "preflight", lambda paths: {})
    with pytest.raises(ValueError, match="已有权威结果"):
        b2d.run_plan(root, arm="A0", dry_run=True)
    assert original.read_bytes() == original_bytes


def test_existing_plan_and_changed_runtime_are_rejected(tmp_path):
    root = tmp_path / "plan"
    value = plan(root)
    with pytest.raises(ValueError, match="拒绝覆盖"):
        b2d.prepare_plan({}, root)
    runtime = root / "runner.py"
    runtime.write_text("print('original')\n")
    value["runtime_files"] = {"runner.py": b2d.sha(runtime)}
    (root / "PLAN.json").write_text(json.dumps(value))
    b2d.validate_plan(root)
    runtime.write_text("print('changed')\n")
    with pytest.raises(ValueError, match="被改动"):
        b2d.validate_plan(root)
