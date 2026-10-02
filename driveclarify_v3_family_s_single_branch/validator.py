"""Pure-CPU, read-only validation of the complete Family-S artifact chain."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from jsonschema import Draft202012Validator

from driveclarify_v3_family_s_bplus.contracts import RoutePoint, SealedPDMBranchPlan
from .wrapper import (
    CANONICALIZATION_VERSION,
    SingleBranchEvidenceBundle,
)
from .evidence import (
    AUTH_SCHEMA,
    CLEANUP_SCHEMA,
    EVALUATOR_SCHEMA,
    EXPERT_OWNER as LAYERED_EXPERT_OWNER,
    FROZEN_TICKS as LAYERED_FROZEN_TICKS,
    OBSERVATION_SCHEMA,
    RUN_SCHEMA,
    EvidenceContractError,
    LayeredEvidenceBundle,
    TrustedValidationContext,
    cleanup_facts,
    content_hash as layered_content_hash,
    official_result_facts,
    verify_envelope,
)


FROZEN_SCHEMA_SHA256 = "1d9561336015ffa3547e1ee166602d6c93d2e83eaf7fa22a9d4cfdb0147ee9dd"
FROZEN_SCHEMA_VERSION = "driveclarify.v3.family-s-single-branch-record.v1"
FROZEN_TICKS = tuple(range(0, 100, 5))
EXPERT_OWNER = "OFFICIAL_PDM_LITE_AUTOPILOT_DATAAGENT_REALIZED_ROLLOUT"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class FrozenSchemaError(RuntimeError):
    """The configured schema is not the frozen authority."""


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    a1_training_eligible: bool
    exclusion_reason_code: Optional[str]
    exclusion_reason_codes: Tuple[str, ...]
    errors: Tuple[str, ...]


@dataclass(frozen=True)
class LayeredValidationResult:
    record_valid: bool
    run_valid: bool
    family_s_positive_eligible: bool
    a1_training_eligible: bool
    a1_dev_eligible: bool
    a1_test_eligible: bool
    exclusion_reason_codes: Tuple[str, ...]
    errors: Tuple[str, ...]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


class FamilySRecordValidator(object):
    """Independently recompute every plan/receipt/record/manifest cross-link."""

    _ARTIFACT_FILES = (
        "sealed_plan.json",
        "adapter_transaction.json",
        "wrapper_receipt.json",
        "record.json",
    )

    def __init__(self, schema_path: Optional[Path] = None) -> None:
        if schema_path is None:
            schema_path = (
                Path(__file__).resolve().parent.parent
                / "reports"
                / "driveclarify_v3_family_s_single_branch_selected_route_switch_protocol_redesign"
                / "FAMILY_S_RECORD_SCHEMA.json"
            )
        self._schema_path = Path(schema_path)
        raw = self._schema_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != FROZEN_SCHEMA_SHA256:
            raise FrozenSchemaError("FROZEN_SCHEMA_HASH_MISMATCH")
        self._schema = json.loads(raw.decode("utf-8"))
        Draft202012Validator.check_schema(self._schema)
        self._validator = Draft202012Validator(self._schema)

    @property
    def schema_path(self) -> Path:
        return self._schema_path

    @staticmethod
    def _invalid(errors: Sequence[str], codes: Sequence[str]) -> ValidationResult:
        unique_errors = tuple(sorted(set(errors)))
        unique_codes = tuple(sorted(set(codes))) or ("PROVENANCE_INCOMPLETE",)
        return ValidationResult(
            valid=False,
            a1_training_eligible=False,
            exclusion_reason_code=unique_codes[0],
            exclusion_reason_codes=unique_codes,
            errors=unique_errors,
        )

    @staticmethod
    def _valid(record: Mapping[str, Any], committed: bool) -> ValidationResult:
        # Candidate-local plan/receipt chains are retained only for structural
        # compatibility.  They can never grant A1 eligibility; the layered
        # validator requires explicit caller-selected roots and owner receipts.
        return ValidationResult(
            valid=True,
            a1_training_eligible=False,
            exclusion_reason_code=(
                "EXTERNAL_TRUST_CONTEXT_REQUIRED" if committed
                else "ATOMIC_COMMIT_AND_EXTERNAL_TRUST_NOT_VERIFIED"
            ),
            exclusion_reason_codes=((
                "EXTERNAL_TRUST_CONTEXT_REQUIRED" if committed
                else "ATOMIC_COMMIT_AND_EXTERNAL_TRUST_NOT_VERIFIED"
            ),),
            errors=(),
        )

    @staticmethod
    def _route_rows(value: Any, label: str, errors: list, codes: list) -> Tuple[RoutePoint, ...]:
        if not isinstance(value, list) or len(value) < 3:
            errors.append(label + "_POINT_COUNT_INVALID")
            codes.append("ROUTE_IDENTITY_MISMATCH")
            return ()
        route = []
        for index, row in enumerate(value):
            try:
                if not isinstance(row, dict) or set(row) != {"xyz_hex", "road_option"}:
                    raise ValueError
                xyz = row["xyz_hex"]
                if not isinstance(xyz, list) or len(xyz) != 3:
                    raise ValueError
                point = RoutePoint(
                    float.fromhex(xyz[0]), float.fromhex(xyz[1]),
                    float.fromhex(xyz[2]), row["road_option"],
                )
                if point.canonical() != row:
                    raise ValueError
                route.append(point)
            except (TypeError, ValueError, KeyError):
                errors.append(label + "_CANONICAL_POINT_INVALID_" + str(index))
                codes.append("ROUTE_IDENTITY_MISMATCH")
                return ()
        return tuple(route)

    @staticmethod
    def _plan_from_artifact(
            artifact: Mapping[str, Any], errors: list, codes: list) -> Optional[SealedPDMBranchPlan]:
        payload = artifact.get("branch_plan")
        if not isinstance(payload, Mapping):
            errors.append("SEALED_PLAN_PAYLOAD_MISSING")
            codes.append("PROVENANCE_INCOMPLETE")
            return None
        expected_keys = {
            "schema", "transaction_id", "branch_generation", "anchor_identity",
            "original_route_generation_identity", "selected_route_generation_identity",
            "original_route_hash", "route", "command_route_indices",
            "original_destination_xyz_hex", "source_hashes",
        }
        if set(payload) != expected_keys or payload.get("schema") != (
                "driveclarify.v3.sealed-pdm-branch-plan.v1"):
            errors.append("SEALED_PLAN_FIELD_SET_OR_SCHEMA_INVALID")
            codes.append("PROVENANCE_INCOMPLETE")
            return None
        route = FamilySRecordValidator._route_rows(
            payload.get("route"), "SEALED_PLAN_ROUTE", errors, codes
        )
        if not route:
            return None
        try:
            destination = tuple(
                float.fromhex(value)
                for value in payload["original_destination_xyz_hex"]
            )
            plan = SealedPDMBranchPlan(
                transaction_id=payload["transaction_id"],
                branch_generation=payload["branch_generation"],
                anchor_identity=payload["anchor_identity"],
                original_route_generation_identity=payload[
                    "original_route_generation_identity"
                ],
                selected_route_generation_identity=payload[
                    "selected_route_generation_identity"
                ],
                original_route_hash=payload["original_route_hash"],
                route=route,
                command_route_indices=tuple(payload["command_route_indices"]),
                original_destination_xyz=destination,
                source_hashes=tuple(payload["source_hashes"]),
            )
        except (TypeError, ValueError, KeyError) as error:
            errors.append("SEALED_PLAN_RECONSTRUCTION_FAILED:" + type(error).__name__)
            codes.append("PROVENANCE_INCOMPLETE")
            return None
        if artifact.get("branch_plan_hash") != plan.branch_plan_hash:
            errors.append("SEALED_PLAN_CONTENT_HASH_MISMATCH")
            codes.append("PROVENANCE_INCOMPLETE")
        return plan

    def validate_candidate(
            self,
            record: Mapping[str, Any],
            context: Optional[Mapping[str, Any]] = None) -> ValidationResult:
        """Legacy isolated JSON can never establish upstream transaction evidence."""
        return self._invalid(
            ["UPSTREAM_ARTIFACT_CHAIN_REQUIRED"],
            ["PROVENANCE_INCOMPLETE"],
        )

    def validate_bundle(
            self,
            record: Mapping[str, Any],
            bundle: SingleBranchEvidenceBundle) -> ValidationResult:
        if not isinstance(bundle, SingleBranchEvidenceBundle):
            return self._invalid(
                ["WRAPPER_EVIDENCE_BUNDLE_REQUIRED"], ["PROVENANCE_INCOMPLETE"]
            )
        try:
            plan, adapter, wrapper = bundle.artifacts()
        except (UnicodeError, json.JSONDecodeError):
            return self._invalid(
                ["WRAPPER_EVIDENCE_BUNDLE_INVALID"], ["PROVENANCE_INCOMPLETE"]
            )
        return self._validate_chain(
            record, plan, adapter, wrapper,
            manifest=None, artifact_root_name=None, committed=False,
        )

    def _validate_chain(
            self,
            record: Mapping[str, Any],
            plan_artifact: Mapping[str, Any],
            adapter_artifact: Mapping[str, Any],
            wrapper_receipt: Mapping[str, Any],
            manifest: Optional[Mapping[str, Any]],
            artifact_root_name: Optional[str],
            committed: bool) -> ValidationResult:
        errors = []  # type: list
        codes = []  # type: list
        if not isinstance(record, Mapping):
            return self._invalid(["RECORD_NOT_MAPPING"], ["SCHEMA_CONFORMANCE_FAILED"])

        schema_errors = sorted(
            self._validator.iter_errors(record),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        if record.get("schema_version") != FROZEN_SCHEMA_VERSION:
            errors.append("SCHEMA_VERSION_MISMATCH")
            codes.append("SCHEMA_VERSION_MISMATCH")
        if schema_errors:
            errors.extend(
                "SCHEMA:" + "/".join(str(part) for part in error.absolute_path)
                for error in schema_errors
            )
            codes.append("SCHEMA_CONFORMANCE_FAILED")

        for artifact, schema, label in (
                (plan_artifact, "driveclarify.v3.sealed-single-branch-plan-artifact.v1", "PLAN"),
                (adapter_artifact, "driveclarify.v3.pdm-adapter-transaction-artifact.v1", "ADAPTER"),
                (wrapper_receipt, "driveclarify.v3.single-branch-wrapper-receipt.v1", "WRAPPER")):
            if not isinstance(artifact, Mapping) or artifact.get("schema") != schema:
                errors.append(label + "_ARTIFACT_SCHEMA_INVALID")
                codes.append("PROVENANCE_INCOMPLETE")
            if artifact.get("canonicalization_version") != CANONICALIZATION_VERSION:
                errors.append(label + "_CANONICALIZATION_VERSION_MISMATCH")
                codes.append("ROUTE_IDENTITY_MISMATCH")

        plan = self._plan_from_artifact(plan_artifact, errors, codes)
        full_route = self._route_rows(
            plan_artifact.get("official_selected_full_route"),
            "OFFICIAL_SELECTED_FULL_ROUTE", errors, codes,
        )
        official_remaining = self._route_rows(
            plan_artifact.get("official_selected_remaining_route"),
            "OFFICIAL_SELECTED_REMAINING_ROUTE", errors, codes,
        )
        pdm_source = self._route_rows(
            plan_artifact.get("pdm_selected_source_remaining_route"),
            "PDM_SELECTED_SOURCE_REMAINING_ROUTE", errors, codes,
        )
        anchor_index = plan_artifact.get("anchor_route_index")
        if not isinstance(anchor_index, int) or not full_route or not (
                1 <= anchor_index < len(full_route)):
            errors.append("ANCHOR_ROUTE_INDEX_INVALID")
            codes.append("ROUTE_IDENTITY_MISMATCH")
        elif tuple(full_route[anchor_index:]) != official_remaining:
            errors.append("OFFICIAL_REMAINING_ANCHOR_SLICE_MISMATCH")
            codes.append("ROUTE_IDENTITY_MISMATCH")
        if plan is not None and tuple(plan.route) != pdm_source:
            errors.append("SEALED_PLAN_SOURCE_REMAINING_MISMATCH")
            codes.append("ROUTE_IDENTITY_MISMATCH")
        if official_remaining != pdm_source:
            errors.append("OFFICIAL_PDM_SOURCE_POINTWISE_MISMATCH")
            codes.append("ROUTE_IDENTITY_MISMATCH")

        full_hash = _content_hash([point.canonical() for point in full_route]) if full_route else None
        remaining_hash = (
            _content_hash([point.canonical() for point in official_remaining])
            if official_remaining else None
        )
        source_hash = (
            _content_hash([point.canonical() for point in pdm_source])
            if pdm_source else None
        )
        if plan_artifact.get("official_selected_route_hash") != full_hash:
            errors.append("OFFICIAL_FULL_ROUTE_CONTENT_HASH_MISMATCH")
            codes.append("ROUTE_IDENTITY_MISMATCH")
        if plan_artifact.get("official_selected_remaining_hash") != remaining_hash:
            errors.append("OFFICIAL_REMAINING_CONTENT_HASH_MISMATCH")
            codes.append("ROUTE_IDENTITY_MISMATCH")
        if plan_artifact.get("pdm_selected_source_remaining_hash") != source_hash:
            errors.append("PDM_SOURCE_REMAINING_CONTENT_HASH_MISMATCH")
            codes.append("ROUTE_IDENTITY_MISMATCH")

        plan_artifact_hash = _content_hash(plan_artifact)
        if adapter_artifact.get("sealed_plan_artifact_sha256") != plan_artifact_hash:
            errors.append("ADAPTER_PLAN_ARTIFACT_CROSS_LINK_MISMATCH")
            codes.append("PROVENANCE_INCOMPLETE")
        if plan is not None:
            if adapter_artifact.get("branch_plan_hash") != plan.branch_plan_hash:
                errors.append("ADAPTER_BRANCH_PLAN_HASH_MISMATCH")
                codes.append("PROVENANCE_INCOMPLETE")
            if adapter_artifact.get("transaction_id") != plan.transaction_id:
                errors.append("ADAPTER_PLAN_TRANSACTION_MISMATCH")
                codes.append("TRANSACTION_NOT_CONSUMED")

        raw_receipt = adapter_artifact.get("raw_adapter_receipt")
        if not isinstance(raw_receipt, Mapping):
            errors.append("RAW_ADAPTER_RECEIPT_MISSING")
            codes.append("PROVENANCE_INCOMPLETE")
            raw_receipt = {}
        raw_hash = _content_hash(raw_receipt)
        if adapter_artifact.get("raw_adapter_receipt_sha256") != raw_hash:
            errors.append("RAW_ADAPTER_RECEIPT_CONTENT_HASH_MISMATCH")
            codes.append("PROVENANCE_INCOMPLETE")
        if (
                raw_receipt.get("transaction_id") != adapter_artifact.get("transaction_id")
                or raw_receipt.get("branch_plan_hash") != adapter_artifact.get("branch_plan_hash")):
            errors.append("RAW_ADAPTER_RECEIPT_IDENTITY_MISMATCH")
            codes.append("TRANSACTION_NOT_CONSUMED")
        consumed = adapter_artifact.get("transaction_consumed_frame")
        created = adapter_artifact.get("transaction_created_frame")
        first = adapter_artifact.get("first_selected_pdm_control_frame")
        if (
                raw_receipt.get("anchor_frame") != consumed
                or raw_receipt.get("commit_state") != "CONSUMED"
                or raw_receipt.get("first_selected_control_consumed") is not True
                or not all(isinstance(value, int) for value in (created, consumed, first))
                or not created <= consumed == first):
            errors.append("ADAPTER_TRANSACTION_NOT_CONSUMED_IN_ORDER")
            codes.append("TRANSACTION_NOT_CONSUMED")
        if adapter_artifact.get("exactly_once_count") != 1:
            errors.append("TRANSACTION_NOT_EXACTLY_ONCE")
            codes.append("TRANSACTION_NOT_CONSUMED")
        if adapter_artifact.get("failure_state") != "NONE":
            errors.append("ADAPTER_FAILURE_STATE_PRESENT")
            codes.append("DUAL_PLANNER_ATOMICITY_FAILED")

        waypoint_installed = self._route_rows(
            adapter_artifact.get("waypoint_installed_selected_remaining_route"),
            "WAYPOINT_INSTALLED_REMAINING_ROUTE", errors, codes,
        )
        command_installed = self._route_rows(
            adapter_artifact.get("command_installed_selected_remaining_route"),
            "COMMAND_INSTALLED_REMAINING_ROUTE", errors, codes,
        )
        if waypoint_installed != pdm_source or command_installed != pdm_source:
            errors.append("INSTALLED_SOURCE_REMAINING_POINTWISE_MISMATCH")
            codes.append("ROUTE_IDENTITY_MISMATCH")
        for route, key in (
                (waypoint_installed, "waypoint_installed_selected_remaining_hash"),
                (command_installed, "command_installed_selected_remaining_hash")):
            computed = _content_hash([point.canonical() for point in route]) if route else None
            if adapter_artifact.get(key) != computed:
                errors.append(key.upper() + "_CONTENT_MISMATCH")
                codes.append("ROUTE_IDENTITY_MISMATCH")

        planner_pairs = (
            ("waypoint_planner_pre_hash", 0, "planner_pre_hashes"),
            ("command_planner_pre_hash", 1, "planner_pre_hashes"),
            ("waypoint_planner_post_hash", 0, "planner_post_hashes"),
            ("command_planner_post_hash", 1, "planner_post_hashes"),
        )
        for key, index, receipt_key in planner_pairs:
            raw_values = raw_receipt.get(receipt_key, ())
            raw_value = raw_values[index] if isinstance(raw_values, list) and len(raw_values) == 2 else None
            if adapter_artifact.get(key) != raw_value or not _is_hash(raw_value):
                errors.append("ADAPTER_" + key.upper() + "_MISMATCH")
                codes.append("DUAL_PLANNER_ATOMICITY_FAILED")
        if raw_receipt.get("old_route_residue_absent") is not True:
            errors.append("OLD_ROUTE_RESIDUE")
            codes.append("OLD_ROUTE_RESIDUE")

        adapter_hash = _content_hash(adapter_artifact)
        if wrapper_receipt.get("sealed_plan_artifact_sha256") != plan_artifact_hash:
            errors.append("WRAPPER_PLAN_ARTIFACT_CROSS_LINK_MISMATCH")
            codes.append("PROVENANCE_INCOMPLETE")
        if wrapper_receipt.get("adapter_transaction_artifact_sha256") != adapter_hash:
            errors.append("WRAPPER_ADAPTER_ARTIFACT_CROSS_LINK_MISMATCH")
            codes.append("PROVENANCE_INCOMPLETE")
        wrapper_cross_links = (
            "branch_plan_hash", "transaction_id", "transaction_created_frame",
            "transaction_consumed_frame", "first_selected_pdm_control_frame",
            "pdm_selected_source_remaining_hash", "waypoint_planner_pre_hash",
            "command_planner_pre_hash", "waypoint_planner_post_hash",
            "command_planner_post_hash", "exactly_once_count", "failure_state",
            "raw_adapter_receipt_sha256",
        )
        if any(wrapper_receipt.get(key) != adapter_artifact.get(key) for key in wrapper_cross_links):
            errors.append("WRAPPER_ADAPTER_CROSS_LINK_MISMATCH")
            codes.append("PROVENANCE_INCOMPLETE")
        if wrapper_receipt.get("official_selected_route_hash") != full_hash:
            errors.append("WRAPPER_FULL_ROUTE_HASH_MISMATCH")
            codes.append("ROUTE_IDENTITY_MISMATCH")
        if wrapper_receipt.get("official_selected_remaining_hash") != remaining_hash:
            errors.append("WRAPPER_OFFICIAL_REMAINING_HASH_MISMATCH")
            codes.append("ROUTE_IDENTITY_MISMATCH")
        if wrapper_receipt.get("pdm_installed_selected_remaining_hash") != source_hash:
            errors.append("WRAPPER_INSTALLED_REMAINING_HASH_MISMATCH")
            codes.append("ROUTE_IDENTITY_MISMATCH")

        route = record.get("route_identity", {})
        transaction = record.get("route_switch_transaction", {})
        target = record.get("observation_and_expert_target", {})
        validity = record.get("validity", {})
        record_route_links = (
            ("old_route_hash", plan.original_route_hash if plan is not None else None),
            ("selected_route_hash", full_hash),
            ("official_selected_route_hash", full_hash),
            ("official_selected_remaining_hash", remaining_hash),
            ("pdm_selected_source_remaining_hash", source_hash),
            ("selected_destination_hash", plan.destination_hash if plan is not None else None),
        )
        if any(route.get(key) != expected for key, expected in record_route_links):
            errors.append("RECORD_ROUTE_ARTIFACT_CROSS_LINK_MISMATCH")
            codes.append("ROUTE_IDENTITY_MISMATCH")
        if route.get("official_pdm_remaining_pointwise_identity") is not True:
            errors.append("RECORD_POINTWISE_IDENTITY_NOT_ASSERTED")
            codes.append("ROUTE_IDENTITY_MISMATCH")
        transaction_links = (
            ("transaction_id", "transaction_id"),
            ("transaction_created_frame", "transaction_created_frame"),
            ("transaction_consumed_frame", "transaction_consumed_frame"),
            ("first_selected_pdm_control_frame", "first_selected_pdm_control_frame"),
            ("waypoint_planner_pre_hash", "waypoint_planner_pre_hash"),
            ("command_planner_pre_hash", "command_planner_pre_hash"),
            ("waypoint_planner_post_hash", "waypoint_planner_post_hash"),
            ("command_planner_post_hash", "command_planner_post_hash"),
            ("route_switch_active_start", "route_switch_active_start"),
            ("route_switch_active_end", "route_switch_active_end"),
        )
        if any(
                transaction.get(record_key) != wrapper_receipt.get(wrapper_key)
                for record_key, wrapper_key in transaction_links):
            errors.append("RECORD_WRAPPER_TRANSACTION_CROSS_LINK_MISMATCH")
            codes.append("TRANSACTION_NOT_CONSUMED")
        if (
                transaction.get("old_route_residue_check") != "PASS"
                or transaction.get("atomic_commit_check") != "PASS"):
            errors.append("RECORD_TRANSACTION_VALIDITY_FAILED")
            codes.append("DUAL_PLANNER_ATOMICITY_FAILED")
        if transaction.get("first_selected_control_consumed") is not True:
            errors.append("RECORD_FIRST_SELECTED_CONTROL_NOT_CONSUMED")
            codes.append("TRANSACTION_NOT_CONSUMED")

        ticks = target.get("sample_tick_offsets")
        trajectories = target.get("expert_future_trajectory", ())
        trajectory_ticks = tuple(
            row.get("relative_tick") for row in trajectories if isinstance(row, Mapping)
        )
        source_frame = target.get("source_observation_frame")
        trajectory_frames = tuple(
            row.get("official_frame") for row in trajectories if isinstance(row, Mapping)
        )
        expected_frames = (
            tuple(source_frame + tick for tick in FROZEN_TICKS)
            if isinstance(source_frame, int) else ()
        )
        if (
                not isinstance(ticks, list) or tuple(ticks) != FROZEN_TICKS
                or len(trajectories) != 20
                or len(target.get("expert_speed_targets", ())) != 20
                or len(target.get("expert_control_targets", ())) != 20
                or trajectory_ticks != FROZEN_TICKS
                or trajectory_frames != expected_frames):
            errors.append("FIVE_TICK_TARGET_INCOMPLETE_OR_MOVED")
            codes.append("FIVE_TICK_TARGET_INCOMPLETE")
        if target.get("expert_owner") != EXPERT_OWNER:
            errors.append("EXPERT_OWNER_INVALID")
            codes.append("EXPERT_OWNER_INVALID")
        if validity.get("official_evaluator_result") not in ("Perfect", "Completed"):
            errors.append("OFFICIAL_EXECUTION_FAILED")
            codes.append("OFFICIAL_EXECUTION_FAILED")
        if validity.get("route_deviation_result") != "ABSENT":
            errors.append("OFFICIAL_ROUTE_DEVIATION")
            codes.append("OFFICIAL_ROUTE_DEVIATION")
        if validity.get("cleanup_status") != "PASS":
            errors.append("CLEANUP_EVIDENCE_INVALID")
            codes.append("CLEANUP_FAILED")

        if committed:
            if not isinstance(manifest, Mapping):
                errors.append("COMMIT_MANIFEST_MISSING")
                codes.append("PARTIAL_ARTIFACT_COMMIT")
            else:
                objects = {
                    "sealed_plan.json": plan_artifact,
                    "adapter_transaction.json": adapter_artifact,
                    "wrapper_receipt.json": wrapper_receipt,
                    "record.json": record,
                }
                file_hashes = manifest.get("artifact_sha256")
                if not isinstance(file_hashes, Mapping) or set(file_hashes) != set(objects) or any(
                        file_hashes.get(name) != _content_hash(value)
                        for name, value in objects.items()):
                    errors.append("COMMIT_MANIFEST_CONTENT_HASH_MISMATCH")
                    codes.append("PARTIAL_ARTIFACT_COMMIT")
                identity_payload = {
                    "record_id": record.get("record_id"),
                    "transaction_id": wrapper_receipt.get("transaction_id"),
                    "artifact_sha256": {
                        name: _content_hash(value) for name, value in objects.items()
                    },
                }
                artifact_set_id = _content_hash(identity_payload)
                expected_root = "artifact-" + artifact_set_id
                if (
                        manifest.get("schema") != "driveclarify.v3.family-s-commit-manifest.v1"
                        or manifest.get("artifact_set_id") != artifact_set_id
                        or manifest.get("artifact_root_name") != expected_root
                        or artifact_root_name != expected_root
                        or manifest.get("candidate_complete") is not True
                        or manifest.get("atomic_publish") != "DIRECTORY_RENAME"
                        or manifest.get("frozen_schema_sha256") != FROZEN_SCHEMA_SHA256):
                    errors.append("COMMIT_MANIFEST_OR_ROOT_BINDING_INVALID")
                    codes.append("PARTIAL_ARTIFACT_COMMIT")

        if errors:
            return self._invalid(errors, codes)
        return self._valid(record, committed=committed)

    def validate_artifact(self, artifact_directory: Path) -> ValidationResult:
        path = Path(artifact_directory)
        if (
                not path.is_dir() or ".partial" in path.name
                or path.name.endswith(".rejected")):
            return self._invalid(
                ["PARTIAL_OR_REJECTED_ARTIFACT_ROOT"], ["PARTIAL_ARTIFACT_COMMIT"]
            )
        required = self._ARTIFACT_FILES + ("commit_manifest.json",)
        if any(not (path / name).is_file() for name in required):
            return self._invalid(
                ["ARTIFACT_FILE_MISSING"], ["PARTIAL_ARTIFACT_COMMIT"]
            )
        try:
            values = {
                name: json.loads((path / name).read_text(encoding="utf-8"))
                for name in required
            }
        except (OSError, UnicodeError, json.JSONDecodeError):
            return self._invalid(
                ["ARTIFACT_READ_OR_JSON_FAILURE"], ["PARTIAL_ARTIFACT_COMMIT"]
            )
        return self._validate_chain(
            values["record.json"], values["sealed_plan.json"],
            values["adapter_transaction.json"], values["wrapper_receipt.json"],
            manifest=values["commit_manifest.json"],
            artifact_root_name=path.name,
            committed=True,
        )


class LayeredFamilySRecordValidator(FamilySRecordValidator):
    """Fail-closed validator rooted in caller-selected, multi-owner artifacts."""

    _LAYERED_FILES = LayeredEvidenceBundle.FILES + ("record.json",)

    @staticmethod
    def _layered_invalid(errors: Sequence[str], codes: Sequence[str]) -> LayeredValidationResult:
        return LayeredValidationResult(
            record_valid=False,
            run_valid=False,
            family_s_positive_eligible=False,
            a1_training_eligible=False,
            a1_dev_eligible=False,
            a1_test_eligible=False,
            exclusion_reason_codes=tuple(sorted(set(codes))) or (
                "PROVENANCE_INCOMPLETE",
            ),
            errors=tuple(sorted(set(errors))),
        )

    @staticmethod
    def _equal(
            actual: Any, expected: Any, label: str,
            errors: list, codes: list, code: str = "PROVENANCE_INCOMPLETE") -> None:
        if actual != expected:
            errors.append(label)
            codes.append(code)

    @staticmethod
    def _mapping(value: Any, label: str, errors: list, codes: list) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            errors.append(label)
            codes.append("PROVENANCE_INCOMPLETE")
            return {}
        return value

    @staticmethod
    def _owner_envelope(
            value: Mapping[str, Any], schema: str, label: str,
            errors: list, codes: list) -> Mapping[str, Any]:
        try:
            return verify_envelope(value, schema)
        except EvidenceContractError as error:
            errors.append(label + ":" + str(error))
            codes.append("PROVENANCE_INCOMPLETE")
            return {}

    def _validate_layered_values(
            self, record: Mapping[str, Any], candidate: Mapping[str, Any],
            trusted: Mapping[str, Any], committed: bool,
            commit_manifest: Optional[Mapping[str, Any]] = None,
            artifact_root_name: Optional[str] = None) -> LayeredValidationResult:
        errors = []  # type: list
        codes = []  # type: list

        if not isinstance(record, Mapping):
            return self._layered_invalid(
                ["RECORD_NOT_MAPPING"], ["SCHEMA_CONFORMANCE_FAILED"]
            )
        schema_errors = sorted(
            self._validator.iter_errors(record),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        if schema_errors:
            errors.extend(
                "SCHEMA:" + "/".join(str(part) for part in error.absolute_path)
                for error in schema_errors
            )
            codes.append("SCHEMA_CONFORMANCE_FAILED")
        if record.get("schema_version") != FROZEN_SCHEMA_VERSION:
            errors.append("SCHEMA_VERSION_MISMATCH")
            codes.append("SCHEMA_VERSION_MISMATCH")

        external_pairs = (
            ("sealed_plan.json", "trusted_sealed_plan"),
            ("adapter_transaction.json", "adapter_transaction"),
            ("wrapper_receipt.json", "wrapper_receipt"),
            ("observation_expert_receipt.json", "observation_expert_receipt"),
            ("evaluator_result_receipt.json", "evaluator_result_receipt"),
            ("cleanup_receipt.json", "cleanup_receipt"),
            ("run_manifest.json", "run_manifest"),
        )
        for candidate_name, trusted_name in external_pairs:
            self._equal(
                candidate.get(candidate_name), trusted.get(trusted_name),
                "CANDIDATE_COPY_DIFFERS_FROM_CALLER_SELECTED_" + trusted_name.upper(),
                errors, codes,
            )

        authorization = self._mapping(
            trusted.get("authorization_manifest"),
            "AUTHORIZATION_MANIFEST_MISSING", errors, codes,
        )
        auth = self._owner_envelope(
            authorization, AUTH_SCHEMA, "AUTHORIZATION_MANIFEST_INVALID", errors, codes,
        )
        authorization_hash = layered_content_hash(authorization) if authorization else None

        plan_artifact = self._mapping(
            trusted.get("trusted_sealed_plan"), "TRUSTED_SEALED_PLAN_MISSING",
            errors, codes,
        )
        plan_hash = layered_content_hash(plan_artifact) if plan_artifact else None
        self._equal(
            auth.get("sealed_plan_artifact_sha256"), plan_hash,
            "AUTHORIZATION_TRUSTED_PLAN_HASH_MISMATCH", errors, codes,
        )
        self._equal(
            auth.get("canonicalization_version"), CANONICALIZATION_VERSION,
            "AUTHORIZATION_CANONICALIZATION_MISMATCH", errors, codes,
            "ROUTE_IDENTITY_MISMATCH",
        )
        self._equal(
            auth.get("sampling_ticks"), list(LAYERED_FROZEN_TICKS),
            "AUTHORIZATION_FROZEN_TICKS_MISMATCH", errors, codes,
            "FIVE_TICK_TARGET_INCOMPLETE",
        )
        self._equal(
            (auth.get("route_switch_active_start"), auth.get("route_switch_active_end")),
            (0, 15), "AUTHORIZATION_MARKER_WINDOW_MISMATCH", errors, codes,
            "TRANSACTION_NOT_CONSUMED",
        )

        plan = self._plan_from_artifact(plan_artifact, errors, codes)
        full_route = self._route_rows(
            plan_artifact.get("official_selected_full_route"),
            "TRUSTED_OFFICIAL_SELECTED_FULL_ROUTE", errors, codes,
        )
        remaining_route = self._route_rows(
            plan_artifact.get("official_selected_remaining_route"),
            "TRUSTED_OFFICIAL_SELECTED_REMAINING_ROUTE", errors, codes,
        )
        source_route = self._route_rows(
            plan_artifact.get("pdm_selected_source_remaining_route"),
            "TRUSTED_PDM_SELECTED_SOURCE_REMAINING_ROUTE", errors, codes,
        )
        anchor_index = plan_artifact.get("anchor_route_index")
        anchor_point = plan_artifact.get("anchor_canonical_point")
        if (
                not isinstance(anchor_index, int) or anchor_index < 1
                or anchor_index >= len(full_route)):
            errors.append("TRUSTED_ANCHOR_ROUTE_INDEX_INVALID")
            codes.append("ROUTE_IDENTITY_MISMATCH")
        else:
            self._equal(
                full_route[anchor_index:].__class__(full_route[anchor_index:]),
                remaining_route,
                "TRUSTED_ANCHOR_SLICE_MISMATCH", errors, codes,
                "ROUTE_IDENTITY_MISMATCH",
            )
            self._equal(
                anchor_point, full_route[anchor_index].canonical(),
                "TRUSTED_ANCHOR_CANONICAL_POINT_MISMATCH", errors, codes,
                "ROUTE_IDENTITY_MISMATCH",
            )
        self._equal(
            remaining_route, source_route,
            "TRUSTED_OFFICIAL_PDM_SOURCE_POINTWISE_MISMATCH", errors, codes,
            "ROUTE_IDENTITY_MISMATCH",
        )
        if plan is not None:
            self._equal(
                tuple(plan.route), source_route,
                "TRUSTED_BRANCH_PLAN_SOURCE_ROUTE_MISMATCH", errors, codes,
                "ROUTE_IDENTITY_MISMATCH",
            )
        computed_full_hash = (
            _content_hash([point.canonical() for point in full_route])
            if full_route else None
        )
        computed_remaining_hash = (
            _content_hash([point.canonical() for point in remaining_route])
            if remaining_route else None
        )
        for key, expected in (
                ("official_selected_route_hash", computed_full_hash),
                ("official_selected_remaining_hash", computed_remaining_hash),
                ("pdm_selected_source_remaining_hash", computed_remaining_hash)):
            self._equal(
                plan_artifact.get(key), expected,
                "TRUSTED_PLAN_" + key.upper() + "_MISMATCH", errors, codes,
                "ROUTE_IDENTITY_MISMATCH",
            )

        auth_anchor = (
            auth.get("anchor_identity"), auth.get("anchor_route_index"),
            auth.get("anchor_canonical_point"), auth.get("anchor_world_pose"),
            auth.get("anchor_boundary_semantics"),
            auth.get("anchor_inclusion_convention"),
        )
        plan_anchor = (
            plan_artifact.get("anchor_identity"), anchor_index, anchor_point,
            plan_artifact.get("anchor_world_pose"),
            plan_artifact.get("anchor_boundary_semantics"),
            plan_artifact.get("anchor_inclusion_convention"),
        )
        self._equal(
            plan_anchor, auth_anchor, "AUTHORIZATION_PLAN_ANCHOR_MISMATCH",
            errors, codes, "ROUTE_IDENTITY_MISMATCH",
        )

        adapter = self._mapping(
            trusted.get("adapter_transaction"), "ADAPTER_RECEIPT_MISSING",
            errors, codes,
        )
        wrapper = self._mapping(
            trusted.get("wrapper_receipt"), "WRAPPER_RECEIPT_MISSING",
            errors, codes,
        )
        if adapter.get("schema") != "driveclarify.v3.pdm-adapter-transaction-artifact.v1":
            errors.append("ADAPTER_SCHEMA_INVALID")
            codes.append("PROVENANCE_INCOMPLETE")
        self._equal(
            adapter.get("authorized_manifest_sha256"), authorization_hash,
            "ADAPTER_AUTHORIZATION_LINK_MISMATCH", errors, codes,
        )
        self._equal(
            adapter.get("sealed_plan_artifact_sha256"), plan_hash,
            "ADAPTER_TRUSTED_PLAN_LINK_MISMATCH", errors, codes,
        )
        adapter_anchor = tuple(adapter.get(key) for key in (
            "anchor_identity", "anchor_route_index", "anchor_canonical_point",
            "anchor_world_pose", "anchor_boundary_semantics",
            "anchor_inclusion_convention",
        ))
        self._equal(
            adapter_anchor, auth_anchor, "ADAPTER_AUTHORIZED_ANCHOR_MISMATCH",
            errors, codes, "ROUTE_IDENTITY_MISMATCH",
        )
        adapter_source = self._route_rows(
            adapter.get("pdm_selected_source_remaining_route"),
            "ADAPTER_SELECTED_SOURCE_ROUTE", errors, codes,
        )
        waypoint_installed = self._route_rows(
            adapter.get("waypoint_installed_selected_remaining_route"),
            "ADAPTER_WAYPOINT_INSTALLED_ROUTE", errors, codes,
        )
        command_projection = self._route_rows(
            adapter.get("command_installed_source_projection_remaining_route"),
            "ADAPTER_COMMAND_SOURCE_PROJECTION_ROUTE", errors, codes,
        )
        command_sparse = self._route_rows(
            adapter.get("command_native_sparse_route"),
            "ADAPTER_COMMAND_NATIVE_SPARSE_ROUTE", errors, codes,
        )
        self._equal(
            adapter_source, source_route, "ADAPTER_SOURCE_NOT_DERIVED_FROM_TRUSTED_PLAN",
            errors, codes, "ROUTE_IDENTITY_MISMATCH",
        )
        self._equal(
            waypoint_installed, source_route, "ADAPTER_WAYPOINT_INSTALL_MISMATCH",
            errors, codes, "ROUTE_IDENTITY_MISMATCH",
        )
        self._equal(
            command_projection, source_route,
            "ADAPTER_COMMAND_SOURCE_PROJECTION_MISMATCH", errors, codes,
            "ROUTE_IDENTITY_MISMATCH",
        )
        self._equal(
            adapter.get("command_installed_selected_remaining_route"),
            adapter.get("command_installed_source_projection_remaining_route"),
            "ADAPTER_LEGACY_COMMAND_ROUTE_ALIAS_MISMATCH", errors, codes,
            "ROUTE_IDENTITY_MISMATCH",
        )
        self._equal(
            adapter.get("command_installed_selected_remaining_hash"),
            adapter.get("command_installed_source_projection_remaining_hash"),
            "ADAPTER_LEGACY_COMMAND_HASH_ALIAS_MISMATCH", errors, codes,
            "ROUTE_IDENTITY_MISMATCH",
        )
        if plan is not None:
            self._equal(
                command_sparse, tuple(plan.command_route),
                "ADAPTER_COMMAND_NATIVE_SPARSE_ROUTE_MISMATCH", errors, codes,
                "ROUTE_IDENTITY_MISMATCH",
            )
        for key, route_value in (
                ("pdm_selected_source_remaining_hash", adapter_source),
                ("waypoint_installed_selected_remaining_hash", waypoint_installed),
                ("command_installed_source_projection_remaining_hash", command_projection),
                ("command_native_sparse_route_hash", command_sparse)):
            expected = (
                _content_hash([point.canonical() for point in route_value])
                if route_value else None
            )
            self._equal(
                adapter.get(key), expected, "ADAPTER_" + key.upper() + "_MISMATCH",
                errors, codes, "ROUTE_IDENTITY_MISMATCH",
            )

        raw = self._mapping(
            adapter.get("raw_adapter_receipt"), "RAW_ADAPTER_RECEIPT_MISSING",
            errors, codes,
        )
        self._equal(
            adapter.get("raw_adapter_receipt_sha256"), _content_hash(raw),
            "RAW_ADAPTER_RECEIPT_HASH_MISMATCH", errors, codes,
        )
        raw_expected = {
            "transaction_id": adapter.get("transaction_id"),
            "branch_plan_hash": adapter.get("branch_plan_hash"),
            "anchor_identity": adapter.get("anchor_identity"),
            "anchor_frame": adapter.get("transaction_consumed_frame"),
            "commit_state": "CONSUMED",
            "first_selected_control_consumed": True,
            "old_route_residue_absent": True,
        }
        for key, expected in raw_expected.items():
            self._equal(
                raw.get(key), expected, "RAW_ADAPTER_" + key.upper() + "_MISMATCH",
                errors, codes, "TRANSACTION_NOT_CONSUMED",
            )
        if plan is not None:
            for key, expected in (
                    ("original_route_generation_identity", plan.original_route_generation_identity),
                    ("selected_route_generation_identity", plan.selected_route_generation_identity),
                    ("destination_hash", plan.destination_hash),
                    ("branch_generation", plan.branch_generation)):
                self._equal(
                    raw.get(key), expected, "RAW_ADAPTER_" + key.upper() + "_MISMATCH",
                    errors, codes,
                )
        planner_pairs = (
            ("waypoint_planner_pre_hash", "planner_pre_hashes", 0),
            ("command_planner_pre_hash", "planner_pre_hashes", 1),
            ("waypoint_planner_post_hash", "planner_post_hashes", 0),
            ("command_planner_post_hash", "planner_post_hashes", 1),
        )
        for key, raw_key, index in planner_pairs:
            rows = raw.get(raw_key)
            raw_value = rows[index] if isinstance(rows, list) and len(rows) == 2 else None
            self._equal(
                adapter.get(key), raw_value,
                "ADAPTER_RAW_PLANNER_HASH_MISMATCH_" + key.upper(),
                errors, codes, "DUAL_PLANNER_ATOMICITY_FAILED",
            )
        if (
                adapter.get("exactly_once_count") != 1
                or adapter.get("failure_state") != "NONE"
                or adapter.get("transaction_consumed_frame")
                != adapter.get("first_selected_pdm_control_frame")):
            errors.append("ADAPTER_EXACTLY_ONCE_OR_CONSUMPTION_INVALID")
            codes.append("TRANSACTION_NOT_CONSUMED")

        adapter_hash = layered_content_hash(adapter) if adapter else None
        if wrapper.get("schema") != "driveclarify.v3.single-branch-wrapper-receipt.v1":
            errors.append("WRAPPER_SCHEMA_INVALID")
            codes.append("PROVENANCE_INCOMPLETE")
        for key, expected in (
                ("authorized_manifest_sha256", authorization_hash),
                ("sealed_plan_artifact_sha256", plan_hash),
                ("adapter_transaction_artifact_sha256", adapter_hash)):
            self._equal(
                wrapper.get(key), expected, "WRAPPER_" + key.upper() + "_MISMATCH",
                errors, codes,
            )
        wrapper_anchor = tuple(wrapper.get(key) for key in (
            "anchor_identity", "anchor_route_index", "anchor_canonical_point",
            "anchor_world_pose", "anchor_boundary_semantics",
            "anchor_inclusion_convention",
        ))
        self._equal(
            wrapper_anchor, auth_anchor, "WRAPPER_AUTHORIZED_ANCHOR_MISMATCH",
            errors, codes, "ROUTE_IDENTITY_MISMATCH",
        )
        self._equal(
            wrapper.get("pdm_selected_source_remaining_route"),
            [point.canonical() for point in source_route],
            "WRAPPER_SELECTED_SOURCE_ROUTE_MISMATCH", errors, codes,
            "ROUTE_IDENTITY_MISMATCH",
        )
        wrapper_adapter_links = (
            "branch_plan_hash", "transaction_id", "transaction_created_frame",
            "transaction_consumed_frame", "first_selected_pdm_control_frame",
            "pdm_selected_source_remaining_hash", "waypoint_planner_pre_hash",
            "command_planner_pre_hash", "waypoint_planner_post_hash",
            "command_planner_post_hash", "exactly_once_count", "failure_state",
            "raw_adapter_receipt_sha256",
        )
        for key in wrapper_adapter_links:
            self._equal(
                wrapper.get(key), adapter.get(key),
                "WRAPPER_ADAPTER_CROSS_LINK_MISMATCH_" + key.upper(),
                errors, codes,
            )
        self._equal(
            (wrapper.get("route_switch_active_start"), wrapper.get("route_switch_active_end")),
            (0, 15), "WRAPPER_MARKER_WINDOW_MISMATCH", errors, codes,
            "TRANSACTION_NOT_CONSUMED",
        )

        observation_artifact = self._mapping(
            trusted.get("observation_expert_receipt"),
            "OBSERVATION_EXPERT_RECEIPT_MISSING", errors, codes,
        )
        observation = self._owner_envelope(
            observation_artifact, OBSERVATION_SCHEMA,
            "OBSERVATION_EXPERT_RECEIPT_INVALID", errors, codes,
        )
        observation_links = (
            ("authorization_manifest_sha256", authorization_hash),
            ("sealed_plan_artifact_sha256", plan_hash),
            ("adapter_transaction_artifact_sha256", adapter_hash),
            ("wrapper_receipt_sha256", layered_content_hash(wrapper) if wrapper else None),
        )
        for key, expected in observation_links:
            self._equal(
                observation.get(key), expected,
                "OBSERVATION_" + key.upper() + "_MISMATCH", errors, codes,
            )
        observation_anchor = tuple(observation.get(key) for key in (
            "anchor_identity", "anchor_route_index", "anchor_canonical_point",
            "anchor_world_pose", "anchor_boundary_semantics",
            "anchor_inclusion_convention",
        ))
        self._equal(
            observation_anchor, auth_anchor,
            "OBSERVATION_AUTHORIZED_ANCHOR_MISMATCH", errors, codes,
            "ROUTE_IDENTITY_MISMATCH",
        )
        self._equal(
            observation.get("expert_owner_identity"), LAYERED_EXPERT_OWNER,
            "OBSERVATION_EXPERT_OWNER_INVALID", errors, codes,
            "EXPERT_OWNER_INVALID",
        )
        self._equal(
            observation.get("expert_route_sha256"), computed_remaining_hash,
            "OBSERVATION_EXPERT_ROUTE_MISMATCH", errors, codes,
            "ROUTE_IDENTITY_MISMATCH",
        )
        source_frame = observation.get("source_frame")
        trajectories = observation.get("expert_future_trajectory")
        ticks = observation.get("sample_tick_offsets")
        frames = [
            row.get("official_frame") for row in trajectories
            if isinstance(row, Mapping)
        ] if isinstance(trajectories, list) else []
        relative = [
            row.get("relative_tick") for row in trajectories
            if isinstance(row, Mapping)
        ] if isinstance(trajectories, list) else []
        if (
                ticks != list(LAYERED_FROZEN_TICKS)
                or relative != list(LAYERED_FROZEN_TICKS)
                or not isinstance(source_frame, int)
                or frames != [source_frame + tick for tick in LAYERED_FROZEN_TICKS]
                or len(observation.get("expert_speed_targets", ())) != 20
                or len(observation.get("expert_control_targets", ())) != 20):
            errors.append("OBSERVATION_FROZEN_TARGET_WINDOW_INVALID")
            codes.append("FIVE_TICK_TARGET_INCOMPLETE")
        sensor_payloads = self._mapping(
            observation.get("sensor_payloads"), "SENSOR_PAYLOAD_LEDGER_MISSING",
            errors, codes,
        )
        sensor_hashes = self._mapping(
            observation.get("sensor_source_hashes"), "SENSOR_SOURCE_HASHES_MISSING",
            errors, codes,
        )
        if set(sensor_payloads) != set(sensor_hashes) or not sensor_payloads:
            errors.append("SENSOR_PAYLOAD_HASH_DOMAIN_MISMATCH")
            codes.append("PROVENANCE_INCOMPLETE")
        for label, item in sensor_payloads.items():
            if (
                    not isinstance(item, Mapping)
                    or item.get("source_frame") != source_frame
                    or item.get("sha256") != sensor_hashes.get(label)):
                errors.append("SENSOR_PAYLOAD_BINDING_INVALID_" + str(label))
                codes.append("PROVENANCE_INCOMPLETE")
        if any(observation.get(key) != 0 for key in (
                "additional_world_ticks", "additional_sensors",
                "additional_callbacks", "additional_planners",
                "additional_pid_controllers", "additional_vehicle_control_writers",
                "additional_expert_forwards")):
            errors.append("OBSERVATION_HOOK_NOT_PASSIVE")
            codes.append("PROVENANCE_INCOMPLETE")

        official_artifact = self._mapping(
            trusted.get("official_result_artifact"),
            "OFFICIAL_RESULT_ARTIFACT_MISSING", errors, codes,
        )
        evaluator_artifact = self._mapping(
            trusted.get("evaluator_result_receipt"),
            "EVALUATOR_RECEIPT_MISSING", errors, codes,
        )
        evaluator = self._owner_envelope(
            evaluator_artifact, EVALUATOR_SCHEMA, "EVALUATOR_RECEIPT_INVALID",
            errors, codes,
        )
        try:
            parsed_facts = official_result_facts(official_artifact)
        except EvidenceContractError as error:
            parsed_facts = {}
            errors.append("OFFICIAL_RESULT_PARSE_FAILED:" + str(error))
            codes.append("OFFICIAL_EXECUTION_FAILED")
        for key, expected in parsed_facts.items():
            self._equal(
                evaluator.get(key), expected,
                "EVALUATOR_OFFICIAL_FACT_MISMATCH_" + key.upper(), errors, codes,
                "OFFICIAL_EXECUTION_FAILED",
            )
        self._equal(
            evaluator.get("official_result_artifact_sha256"),
            layered_content_hash(official_artifact) if official_artifact else None,
            "EVALUATOR_OFFICIAL_ARTIFACT_HASH_MISMATCH", errors, codes,
        )
        self._equal(
            evaluator.get("authorization_manifest_sha256"), authorization_hash,
            "EVALUATOR_AUTHORIZATION_LINK_MISMATCH", errors, codes,
        )
        if evaluator.get("official_evaluator_result") not in ("Perfect", "Completed"):
            errors.append("OFFICIAL_EXECUTION_FAILED")
            codes.append("OFFICIAL_EXECUTION_FAILED")
        if evaluator.get("route_deviation") is not False:
            errors.append("OFFICIAL_ROUTE_DEVIATION")
            codes.append("OFFICIAL_ROUTE_DEVIATION")
        if (
                evaluator.get("collision") is not False
                or evaluator.get("off_road") is not False
                or evaluator.get("wrong_lane") is not False
                or evaluator.get("traffic_rule_events")):
            errors.append("OFFICIAL_SAFETY_INVALID")
            codes.append("SAFETY_INVALID")
        if (
                evaluator.get("selected_goal_realization") != "PASS"
                or evaluator.get("selected_connector_entry") != "PASS"):
            errors.append("SELECTED_ROUTE_REALIZATION_FAILED")
            codes.append("OFFICIAL_EXECUTION_FAILED")
        if evaluator.get("route_deviation_is_failure") is not True:
            errors.append("ROUTE_DEVIATION_RULE_REINTERPRETED")
            codes.append("OFFICIAL_ROUTE_DEVIATION")

        process_receipt = self._mapping(
            trusted.get("process_receipt"), "PROCESS_RECEIPT_MISSING", errors, codes,
        )
        cleanup_artifact = self._mapping(
            trusted.get("cleanup_receipt"), "CLEANUP_RECEIPT_MISSING", errors, codes,
        )
        cleanup = self._owner_envelope(
            cleanup_artifact, CLEANUP_SCHEMA, "CLEANUP_RECEIPT_INVALID",
            errors, codes,
        )
        for key, expected in cleanup_facts(process_receipt).items():
            self._equal(
                cleanup.get(key), expected,
                "CLEANUP_PROCESS_FACT_MISMATCH_" + key.upper(), errors, codes,
                "CLEANUP_FAILED",
            )
        self._equal(
            cleanup.get("process_receipt_sha256"),
            layered_content_hash(process_receipt) if process_receipt else None,
            "CLEANUP_PROCESS_RECEIPT_HASH_MISMATCH", errors, codes,
            "CLEANUP_FAILED",
        )
        if cleanup.get("cleanup_result") != "PASS":
            errors.append("CLEANUP_FAILED")
            codes.append("CLEANUP_FAILED")

        self._equal(record.get("record_id"), auth.get("record_id"),
                    "RECORD_ID_NOT_AUTHORIZED", errors, codes)
        self._equal(record.get("episode_id"), auth.get("episode_id"),
                    "EPISODE_ID_NOT_AUTHORIZED", errors, codes)
        self._equal(record.get("record_class"), "FAMILY_S_POSITIVE",
                    "RECORD_CLASS_INVALID", errors, codes)
        self._equal(record.get("provenance"), auth.get("provenance"),
                    "RECORD_PROVENANCE_NOT_AUTHORIZED", errors, codes)
        self._equal(record.get("route_identity"), auth.get("route_identity"),
                    "RECORD_ROUTE_IDENTITY_NOT_AUTHORIZED", errors, codes,
                    "ROUTE_IDENTITY_MISMATCH")
        record_route = self._mapping(
            record.get("route_identity"), "RECORD_ROUTE_IDENTITY_MISSING",
            errors, codes,
        )
        for key, expected in (
                ("anchor_route_index", anchor_index),
                ("anchor_world_pose", auth.get("anchor_world_pose")),
                ("official_selected_route_hash", computed_full_hash),
                ("selected_route_hash", computed_full_hash),
                ("official_selected_remaining_hash", computed_remaining_hash),
                ("pdm_selected_source_remaining_hash", computed_remaining_hash),
                ("old_route_hash", plan.original_route_hash if plan is not None else None),
                ("selected_destination_hash", plan.destination_hash if plan is not None else None)):
            self._equal(
                record_route.get(key), expected,
                "RECORD_TRUSTED_ROUTE_MISMATCH_" + key.upper(), errors, codes,
                "ROUTE_IDENTITY_MISMATCH",
            )
        record_transaction = self._mapping(
            record.get("route_switch_transaction"),
            "RECORD_TRANSACTION_MISSING", errors, codes,
        )
        for record_key, wrapper_key in (
                ("transaction_id", "transaction_id"),
                ("transaction_created_frame", "transaction_created_frame"),
                ("transaction_consumed_frame", "transaction_consumed_frame"),
                ("first_selected_pdm_control_frame", "first_selected_pdm_control_frame"),
                ("route_switch_active_start", "route_switch_active_start"),
                ("route_switch_active_end", "route_switch_active_end"),
                ("waypoint_planner_pre_hash", "waypoint_planner_pre_hash"),
                ("command_planner_pre_hash", "command_planner_pre_hash"),
                ("waypoint_planner_post_hash", "waypoint_planner_post_hash"),
                ("command_planner_post_hash", "command_planner_post_hash")):
            self._equal(
                record_transaction.get(record_key), wrapper.get(wrapper_key),
                "RECORD_WRAPPER_TRANSACTION_MISMATCH_" + record_key.upper(),
                errors, codes, "TRANSACTION_NOT_CONSUMED",
            )
        if (
                record_transaction.get("route_switch_outer_forward_index") != 0
                or record_transaction.get("route_switch_active") is not True
                or record_transaction.get("old_route_residue_check") != "PASS"
                or record_transaction.get("atomic_commit_check") != "PASS"
                or record_transaction.get("first_selected_control_consumed") is not True
                or record_transaction.get("marker_origin")
                != "AUTHORITATIVE_TRANSACTION_ACTIVE_AND_CONSUMED"):
            errors.append("RECORD_TRANSACTION_SEMANTICS_INVALID")
            codes.append("TRANSACTION_NOT_CONSUMED")

        target = self._mapping(
            record.get("observation_and_expert_target"),
            "RECORD_OBSERVATION_TARGET_MISSING", errors, codes,
        )
        target_expected = {
            "source_observation_frame": observation.get("source_frame"),
            "rgb_source_hashes": observation.get("rgb_source_hashes"),
            "sensor_source_hashes": observation.get("sensor_source_hashes"),
            "ego_state": observation.get("ego_state"),
            "navigation_targets": observation.get("navigation_targets"),
            "navigation_targets_hash": observation.get("navigation_targets_sha256"),
            "expert_owner": observation.get("expert_owner_identity"),
            "expert_future_trajectory": observation.get("expert_future_trajectory"),
            "expert_speed_targets": observation.get("expert_speed_targets"),
            "expert_control_targets": observation.get("expert_control_targets"),
            "sample_tick_offsets": observation.get("sample_tick_offsets"),
        }
        self._equal(
            target, target_expected, "RECORD_OBSERVATION_RECEIPT_MISMATCH",
            errors, codes, "PROVENANCE_INCOMPLETE",
        )
        validity = self._mapping(
            record.get("validity"), "RECORD_VALIDITY_MISSING", errors, codes,
        )
        validity_expected = {
            "official_evaluator_result": evaluator.get("official_evaluator_result"),
            "route_deviation_result": (
                "PRESENT" if evaluator.get("route_deviation") else "ABSENT"
            ),
            "selected_goal_realization": evaluator.get("selected_goal_realization"),
            "selected_connector_entry": evaluator.get("selected_connector_entry"),
            "collision": evaluator.get("collision"),
            "off_road": evaluator.get("off_road"),
            "wrong_lane": evaluator.get("wrong_lane"),
            "traffic_rule_events": evaluator.get("traffic_rule_events"),
            "termination_reason": evaluator.get("termination_reason"),
            "cleanup_status": cleanup.get("cleanup_result"),
        }
        self._equal(
            validity, validity_expected, "RECORD_RESULT_OWNER_MISMATCH",
            errors, codes, "OFFICIAL_EXECUTION_FAILED",
        )
        eligibility = self._mapping(
            record.get("eligibility"), "RECORD_ELIGIBILITY_MISSING", errors, codes,
        )
        if (
                eligibility.get("family_s_positive_eligible") is not True
                or any(eligibility.get(key) is not False for key in (
                    "family_o_retention_eligible", "a1_training_eligible",
                    "a1_dev_eligible", "a1_test_eligible"))):
            errors.append("RUNTIME_RECORD_SPLIT_ELIGIBILITY_MUST_BEGIN_FALSE")
            codes.append("PROVENANCE_INCOMPLETE")

        run_artifact = self._mapping(
            trusted.get("run_manifest"), "RUN_MANIFEST_MISSING", errors, codes,
        )
        run = self._owner_envelope(
            run_artifact, RUN_SCHEMA, "RUN_MANIFEST_INVALID", errors, codes,
        )
        run_expected = {
            "episode_id": record.get("episode_id"),
            "record_id": record.get("record_id"),
            "authorization_manifest_sha256": authorization_hash,
            "trusted_sealed_plan_sha256": plan_hash,
            "adapter_transaction_sha256": adapter_hash,
            "wrapper_receipt_sha256": layered_content_hash(wrapper) if wrapper else None,
            "observation_expert_receipt_sha256": layered_content_hash(
                observation_artifact
            ) if observation_artifact else None,
            "official_result_artifact_sha256": layered_content_hash(
                official_artifact
            ) if official_artifact else None,
            "evaluator_result_receipt_sha256": layered_content_hash(
                evaluator_artifact
            ) if evaluator_artifact else None,
            "process_receipt_sha256": layered_content_hash(process_receipt)
            if process_receipt else None,
            "cleanup_receipt_sha256": layered_content_hash(cleanup_artifact)
            if cleanup_artifact else None,
            "record_sha256": layered_content_hash(record),
            "record_valid_candidate": True,
            "runtime_split_assignment": None,
            "runtime_a1_eligibility": False,
        }
        self._equal(
            run, run_expected, "RUN_MANIFEST_OWNER_CHAIN_MISMATCH", errors, codes,
        )

        if committed:
            objects = dict(candidate)
            objects["record.json"] = record
            hashes = {name: layered_content_hash(value) for name, value in objects.items()}
            identity = {
                "authorization_manifest_sha256": authorization_hash,
                "run_manifest_sha256": layered_content_hash(run_artifact)
                if run_artifact else None,
                "record_id": record.get("record_id"),
                "artifact_sha256": hashes,
            }
            artifact_set_id = layered_content_hash(identity)
            expected_root = "artifact-" + artifact_set_id
            manifest_expected = {
                "schema": "driveclarify.v3.layered-family-s-commit-manifest.v1",
                "artifact_set_id": artifact_set_id,
                "artifact_root_name": expected_root,
                "authorization_manifest_sha256": authorization_hash,
                "trusted_sealed_plan_sha256": plan_hash,
                "run_manifest_sha256": layered_content_hash(run_artifact)
                if run_artifact else None,
                "record_id": record.get("record_id"),
                "episode_id": record.get("episode_id"),
                "artifact_names": sorted(objects),
                "artifact_sha256": hashes,
                "frozen_schema_sha256": FROZEN_SCHEMA_SHA256,
                "record_eligibility_input_set": sorted(objects),
                "candidate_complete": True,
                "atomic_publish": "DIRECTORY_RENAME",
            }
            self._equal(
                commit_manifest, manifest_expected,
                "LAYERED_COMMIT_MANIFEST_MISMATCH", errors, codes,
                "PARTIAL_ARTIFACT_COMMIT",
            )
            self._equal(
                artifact_root_name, expected_root,
                "LAYERED_ARTIFACT_ROOT_MISMATCH", errors, codes,
                "PARTIAL_ARTIFACT_COMMIT",
            )

        if errors:
            return self._layered_invalid(errors, codes)
        return LayeredValidationResult(
            record_valid=True,
            run_valid=committed,
            family_s_positive_eligible=committed,
            a1_training_eligible=False,
            a1_dev_eligible=False,
            a1_test_eligible=False,
            exclusion_reason_codes=("SPLIT_NOT_ASSIGNED",),
            errors=(),
        )

    def validate_layered_bundle(
            self, record: Mapping[str, Any], bundle: LayeredEvidenceBundle,
            context: TrustedValidationContext) -> LayeredValidationResult:
        if not isinstance(bundle, LayeredEvidenceBundle):
            return self._layered_invalid(
                ["LAYERED_EVIDENCE_BUNDLE_REQUIRED"], ["PROVENANCE_INCOMPLETE"]
            )
        if not isinstance(context, TrustedValidationContext):
            return self._layered_invalid(
                ["EXTERNAL_TRUST_CONTEXT_REQUIRED"], ["PROVENANCE_INCOMPLETE"]
            )
        try:
            trusted = context.load()
        except EvidenceContractError as error:
            return self._layered_invalid(
                ["EXTERNAL_TRUST_CONTEXT_INVALID:" + str(error)],
                ["PROVENANCE_INCOMPLETE"],
            )
        return self._validate_layered_values(
            record, bundle.artifacts(), trusted, committed=False,
        )

    def validate_layered_artifact(
            self, artifact_directory: Path,
            context: TrustedValidationContext) -> LayeredValidationResult:
        path = Path(artifact_directory).resolve()
        if not isinstance(context, TrustedValidationContext):
            return self._layered_invalid(
                ["EXTERNAL_TRUST_CONTEXT_REQUIRED"], ["PROVENANCE_INCOMPLETE"]
            )
        if (
                not path.is_dir() or ".partial" in path.name
                or path.name.endswith(".rejected")):
            return self._layered_invalid(
                ["PARTIAL_OR_REJECTED_ARTIFACT_ROOT"], ["PARTIAL_ARTIFACT_COMMIT"]
            )
        try:
            for external in context.paths():
                if external == path or path in external.parents:
                    raise EvidenceContractError(
                        "TRUST_ROOT_OR_OWNER_RECEIPT_INSIDE_CANDIDATE_SET"
                    )
            required = self._LAYERED_FILES + ("commit_manifest.json",)
            if any(not (path / name).is_file() for name in required):
                raise EvidenceContractError("LAYERED_ARTIFACT_FILE_MISSING")
            values = {
                name: json.loads((path / name).read_text(encoding="utf-8"))
                for name in required
            }
            trusted = context.load()
        except (OSError, UnicodeError, json.JSONDecodeError, EvidenceContractError) as error:
            return self._layered_invalid(
                ["LAYERED_ARTIFACT_READ_FAILED:" + str(error)],
                ["PARTIAL_ARTIFACT_COMMIT"],
            )
        candidate = {name: values[name] for name in LayeredEvidenceBundle.FILES}
        return self._validate_layered_values(
            values["record.json"], candidate, trusted, committed=True,
            commit_manifest=values["commit_manifest.json"],
            artifact_root_name=path.name,
        )
