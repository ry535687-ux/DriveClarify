#!/usr/bin/env python3
"""Release a formal simulated-passenger answer only after a durable ASK."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with temporary.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange", required=True, type=Path)
    parser.add_argument("--selected-candidate-id", required=True, choices=("A", "B"))
    parser.add_argument("--timeout-s", type=float, default=420.0)
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout_s
    while time.monotonic() < deadline:
        asks = sorted(args.exchange.glob("ask-*.json"))
        if len(asks) > 1:
            raise RuntimeError("RQ1_V2_MULTIPLE_ASK_RECEIPTS")
        if asks:
            ask_path = asks[0]
            ask = json.loads(ask_path.read_text(encoding="utf-8"))
            if ask.get("action") != "ASK" or ask.get("durable") is not True:
                raise RuntimeError("RQ1_V2_ASK_NOT_DURABLE")
            answer_path = args.exchange / "ORACLE_ANSWER.json"
            if answer_path.exists():
                raise RuntimeError("RQ1_V2_ANSWER_PREEXISTED_DURABLE_ASK")
            released = time.time_ns()
            _write(
                answer_path,
                {
                    "schema": "driveclarify.rq1_v2.formal-passenger-answer.v1",
                    "query_id": ask["query_id"],
                    "ask_receipt_id": ask["receipt_id"],
                    "selected_candidate_id": args.selected_candidate_id,
                    "released_epoch_ns": released,
                },
            )
            _write(
                args.exchange / "FORMAL_ANSWER_RELEASE_RECEIPT.json",
                {
                    "schema": "driveclarify.rq1_v2.formal-answer-release-receipt.v1",
                    "ask_raw_sha256": _sha(ask_path),
                    "answer_raw_sha256": _sha(answer_path),
                    "ask_written_epoch_ns": ask["written_epoch_ns"],
                    "answer_released_epoch_ns": released,
                    "answer_released_after_durable_ask": released
                    > int(ask["written_epoch_ns"]),
                    "pre_ask_answer_access_count": 0,
                    "authorized_answer_release_count": 1,
                },
            )
            return 0
        time.sleep(0.05)
    raise TimeoutError("RQ1_V2_DURABLE_ASK_NOT_OBSERVED")


if __name__ == "__main__":
    main()
