"""Fail closed when any frozen runtime source/config byte differs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatches = []
    missing = []
    schema_errors = []
    for row in manifest["files"]:
        required = {
            "absolute_path",
            "relative_path",
            "closure_category",
            "runtime_role",
            "tracked",
            "size_bytes",
            "sha256",
        }
        absent = sorted(required - set(row))
        if absent:
            schema_errors.append(",".join(absent))
            continue
        path = Path(row["absolute_path"])
        if not path.exists():
            missing.append(str(path))
        else:
            if path.stat().st_size != int(row["size_bytes"]):
                mismatches.append(str(path) + ":SIZE")
            elif digest(path) != row["sha256"]:
                mismatches.append(str(path) + ":SHA256")
    result = {
        "manifest": str(manifest_path),
        "file_count": len(manifest["files"]),
        "missing": missing,
        "mismatches": mismatches,
        "schema_errors": schema_errors,
        "status": "PASS" if not missing and not mismatches and not schema_errors else "FAIL",
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
