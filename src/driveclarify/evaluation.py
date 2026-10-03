"""Evaluate the final method on public inputs; lock predictions before labels."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from .core.features import _trajectory
from .core.relation import compare_tasks
from .paths import RESOURCES

POLICIES = ["Never Clarify", "Always Clarify", "Ambiguity-Only",
            "Trajectory-Distance", "Random-Query@matched-rate", "DriveClarify"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_lines(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_csv(path, rows):
    with Path(path).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def predict(inputs, config):
    """No labels enter this function. Random-Query is an exact expectation."""
    predictions = []
    for row in inputs:
        value = row["input"]
        if [candidate["candidate_id"] for candidate in value["candidates"]] != ["K1", "K2"]:
            raise ValueError("Expected the public K1/K2 candidate binding")
        if any(not candidate["text"].strip() for candidate in value["candidates"]):
            raise ValueError("Candidate text is empty")
        relation = compare_tasks(value, config)["relation"]
        trajectory, details = _trajectory(value, config)
        probabilities = [0., 1., 1., float(trajectory.value == "TASK_DIVERGENT"),
                         1 / 3, float(relation == "TASK_DIVERGENT")]
        for policy, probability in zip(POLICIES, probabilities):
            predictions.append({
                "public_id": row["public_id"], "policy": policy,
                "query_probability": probability,
                "abstain": policy == "DriveClarify" and relation == "UNKNOWN",
                "relation": relation if policy == "DriveClarify" else
                            trajectory.value if policy == "Trajectory-Distance" else None,
                "default_candidate": "K1", "public_sha256": row["public_sha256"],
                "trajectory_details": details if policy == "Trajectory-Distance" else None,
            })
    return predictions


def score(predictions, labels):
    by_id = {row["public_id"]: row for row in labels}
    if len(by_id) != len(labels) or {row["public_id"] for row in predictions} != set(by_id):
        raise ValueError("Input and label identifiers do not form a one-to-one binding")
    rows = []
    for row in predictions:
        label = by_id[row["public_id"]]
        truth = label["truth_relation"]
        defined = truth in ("TASK_EQUIVALENT", "TASK_DIVERGENT")
        query = row["query_probability"]
        resolved = float(not row["abstain"])
        wrong = (1 - query) * .5 if truth == "TASK_DIVERGENT" else 0.
        rows.append({
            "public_id": row["public_id"], "record_id": label["record_id"],
            "layout_id": label["layout_id"], "split": label["split"], "policy": row["policy"],
            "truth_relation": truth, "truth_defined": defined,
            "correct": resolved - wrong if defined else None,
            "wrong": wrong if defined else None, "query": query,
            "unnecessary": query if truth == "TASK_EQUIVALENT" else None,
            "coverage": resolved,
            "value_type": "RANDOMIZATION_EXPECTATION" if "Random" in row["policy"]
                          else "BALANCED_INTENT_WEIGHTED",
        })
    return rows


def evaluate(output, inputs=RESOURCES / "inputs.jsonl", labels=RESOURCES / "labels.jsonl",
             config=RESOURCES / "evaluation.json", verify_reference=True):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    config_value = json.loads(Path(config).read_text())
    public = read_lines(inputs)
    predictions = predict(public, config_value)
    prediction_path = output / "predictions.jsonl"
    prediction_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in predictions))
    lock = {"predictions_sha256": digest(prediction_path), "inputs_sha256": digest(inputs),
            "config_sha256": digest(config), "input_records": len(public),
            "rows": len(predictions), "labels_read_before_lock": False,
            "new_model_forwards": 0}
    write_json(output / "prediction-lock.json", lock)
    # The label file is first decoded only after the prediction file is durable.
    label_rows = read_lines(labels)
    rows = score(predictions, label_rows)
    if digest(prediction_path) != lock["predictions_sha256"]:
        raise ValueError("Locked predictions changed during scoring")
    score_path = output / "scores.csv"
    write_csv(score_path, rows)
    summaries = []
    for policy in POLICIES:
        all_rows = [row for row in rows if row["policy"] == policy]
        defined = [row for row in all_rows if row["truth_defined"]]
        summaries.append({"policy": policy, "n_all": len(all_rows), "n_defined": len(defined),
                          **{key: sum(row[key] for row in defined if row[key] is not None) /
                                  sum(row[key] is not None for row in defined)
                             for key in ("correct", "wrong", "query", "unnecessary", "coverage")},
                          "all_coverage": sum(row["coverage"] for row in all_rows) / len(all_rows)})
    write_csv(output / "summary.csv", summaries)
    matches = {}
    if verify_reference:
        matches = {"predictions": digest(prediction_path) == digest(RESOURCES / "expected-predictions.jsonl"),
                   "scores": digest(score_path) == digest(RESOURCES / "expected-scores.csv")}
    receipt = {"status": "PASS" if all(matches.values()) else "MISMATCH",
               "input_records": len(public), "predictions": len(predictions),
               "defined_inputs": sum(row["truth_relation"] in ("TASK_EQUIVALENT", "TASK_DIVERGENT") for row in label_rows),
               "reference_matches": matches, "labels_sha256": digest(labels),
               "scope": "Saved controlled inputs and balanced-intent scoring; no new model inference or driving"}
    write_json(output / "receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["status"] == "PASS" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    return evaluate(args.output)
