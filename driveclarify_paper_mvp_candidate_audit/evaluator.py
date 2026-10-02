"""Post-episode evaluator for catalog leakage in runtime candidate generation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_paper_mvp_runtime.contracts import (
    FORBIDDEN_RUNTIME_FIELDS,
    PolicyEpisodeInput,
    canonical_sha256,
)

from .contracts import (
    AUDIT_FILENAME,
    CandidateGenerationAuditReport,
    REQUIRED_SCENARIO_COUNT,
    RecordedCandidateGeneration,
    ScenarioCandidateLeakageAudit,
)


_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class _CandidateView:
    candidate_id: str
    interpretation_id: str
    prompt_text: str
    candidate_semantic_digest: str
    candidate_input_digest: str
    visual_anchor_digest: str


@dataclass(frozen=True)
class _GenerationView:
    status: str
    candidates: tuple[_CandidateView, ...]
    audit: Mapping[str, Any]
    contract_errors: tuple[str, ...]


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    return bool(
        type(value) is str
        and len(value) == 64
        and all(character in _HEX for character in value)
    )


def _string_tuple(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)) or any(
        type(item) is not str for item in value
    ):
        return None
    return tuple(value)


def _int_or_none(value: Any) -> int | None:
    return value if type(value) is int else None


def _bool_or_none(value: Any) -> bool | None:
    return value if type(value) is bool else None


def _generation_view(output: Any) -> _GenerationView:
    raw = _plain(output)
    if not isinstance(raw, Mapping):
        return _GenerationView("INVALID", (), {}, ("GENERATION_OUTPUT_NOT_MAPPING",))
    errors: list[str] = []
    status = raw.get("status")
    if type(status) is not str:
        status = "INVALID"
        errors.append("GENERATION_STATUS_INVALID")
    raw_candidates = raw.get("candidates")
    candidates: list[_CandidateView] = []
    if not isinstance(raw_candidates, (list, tuple)):
        errors.append("GENERATION_CANDIDATES_INVALID")
    else:
        for index, raw_candidate in enumerate(raw_candidates):
            candidate = _plain(raw_candidate)
            if not isinstance(candidate, Mapping):
                errors.append(f"CANDIDATE_{index}_NOT_MAPPING")
                continue
            fields = {
                name: candidate.get(name)
                for name in (
                    "candidate_id",
                    "interpretation_id",
                    "prompt_text",
                    "candidate_semantic_digest",
                    "candidate_input_digest",
                    "visual_anchor_digest",
                )
            }
            if any(type(value) is not str or not value for value in fields.values()):
                errors.append(f"CANDIDATE_{index}_FIELD_INVALID")
                continue
            for digest_field in (
                "candidate_semantic_digest",
                "candidate_input_digest",
                "visual_anchor_digest",
            ):
                if not _is_sha256(fields[digest_field]):
                    errors.append(
                        f"CANDIDATE_{index}_{digest_field.upper()}_INVALID"
                    )
            candidates.append(_CandidateView(**fields))
    audit = _plain(raw.get("audit"))
    if not isinstance(audit, Mapping):
        audit = {}
        errors.append("GENERATION_AUDIT_INVALID")
    if len(candidates) != 2 or len({item.candidate_id for item in candidates}) != 2:
        errors.append("GENERATION_REQUIRES_TWO_DISTINCT_CANDIDATES")
    if status != "READY":
        errors.append("GENERATION_STATUS_NOT_READY")
    return _GenerationView(str(status), tuple(candidates), audit, tuple(errors))


def _forbidden_paths(
    value: Any,
    denylist: frozenset[str],
    *,
    path: str,
) -> tuple[str, ...]:
    value = _plain(value)
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            child = path + "." + key_text
            if key_text.casefold() in denylist:
                found.append(child)
            found.extend(_forbidden_paths(item, denylist, path=child))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(
                _forbidden_paths(item, denylist, path=f"{path}[{index}]")
            )
    return tuple(sorted(set(found)))


def _string_leaves(value: Any, *, path: str) -> tuple[tuple[str, str], ...]:
    leaves: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            leaves.extend(_string_leaves(item, path=path + "." + str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            leaves.extend(_string_leaves(item, path=f"{path}[{index}]"))
    elif type(value) is str:
        leaves.append((path, value))
    return tuple(leaves)


def _catalog_annotations(
    scenario: Mapping[str, Any],
) -> tuple[
    dict[str, tuple[tuple[str, str], ...]],
    dict[str, tuple[tuple[str, str], ...]],
    dict[str, tuple[str, ...]],
]:
    interpretations = scenario.get("candidate_interpretations")
    if not isinstance(interpretations, Mapping):
        return {}, {}, {}
    text_by_candidate: dict[str, tuple[tuple[str, str], ...]] = {}
    hash_by_candidate: dict[str, tuple[tuple[str, str], ...]] = {}
    identifiers_by_candidate: dict[str, tuple[str, ...]] = {}
    for candidate_key, payload in interpretations.items():
        key = str(candidate_key)
        leaves = _string_leaves(payload, path="candidate_interpretations." + key)
        text_by_candidate[key] = leaves
        hashes: list[tuple[str, str]] = [
            ("candidate_interpretations." + key + ".$payload", canonical_sha256(payload))
        ]
        identifiers = {key}
        for leaf_path, text in leaves:
            hashes.append((leaf_path + ".$text_sha256", canonical_sha256(text)))
            if _is_sha256(text):
                hashes.append((leaf_path + ".$stored_sha256", text))
            if leaf_path.endswith(".candidate_id"):
                identifiers.add(text)
        hash_by_candidate[key] = tuple(hashes)
        identifiers_by_candidate[key] = tuple(sorted(identifiers))
    return text_by_candidate, hash_by_candidate, identifiers_by_candidate


def _runtime_integrity_errors(
    view: _GenerationView,
    policy_input_sha256: str | None,
) -> tuple[str, ...]:
    errors = list(view.contract_errors)
    audit = view.audit
    candidate_ids = tuple(item.candidate_id for item in view.candidates)
    semantic_hashes = tuple(
        item.candidate_semantic_digest for item in view.candidates
    )
    prompt_hashes = tuple(canonical_sha256(item.prompt_text) for item in view.candidates)
    checks = (
        ("candidate_ids", candidate_ids, "AUDIT_CANDIDATE_IDS_MISMATCH"),
        (
            "candidate_semantic_sha256",
            semantic_hashes,
            "AUDIT_CANDIDATE_SEMANTIC_HASH_MISMATCH",
        ),
        (
            "candidate_prompt_sha256",
            prompt_hashes,
            "AUDIT_CANDIDATE_PROMPT_HASH_MISMATCH",
        ),
    )
    for field, expected, error in checks:
        actual = _string_tuple(audit.get(field))
        if actual != expected:
            errors.append(error)
    if audit.get("status") != view.status:
        errors.append("AUDIT_STATUS_MISMATCH")
    if policy_input_sha256 is None or audit.get("policy_input_sha256") != policy_input_sha256:
        errors.append("AUDIT_POLICY_INPUT_HASH_MISMATCH")
    self_reported_overlap = audit.get("forbidden_field_overlap", ())
    if (
        not isinstance(self_reported_overlap, (list, tuple))
        or tuple(self_reported_overlap) != ()
    ):
        errors.append("RUNTIME_SELF_REPORTED_FORBIDDEN_FIELD_OVERLAP")
    return tuple(sorted(set(errors)))


def _overlap_findings(
    view: _GenerationView,
    scenario: Mapping[str, Any],
) -> tuple[
    tuple[Mapping[str, Any], ...],
    tuple[Mapping[str, Any], ...],
    tuple[Mapping[str, Any], ...],
    tuple[Mapping[str, Any], ...],
]:
    text_by_candidate, hash_by_candidate, identifiers_by_candidate = (
        _catalog_annotations(scenario)
    )
    text_findings: list[Mapping[str, Any]] = []
    hash_findings: list[Mapping[str, Any]] = []
    identifier_findings: list[Mapping[str, Any]] = []
    runtime_to_catalog: dict[str, set[str]] = {
        item.candidate_id: set() for item in view.candidates
    }
    for runtime_candidate in view.candidates:
        for catalog_candidate, leaves in text_by_candidate.items():
            for field_path, annotation_text in leaves:
                if runtime_candidate.prompt_text == annotation_text:
                    text_findings.append(
                        {
                            "runtime_candidate_id": runtime_candidate.candidate_id,
                            "catalog_candidate_id": catalog_candidate,
                            "catalog_field_path": field_path,
                            "overlap_sha256": canonical_sha256(annotation_text),
                        }
                    )
                    runtime_to_catalog[runtime_candidate.candidate_id].add(
                        catalog_candidate
                    )
        runtime_hashes = {
            "prompt_sha256": canonical_sha256(runtime_candidate.prompt_text),
            "candidate_semantic_digest": (
                runtime_candidate.candidate_semantic_digest
            ),
            "candidate_input_digest": runtime_candidate.candidate_input_digest,
            "visual_anchor_digest": runtime_candidate.visual_anchor_digest,
        }
        for runtime_field, runtime_hash in runtime_hashes.items():
            for catalog_candidate, hashes in hash_by_candidate.items():
                for field_path, catalog_hash in hashes:
                    if runtime_hash == catalog_hash:
                        hash_findings.append(
                            {
                                "runtime_candidate_id": runtime_candidate.candidate_id,
                                "runtime_hash_field": runtime_field,
                                "catalog_candidate_id": catalog_candidate,
                                "catalog_hash_path": field_path,
                                "overlap_sha256": runtime_hash,
                            }
                        )
                        runtime_to_catalog[runtime_candidate.candidate_id].add(
                            catalog_candidate
                        )
        for catalog_candidate, identifiers in identifiers_by_candidate.items():
            for runtime_field, runtime_identifier in (
                ("candidate_id", runtime_candidate.candidate_id),
                ("interpretation_id", runtime_candidate.interpretation_id),
            ):
                if runtime_identifier in identifiers:
                    identifier_findings.append(
                        {
                            "runtime_candidate_id": runtime_candidate.candidate_id,
                            "runtime_identifier_field": runtime_field,
                            "catalog_candidate_id": catalog_candidate,
                            "identifier_sha256": canonical_sha256(runtime_identifier),
                        }
                    )
                    runtime_to_catalog[runtime_candidate.candidate_id].add(
                        catalog_candidate
                    )

    runtime_order = tuple(item.candidate_id for item in view.candidates)
    mapped_order: tuple[str, ...] | None = None
    mapped = [runtime_to_catalog[item.candidate_id] for item in view.candidates]
    if mapped and all(len(item) == 1 for item in mapped):
        mapped_order = tuple(next(iter(item)) for item in mapped)
    order_findings: list[Mapping[str, Any]] = []
    catalog_orders = scenario.get("candidate_order_by_seed", {})
    if isinstance(catalog_orders, Mapping):
        for seed, raw_order in catalog_orders.items():
            if not isinstance(raw_order, (list, tuple)):
                continue
            order = tuple(str(item) for item in raw_order)
            match_type = None
            compared_runtime_order: tuple[str, ...] | None = None
            if runtime_order == order:
                match_type = "DIRECT_RUNTIME_IDENTIFIER_ORDER"
                compared_runtime_order = runtime_order
            elif mapped_order is not None and mapped_order == order:
                match_type = "ANNOTATION_MAPPED_RUNTIME_ORDER"
                compared_runtime_order = mapped_order
            if match_type is not None:
                order_findings.append(
                    {
                        "seed": str(seed),
                        "match_type": match_type,
                        "runtime_order_sha256": canonical_sha256(
                            compared_runtime_order
                        ),
                        "catalog_order_sha256": canonical_sha256(order),
                    }
                )
    key = lambda finding: json.dumps(finding, sort_keys=True, separators=(",", ":"))
    return (
        tuple(sorted(text_findings, key=key)),
        tuple(sorted(hash_findings, key=key)),
        tuple(sorted(identifier_findings, key=key)),
        tuple(sorted(order_findings, key=key)),
    )


def _missing_scenario_audit(
    scenario_id: str,
) -> ScenarioCandidateLeakageAudit:
    return ScenarioCandidateLeakageAudit(
        scenario_id=scenario_id,
        status="FAIL",
        record_present=False,
        runtime_input_contract_valid=False,
        runtime_output_contract_valid=False,
        policy_input_sha256=None,
        runtime_candidate_ids=(),
        runtime_reported_catalog_read_count=None,
        runtime_reported_evaluation_label_access_count=None,
        runtime_reported_catalog_candidate_order_visible=None,
        runtime_reported_annotation_overlap_checked=None,
        forbidden_field_paths=(),
        exact_text_overlap_findings=(),
        exact_hash_overlap_findings=(),
        identifier_overlap_findings=(),
        order_overlap_findings=(),
        annotation_overlap_count=0,
        runtime_audit_integrity_errors=("RUNTIME_RECORD_MISSING",),
        reason_codes=("RUNTIME_RECORD_MISSING",),
    )


def _scenario_audit(
    record: RecordedCandidateGeneration,
    scenario: Mapping[str, Any],
    denylist: frozenset[str],
    *,
    duplicated: bool,
) -> ScenarioCandidateLeakageAudit:
    forbidden = tuple(
        sorted(
            set(
                _forbidden_paths(
                    record.policy_input_projection,
                    denylist,
                    path="policy_input_projection",
                )
                + _forbidden_paths(
                    record.generation_output,
                    denylist,
                    path="generation_output",
                )
            )
        )
    )
    input_valid = True
    policy_input_sha256: str | None = None
    input_error: str | None = None
    try:
        episode = PolicyEpisodeInput.from_mapping(record.policy_input_projection)
        policy_input_sha256 = episode.input_digest
    except (KeyError, TypeError, ValueError) as exc:
        input_valid = False
        input_error = "RUNTIME_INPUT_CONTRACT_INVALID:" + type(exc).__name__

    view = _generation_view(record.generation_output)
    integrity_errors = list(
        _runtime_integrity_errors(view, policy_input_sha256)
    )
    if input_error is not None:
        integrity_errors.append(input_error)
    if duplicated:
        integrity_errors.append("DUPLICATE_RUNTIME_RECORD")
    text, hashes, identifiers, orders = _overlap_findings(view, scenario)
    annotation_overlap_count = len(text) + len(hashes) + len(identifiers) + len(orders)
    audit = view.audit
    catalog_reads = _int_or_none(audit.get("catalog_read_count"))
    label_accesses = _int_or_none(audit.get("evaluation_label_access_count"))
    order_visible = _bool_or_none(audit.get("catalog_candidate_order_visible"))
    annotation_checked = _bool_or_none(
        audit.get("runtime_annotation_overlap_checked")
    )
    reasons: list[str] = []
    if catalog_reads != 0:
        reasons.append("RUNTIME_CATALOG_READ_COUNT_NOT_ZERO")
    if label_accesses != 0:
        reasons.append("RUNTIME_EVALUATION_LABEL_ACCESS_COUNT_NOT_ZERO")
    if order_visible is not False:
        reasons.append("RUNTIME_CATALOG_ORDER_VISIBILITY_NOT_FALSE")
    if annotation_checked is not False:
        reasons.append("RUNTIME_ANNOTATION_CHECK_MUST_REMAIN_EVALUATOR_ONLY")
    if forbidden:
        reasons.append("FORBIDDEN_RUNTIME_FIELD_OVERLAP")
    if text:
        reasons.append("EXACT_ANNOTATION_TEXT_OVERLAP")
    if hashes:
        reasons.append("EXACT_ANNOTATION_HASH_OVERLAP")
    if identifiers:
        reasons.append("CATALOG_CANDIDATE_IDENTIFIER_OVERLAP")
    if orders:
        reasons.append("CATALOG_CANDIDATE_ORDER_OVERLAP")
    reasons.extend(integrity_errors)
    output_valid = not view.contract_errors
    passed = bool(
        input_valid
        and output_valid
        and not integrity_errors
        and catalog_reads == 0
        and label_accesses == 0
        and order_visible is False
        and annotation_checked is False
        and not forbidden
        and annotation_overlap_count == 0
    )
    return ScenarioCandidateLeakageAudit(
        scenario_id=record.scenario_id,
        status="PASS" if passed else "FAIL",
        record_present=True,
        runtime_input_contract_valid=input_valid,
        runtime_output_contract_valid=output_valid,
        policy_input_sha256=policy_input_sha256,
        runtime_candidate_ids=tuple(item.candidate_id for item in view.candidates),
        runtime_reported_catalog_read_count=catalog_reads,
        runtime_reported_evaluation_label_access_count=label_accesses,
        runtime_reported_catalog_candidate_order_visible=order_visible,
        runtime_reported_annotation_overlap_checked=annotation_checked,
        forbidden_field_paths=forbidden,
        exact_text_overlap_findings=text,
        exact_hash_overlap_findings=hashes,
        identifier_overlap_findings=identifiers,
        order_overlap_findings=orders,
        annotation_overlap_count=annotation_overlap_count,
        runtime_audit_integrity_errors=tuple(sorted(set(integrity_errors))),
        reason_codes=tuple(sorted(set(reasons))) if reasons else ("NO_LEAKAGE_DETECTED",),
    )


def evaluate_candidate_generation_records(
    records: Sequence[RecordedCandidateGeneration],
    frozen_catalog: Mapping[str, Any],
    *,
    catalog_path: str,
    catalog_sha256: str,
    evaluator_catalog_read_count: int,
) -> CandidateGenerationAuditReport:
    """Evaluate already-recorded runtime outputs after the policy episode."""

    if evaluator_catalog_read_count != 1:
        raise ValueError("EVALUATOR_MUST_READ_FROZEN_CATALOG_EXACTLY_ONCE")
    scenarios = frozen_catalog.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("FROZEN_CATALOG_SCENARIOS_REQUIRED")
    scenario_by_id: dict[str, Mapping[str, Any]] = {}
    for scenario in scenarios:
        if not isinstance(scenario, Mapping) or type(scenario.get("scenario_id")) is not str:
            raise ValueError("FROZEN_CATALOG_SCENARIO_ID_INVALID")
        scenario_id = str(scenario["scenario_id"])
        if scenario_id in scenario_by_id:
            raise ValueError("FROZEN_CATALOG_SCENARIO_ID_DUPLICATED")
        scenario_by_id[scenario_id] = scenario

    firewall = frozen_catalog.get("label_firewall", {})
    catalog_denylist = (
        firewall.get("evaluation_only_denylist", ())
        if isinstance(firewall, Mapping)
        else ()
    )
    denylist = frozenset(
        {item.casefold() for item in FORBIDDEN_RUNTIME_FIELDS}
        | {
            str(item).casefold()
            for item in catalog_denylist
            if type(item) is str
        }
    )
    record_lists: dict[str, list[RecordedCandidateGeneration]] = {}
    for record in records:
        if not isinstance(record, RecordedCandidateGeneration):
            raise TypeError("RECORDED_CANDIDATE_GENERATION_CONTRACT_REQUIRED")
        record_lists.setdefault(record.scenario_id, []).append(record)
    catalog_ids = tuple(scenario_by_id)
    record_ids = set(record_lists)
    missing = tuple(sorted(set(catalog_ids) - record_ids))
    extra = tuple(sorted(record_ids - set(catalog_ids)))
    duplicates = tuple(
        sorted(
            scenario_id
            for scenario_id, values in record_lists.items()
            if len(values) != 1
        )
    )
    scenario_audits: list[ScenarioCandidateLeakageAudit] = []
    for scenario_id in catalog_ids:
        values = record_lists.get(scenario_id, ())
        if not values:
            scenario_audits.append(_missing_scenario_audit(scenario_id))
            continue
        scenario_audits.append(
            _scenario_audit(
                values[0],
                scenario_by_id[scenario_id],
                denylist,
                duplicated=len(values) != 1,
            )
        )
    typed_audits = tuple(scenario_audits)
    exact_text_count = sum(
        len(item.exact_text_overlap_findings) for item in typed_audits
    )
    exact_hash_count = sum(
        len(item.exact_hash_overlap_findings) for item in typed_audits
    )
    identifier_count = sum(
        len(item.identifier_overlap_findings) for item in typed_audits
    )
    order_count = sum(len(item.order_overlap_findings) for item in typed_audits)
    annotation_count = sum(item.annotation_overlap_count for item in typed_audits)
    forbidden_count = sum(len(item.forbidden_field_paths) for item in typed_audits)
    runtime_reads = sum(
        item.runtime_reported_catalog_read_count or 0 for item in typed_audits
    )
    runtime_label_accesses = sum(
        item.runtime_reported_evaluation_label_access_count or 0
        for item in typed_audits
    )
    audited_count = len(set(catalog_ids).intersection(record_ids))
    passed_count = sum(item.status == "PASS" for item in typed_audits)
    predicates = {
        "catalog_contains_exactly_24_scenarios": len(catalog_ids)
        == REQUIRED_SCENARIO_COUNT,
        "runtime_records_cover_24_of_24_once": bool(
            audited_count == REQUIRED_SCENARIO_COUNT
            and not missing
            and not extra
            and not duplicates
        ),
        "all_24_scenario_audits_pass": passed_count == REQUIRED_SCENARIO_COUNT,
        "runtime_generator_catalog_read_count_zero": all(
            item.runtime_reported_catalog_read_count == 0 for item in typed_audits
        ),
        "runtime_generator_evaluation_label_access_count_zero": all(
            item.runtime_reported_evaluation_label_access_count == 0
            for item in typed_audits
        ),
        "runtime_generator_catalog_order_visibility_false": all(
            item.runtime_reported_catalog_candidate_order_visible is False
            for item in typed_audits
        ),
        "runtime_annotation_comparison_remains_evaluator_only": all(
            item.runtime_reported_annotation_overlap_checked is False
            for item in typed_audits
        ),
        "exact_text_overlap_zero": exact_text_count == 0,
        "exact_hash_overlap_zero": exact_hash_count == 0,
        "identifier_overlap_zero": identifier_count == 0,
        "order_overlap_zero": order_count == 0,
        "annotation_overlap_zero": annotation_count == 0,
        "forbidden_field_overlap_zero": forbidden_count == 0,
        "catalog_read_occurs_once_at_evaluator_boundary": (
            evaluator_catalog_read_count == 1 and runtime_reads == 0
        ),
    }
    passed = all(predicates.values())
    reasons = (
        ("ALL_24_RUNTIME_CANDIDATE_GENERATIONS_CATALOG_FREE",)
        if passed
        else tuple(
            "FAILED_" + name.upper()
            for name, value in predicates.items()
            if not value
        )
    )
    return CandidateGenerationAuditReport(
        status="PASS" if passed else "FAIL",
        catalog_path=catalog_path,
        catalog_sha256=catalog_sha256,
        catalog_read_boundary="EVALUATOR_ONLY_POST_EPISODE",
        evaluator_catalog_read_count=evaluator_catalog_read_count,
        required_scenario_count=REQUIRED_SCENARIO_COUNT,
        catalog_scenario_count=len(catalog_ids),
        audited_scenario_count=audited_count,
        passed_scenario_count=passed_count,
        missing_scenario_ids=missing,
        extra_scenario_ids=extra,
        duplicate_record_scenario_ids=duplicates,
        exact_text_overlap_count=exact_text_count,
        exact_hash_overlap_count=exact_hash_count,
        identifier_overlap_count=identifier_count,
        order_overlap_count=order_count,
        annotation_overlap_count=annotation_count,
        forbidden_field_overlap_count=forbidden_count,
        runtime_reported_catalog_read_count=runtime_reads,
        runtime_reported_evaluation_label_access_count=runtime_label_accesses,
        pass_predicates=predicates,
        scenario_audits=typed_audits,
        reason_codes=reasons,
    )


def run_candidate_generation_audit(
    records: Sequence[RecordedCandidateGeneration],
    *,
    frozen_catalog_path: str | Path,
    output_directory: str | Path,
) -> CandidateGenerationAuditReport:
    """Read the frozen catalog once, evaluate, and write the fixed audit file."""

    catalog_path = Path(frozen_catalog_path)
    raw_catalog = catalog_path.read_bytes()
    catalog = json.loads(raw_catalog.decode("utf-8"))
    if not isinstance(catalog, Mapping):
        raise ValueError("FROZEN_CATALOG_ROOT_MUST_BE_MAPPING")
    report = evaluate_candidate_generation_records(
        records,
        catalog,
        catalog_path=str(catalog_path.resolve()),
        catalog_sha256=hashlib.sha256(raw_catalog).hexdigest(),
        evaluator_catalog_read_count=1,
    )
    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / AUDIT_FILENAME
    output_path.write_text(
        json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return report


__all__ = [
    "evaluate_candidate_generation_records",
    "run_candidate_generation_audit",
]
