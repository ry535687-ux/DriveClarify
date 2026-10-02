"""Route-owner and semantic-G binding for the frozen RQ2 transition wrapper.

This module contains no timing bucket, oracle label, policy decision, or outcome.
It only binds a route subset to its prospectively registered destination and
composes a detached alternative-route template with the still-authoritative
downstream suffix when the template is a rejoin path rather than a complete
route to G.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .canonical import bytes_sha256, canonical_sha256
from .models import GlobalTask, RouteRow


REGISTRY_SCHEMA = "driveclarify.rq2.route_authority_registry.v1"
DIRECT_TERMINAL = "REGISTERED_GRAPH_TERMINAL"
REJOIN_SUFFIX = "REJOIN_AUTHORITATIVE_SUFFIX"


@dataclass(frozen=True)
class RouteAuthorityBinding:
    route_subset: str
    scene_route_identity: str
    route_sha256: str
    global_destination_identity: str
    global_destination_endpoint_digest: str
    candidate_route_mode: str
    planner_terminal: Mapping[str, Any]
    template_rejoin: Mapping[str, Any] | None

    def __post_init__(self) -> None:
        if self.candidate_route_mode not in {DIRECT_TERMINAL, REJOIN_SUFFIX}:
            raise ValueError("RQ2_CANDIDATE_ROUTE_MODE_INVALID")
        if len(self.route_sha256) != 64 or len(self.global_destination_endpoint_digest) != 64:
            raise ValueError("RQ2_ROUTE_AUTHORITY_DIGEST_INVALID")
        if self.candidate_route_mode == REJOIN_SUFFIX and self.template_rejoin is None:
            raise ValueError("RQ2_REJOIN_BINDING_MISSING")


def load_route_authority_registry(path: Path) -> Mapping[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != REGISTRY_SCHEMA:
        raise RuntimeError("RQ2_ROUTE_AUTHORITY_REGISTRY_SCHEMA_INVALID")
    routes = raw.get("routes")
    if not isinstance(routes, dict) or not routes:
        raise RuntimeError("RQ2_ROUTE_AUTHORITY_REGISTRY_EMPTY")
    return raw


def resolve_route_authority_binding(
    *,
    registry: Mapping[str, Any],
    route_subset: str,
    route_path: Path | None = None,
) -> RouteAuthorityBinding:
    try:
        raw = registry["routes"][str(route_subset)]
    except (KeyError, TypeError) as error:
        raise RuntimeError("RQ2_ROUTE_SUBSET_AUTHORITY_UNREGISTERED") from error
    binding = RouteAuthorityBinding(
        route_subset=str(route_subset),
        scene_route_identity=str(raw["scene_route_identity"]),
        route_sha256=str(raw["route_sha256"]),
        global_destination_identity=str(raw["global_destination_identity"]),
        global_destination_endpoint_digest=str(
            raw["global_destination_endpoint_digest"]
        ),
        candidate_route_mode=str(raw["candidate_route_mode"]),
        planner_terminal=dict(raw["planner_terminal"]),
        template_rejoin=(
            None if raw.get("template_rejoin") is None else dict(raw["template_rejoin"])
        ),
    )
    if route_path is not None and bytes_sha256(route_path.read_bytes()) != binding.route_sha256:
        raise RuntimeError("RQ2_ROUTE_FILE_AUTHORITY_HASH_MISMATCH")
    return binding


def assert_runtime_g_binding(
    *, binding: RouteAuthorityBinding, global_task: GlobalTask
) -> None:
    if global_task.global_destination_identity != binding.global_destination_identity:
        raise RuntimeError("RQ2_ROUTE_SUBSET_SEMANTIC_G_IDENTITY_MISMATCH")
    if global_task.destination_endpoint_digest != binding.global_destination_endpoint_digest:
        raise RuntimeError("RQ2_ROUTE_SUBSET_G_ENDPOINT_DIGEST_MISMATCH")


def _xyz(row: RouteRow) -> tuple[float, float, float]:
    return (float(row.x_m), float(row.y_m), float(row.z_m))


def _endpoint_xyz(value: Mapping[str, Any]) -> tuple[float, float, float]:
    endpoint = tuple(float(item) for item in value["xyz"])
    if len(endpoint) != 3 or not all(math.isfinite(item) for item in endpoint):
        raise RuntimeError("RQ2_ROUTE_AUTHORITY_ENDPOINT_INVALID")
    return endpoint


def _rows_with_recomputed_distances(rows: Sequence[RouteRow]) -> tuple[RouteRow, ...]:
    result: list[RouteRow] = []
    previous: tuple[float, float, float] | None = None
    for row in rows:
        point = _xyz(row)
        distance = 0.0 if previous is None else math.dist(previous, point)
        result.append(
            RouteRow(
                point[0],
                point[1],
                point[2],
                row.road_option,
                distance,
                coordinate_domain="CARLA_WORLD",
            )
        )
        previous = point
    return tuple(result)


def restore_verified_p_old_terminal(
    *,
    authoritative_suffix_world: Sequence[RouteRow],
    p_old_global_task: GlobalTask,
    runtime_global_task: GlobalTask,
) -> tuple[RouteRow, ...]:
    """Restore the exact semantic-G bytes after planner/world round-trip.

    P_old stores route rows in the translated SimLingo planner frame while its
    GlobalTask stores G in CARLA world coordinates.  Subtracting the planner
    translation can introduce floating round-off.  This function is not a
    tolerance or geometric acceptance rule: it first requires the complete
    frozen P_old G object to equal the runtime G object, then replaces only the
    terminal representation with those already-authoritative exact bytes.
    """

    rows = tuple(authoritative_suffix_world)
    if not rows:
        raise RuntimeError("RQ2_P_OLD_SUFFIX_EMPTY_FOR_TERMINAL_RESTORATION")
    if p_old_global_task != runtime_global_task:
        raise RuntimeError("RQ2_P_OLD_RUNTIME_G_IDENTITY_DRIFT")
    last = rows[-1]
    exact = runtime_global_task.endpoint_xyz_m
    restored = (
        *rows[:-1],
        RouteRow(
            exact[0],
            exact[1],
            exact[2],
            last.road_option,
            last.distance_from_previous_m,
            coordinate_domain="CARLA_WORLD",
        ),
    )
    return _rows_with_recomputed_distances(restored)


def bind_candidate_route_to_authoritative_g(
    *,
    template_rows: Sequence[RouteRow],
    authoritative_suffix_world: Sequence[RouteRow],
    global_task: GlobalTask,
    binding: RouteAuthorityBinding,
) -> tuple[tuple[RouteRow, ...], Mapping[str, Any]]:
    """Return a detached full candidate with one registered route/G owner.

    The nearest-row operation selects an index; it is not an admissibility
    threshold.  The terminal checks are exact digest/coordinate checks.  A
    different route generation cannot be supplied here because the caller
    converts the immutable P_old suffix captured at the update boundary.
    """

    assert_runtime_g_binding(binding=binding, global_task=global_task)
    template = tuple(template_rows)
    authoritative = tuple(authoritative_suffix_world)
    if not template:
        raise RuntimeError("RQ2_ALTERNATIVE_TEMPLATE_EMPTY")
    terminal_xyz = _endpoint_xyz(binding.planner_terminal)
    if binding.candidate_route_mode == DIRECT_TERMINAL:
        full = _rows_with_recomputed_distances(template)
        if _xyz(full[-1]) != terminal_xyz:
            raise RuntimeError("RQ2_DIRECT_GRAPH_TERMINAL_NOT_REGISTERED")
        return full, {
            "mode": DIRECT_TERMINAL,
            "template_row_count": len(template),
            "authoritative_suffix_rows_appended": 0,
            "planner_terminal_xyz_m": terminal_xyz,
            "binding_sha256": canonical_sha256(binding),
        }

    if not authoritative:
        raise RuntimeError("RQ2_AUTHORITATIVE_SUFFIX_EMPTY_FOR_REJOIN")
    assert binding.template_rejoin is not None
    rejoin_xyz = _endpoint_xyz(binding.template_rejoin)
    if _xyz(template[-1]) != rejoin_xyz:
        raise RuntimeError("RQ2_TEMPLATE_REJOIN_ENDPOINT_MISMATCH")
    nearest = min(
        range(len(authoritative)),
        key=lambda index: math.dist(rejoin_xyz, _xyz(authoritative[index])),
    )
    continuation = authoritative[nearest:]
    full = _rows_with_recomputed_distances((*template, *continuation))
    if _xyz(full[-1]) != terminal_xyz or _xyz(full[-1]) != global_task.endpoint_xyz_m:
        raise RuntimeError("RQ2_STITCHED_CANDIDATE_TERMINAL_NOT_EXACT_G")
    return full, {
        "mode": REJOIN_SUFFIX,
        "template_row_count": len(template),
        "authoritative_suffix_rows_appended": len(continuation),
        "authoritative_suffix_rejoin_index": nearest,
        "template_rejoin_xyz_m": rejoin_xyz,
        "planner_terminal_xyz_m": terminal_xyz,
        "binding_sha256": canonical_sha256(binding),
    }
