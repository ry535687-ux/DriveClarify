import copy
import json

import pytest

from driveclarify import compare_tasks
from driveclarify import evaluation
from driveclarify.core.contracts import ContractError
from driveclarify.paths import RESOURCES


def test_full_prediction_and_score_regression(tmp_path):
    output = tmp_path / "run"
    assert evaluation.evaluate(output) == 0
    receipt = json.loads((output / "receipt.json").read_text())
    assert (receipt["input_records"], receipt["predictions"], receipt["defined_inputs"]) == (176, 1056, 132)
    assert receipt["reference_matches"] == {"predictions": True, "scores": True}


def test_labels_are_first_read_after_prediction_lock(tmp_path, monkeypatch):
    output = tmp_path / "run"
    original = evaluation.read_lines
    observed = []

    def read(path):
        if path.name == "labels.jsonl":
            assert (output / "prediction-lock.json").is_file()
            lock = json.loads((output / "prediction-lock.json").read_text())
            assert evaluation.digest(output / "predictions.jsonl") == lock["predictions_sha256"]
            observed.append(True)
        return original(path)

    monkeypatch.setattr(evaluation, "read_lines", read)
    evaluation.evaluate(output)
    assert observed == [True]


def test_undefined_labels_do_not_become_zero_correctness(tmp_path):
    output = tmp_path / "run"
    evaluation.evaluate(output)
    import csv
    rows = list(csv.DictReader((output / "scores.csv").open()))
    undefined = [row for row in rows if row["truth_defined"] == "False"]
    assert len(undefined) == 44 * 6
    assert all(row["correct"] == row["wrong"] == "" for row in undefined)


def test_duplicate_labels_cannot_be_scored():
    label = {"public_id": "case"}
    with pytest.raises(ValueError, match="one-to-one"):
        evaluation.score([], [label, label])


def test_missing_task_evidence_stays_unknown_despite_trajectory_difference():
    config = json.loads((RESOURCES / "evaluation.json").read_text())
    inputs = evaluation.read_lines(RESOURCES / "inputs.jsonl")
    from driveclarify.core.features import _trajectory
    value = next(copy.deepcopy(row["input"]) for row in inputs
                 if _trajectory(row["input"], config)[0].value == "TASK_DIVERGENT")
    value["runtime_context"]["candidate_obligations"] = []
    value["grounding"] = {}
    assert compare_tasks(value, config)["relation"] == "UNKNOWN"


def test_truth_fields_are_rejected_before_method_comparison():
    value = copy.deepcopy(evaluation.read_lines(RESOURCES / "inputs.jsonl")[0]["input"])
    value["true_intent"] = "K1"
    with pytest.raises(ContractError, match="LABEL_FIREWALL"):
        compare_tasks(value, json.loads((RESOURCES / "evaluation.json").read_text()))

