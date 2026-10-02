"""验证复现工具不会覆盖结果、绕过冻结摘要或复制仓库外文件。"""
import json

import pytest

from engineering.reproduce import fresh_directory, local_file, sha, verify_assets


def test_existing_output_is_preserved(tmp_path):
    output = tmp_path / "result"
    output.mkdir()
    evidence = output / "evidence.json"
    evidence.write_text('{"frozen": true}')
    with pytest.raises(FileExistsError):
        fresh_directory(output)
    assert evidence.read_text() == '{"frozen": true}'


def test_tampered_input_cannot_pass_preflight(tmp_path):
    data = tmp_path / "input.json"
    data.write_text('{"value": 1}')
    engineering = tmp_path / "engineering"
    engineering.mkdir()
    (engineering / "paper_assets.json").write_text(json.dumps({
        "files": [{"path": "input.json", "sha256": sha(data), "role": "input"}]
    }))
    verify_assets(tmp_path)
    data.write_text('{"value": 2}')
    with pytest.raises(ValueError, match="摘要不符"):
        verify_assets(tmp_path)


def test_manifest_cannot_escape_repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("not repository content")
    with pytest.raises(ValueError, match="仓库边界"):
        local_file(root, "../outside.txt")
    with pytest.raises(ValueError, match="仓库边界"):
        local_file(root, str(outside))


def test_symlink_cannot_export_external_content(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("not repository content")
    (root / "linked.txt").symlink_to(outside)
    with pytest.raises(ValueError):
        local_file(root, "linked.txt")
