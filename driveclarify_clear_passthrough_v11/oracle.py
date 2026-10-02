"""Durable ASK receipt writer implementing the V11 oracle firewall."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time

from .contracts import AskReceipt, canonical_sha256


class DurableAskWriter:
    def __init__(self, exchange_directory):
        self.exchange_directory = Path(exchange_directory).resolve()

    def write(self, question, observation_id):
        if not isinstance(question, str) or not question.strip():
            raise ValueError("ASK_QUESTION_REQUIRED")
        if not isinstance(observation_id, str) or not observation_id.strip():
            raise ValueError("ASK_OBSERVATION_ID_REQUIRED")
        self.exchange_directory.mkdir(parents=True, exist_ok=True)
        query_id = "query-" + canonical_sha256(
            {"question": question, "observation_id": observation_id}
        )[:24]
        receipt_id = "ask-" + canonical_sha256(
            {"query_id": query_id, "epoch_ns": time.time_ns()}
        )[:24]
        path = self.exchange_directory / (receipt_id + ".json")
        payload = {
            "schema": "driveclarify.v11.durable-ask-receipt.v1",
            "action": "ASK",
            "durable": True,
            "observation_id": observation_id,
            "query_id": query_id,
            "question": question,
            "receipt_id": receipt_id,
            "written_epoch_ns": time.time_ns(),
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        raw = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        with temporary.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
        descriptor = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        digest = canonical_sha256(payload)
        return AskReceipt(
            receipt_id=receipt_id,
            query_id=query_id,
            observation_id=observation_id,
            durable=True,
            path=str(path),
            sha256=digest,
        )


__all__ = ["DurableAskWriter"]
