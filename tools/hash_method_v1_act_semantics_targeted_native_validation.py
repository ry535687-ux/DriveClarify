#!/usr/bin/env python3
"""Build the append-only hash inventory for the targeted native stage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "driveclarify_method_v1_act_semantics_targeted_native_validation"
OUTPUT = REPORT / "ARTIFACT_HASHES.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


artifacts = {
    path.relative_to(REPORT).as_posix(): sha256(path)
    for path in sorted(REPORT.rglob("*"))
    if path.is_file() and path != OUTPUT
}

production_sources = [
    "driveclarify_decision_evidence_v2/m2b.py",
    "driveclarify_grounded_language_v1/visualization.py",
    "driveclarify_grounded_language_v1_extension_e1_r1/runtime.py",
    "driveclarify_m3_live_authority/tagged_subject_v1.py",
    "driveclarify_persistent_ambiguity_runtime_v1/convergence_observer.py",
    "driveclarify_persistent_ambiguity_runtime_v1/m2b_adapter.py",
    "driveclarify_persistent_ambiguity_runtime_v1/method_v1_decision.py",
    "driveclarify_persistent_ambiguity_runtime_v1/runtime.py",
    "tools/run_method_v1_act_semantics_targeted_native_validation.py",
]
test_sources = [
    "tests/persistent_ambiguity_runtime_v1/test_convergence_observer_wiring.py",
]

aggregate = hashlib.sha256()
for name, digest in sorted(artifacts.items()):
    aggregate.update(f"{name}\0{digest}\n".encode("utf-8"))

payload = {
    "schema_version": "driveclarify.method_v1_targeted_native_artifact_hashes.v1",
    "status": "PASS_HASH_INVENTORY_COMPLETE",
    "algorithm": "sha256",
    "self_hash_included": False,
    "artifact_count": len(artifacts),
    "artifact_aggregate_sha256": aggregate.hexdigest(),
    "artifacts": artifacts,
    "production_source_sha256": {
        name: sha256(ROOT / name) for name in production_sources
    },
    "test_source_sha256": {name: sha256(ROOT / name) for name in test_sources},
    "protected_state_sha256": {
        "simlingo_checkpoint": "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28",
        "grounding_dino": "1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3",
        "e3_ledger": "9a7bba939f6865247674260dacd9c92d61561d691b3ae3ad0fb43d3b062fcc14",
        "e3_final_receipt": "bd546a75126b782b547ea5633ab10272f29ade884b8fe053ffdcd6cae880f798",
    },
}

OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps({
    "artifact_count": payload["artifact_count"],
    "artifact_aggregate_sha256": payload["artifact_aggregate_sha256"],
}, sort_keys=True))
