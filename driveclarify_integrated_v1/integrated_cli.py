"""Command-line entrypoint for one record or a document containing records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .canonical_ontology import canonical_json_bytes, stable_sha256
from .integrated_runtime import IntegratedRuntimeV1


def run_document(value: Any) -> dict[str, Any]:
    runtime = IntegratedRuntimeV1()
    if isinstance(value, dict) and isinstance(value.get("cases"), list):
        outputs = [runtime.run(item) for item in value["cases"]]
    elif isinstance(value, dict):
        outputs = [runtime.run(value)]
    else:
        raise ValueError("RUNTIME_DOCUMENT_MUST_BE_OBJECT_OR_CASE_SET")
    document = {
        "schema_version": "driveclarify.integrated_runtime_output_set.v1",
        "case_count": len(outputs),
        "outputs": outputs,
        "control_authorization_count": sum(bool(item.get("control_authorized")) for item in outputs),
    }
    return {**document, "deterministic_document_sha256": stable_sha256(document)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    value = json.loads(args.input.read_text(encoding="utf-8"))
    output = run_document(value)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(output))
    print(json.dumps({"case_count": output["case_count"], "output_sha256": stable_sha256(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

