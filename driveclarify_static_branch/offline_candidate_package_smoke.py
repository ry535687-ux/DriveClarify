"""CPU-only torch round-trip validation for all six frozen packages."""

from __future__ import annotations

import io
import importlib
import sys
from pathlib import Path

from .offline_candidate_capture import OBSERVATION_BATCH, SIMLINGO, load_json, verify_observation_package


def main() -> int:
    for path in (str(SIMLINGO), str(SIMLINGO / "team_code")):
        if path not in sys.path:
            sys.path.insert(0, path)
    torch = importlib.import_module("torch")
    from driveclarify_candidate_stability.simlingo_live_adapter import _input_fingerprint
    from .offline_candidate_worker import _evidence_fingerprint

    index = load_json(OBSERVATION_BATCH / "OBSERVATION_PACKAGE_INDEX.json")
    count = 0
    for item in index["packages"]:
        manifest = Path(item["manifest_path"])
        verification = verify_observation_package(manifest, expected_unit_id=item["unit_id"], expected_observation_hash=item["observation_hash"])
        if verification["status"] != "PASS":
            raise RuntimeError("PACKAGE_VERIFY_FAILED:" + item["unit_id"])
        root = Path(item["package_path"])
        for path in sorted((root / "model_ready").glob("*.pt")):
            value = torch.load(str(path), map_location="cpu")
            stream = io.BytesIO()
            torch.save(value, stream)
            stream.seek(0)
            again = torch.load(stream, map_location="cpu")
            if type(again) is not type(value):
                raise RuntimeError("ROUND_TRIP_TYPE_MISMATCH:" + str(path))
            _evidence_fingerprint(torch, value, _input_fingerprint)
            count += 1
    expected = len(index["packages"]) * 8
    if count != expected:
        raise RuntimeError(
            "ROUND_TRIP_FIELD_COUNT_MISMATCH:expected={}:actual={}".format(expected, count)
        )
    print(
        "PASS_OFFLINE_PACKAGE_ROUND_TRIP_AND_FINGERPRINT fields={} packages={}".format(
            count, len(index["packages"])
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
