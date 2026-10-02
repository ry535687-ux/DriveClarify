"""Semantic-G plus discrete OpenDRIVE terminal-region validation for RQ2.

This module deliberately contains no learned or distance-based acceptance
threshold.  A planner terminal is equivalent to the authoritative runtime G
only when both exact endpoint representations are members of one prospectively
registered, map-hash-bound legal terminal region.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .canonical import bytes_sha256, canonical_sha256
from .models import GlobalTask, RouteRow


REGISTRY_SCHEMA = "driveclarify.rq2.g_terminal_region_registry.v2"
CERTIFICATE_SCHEMA = "driveclarify.rq2.g_terminal_region_certificate.v2"
EQUIVALENCE_SCHEMA = "driveclarify.rq2.g_terminal_region_equivalence.v2"
AUTHORITATIVE_RUNTIME_G = "AUTHORITATIVE_RUNTIME_G"
PLANNER_GRAPH_TERMINAL = "PLANNER_GRAPH_TERMINAL"


def _certificate_payload(
    *,
    region_identity: str,
    semantic_destination_identity: str,
    map_name: str,
    source_opendrive_sha256: str,
    road_id: int,
    section_id: int,
    lane_id: int,
    lane_type: str,
    geometric_relation: str,
    representations: Sequence["TerminalEndpointRepresentation"],
    evidence_method: str,
) -> Mapping[str, Any]:
    return {
        "schema_version": CERTIFICATE_SCHEMA,
        "region_identity": str(region_identity),
        "semantic_destination_identity": str(semantic_destination_identity),
        "map_name": str(map_name),
        "source_opendrive_sha256": str(source_opendrive_sha256),
        "road_id": int(road_id),
        "section_id": int(section_id),
        "lane_id": int(lane_id),
        "lane_type": str(lane_type),
        "geometric_relation": str(geometric_relation),
        "representations": tuple(representations),
        "evidence_method": str(evidence_method),
    }


def _endpoint_digest(
    endpoint_xyz_m: Sequence[float], *, coordinate_domain: str
) -> str:
    endpoint = tuple(float(value) for value in endpoint_xyz_m)
    if len(endpoint) != 3 or not all(math.isfinite(value) for value in endpoint):
        raise ValueError("G_V2_TERMINAL_ENDPOINT_INVALID")
    return canonical_sha256(
        {
            "schema_version": "driveclarify.rq2.global_task.v1",
            "coordinate_domain": str(coordinate_domain),
            "unit": "m",
            "endpoint_xyz_m": endpoint,
        }
    )


@dataclass(frozen=True)
class TerminalEndpointRepresentation:
    role: str
    coordinate_domain: str
    endpoint_xyz_m: tuple[float, float, float]
    endpoint_digest: str

    @classmethod
    def bind(
        cls,
        *,
        role: str,
        coordinate_domain: str,
        endpoint_xyz_m: Sequence[float],
    ) -> "TerminalEndpointRepresentation":
        endpoint = tuple(float(value) for value in endpoint_xyz_m)
        return cls(
            role=str(role),
            coordinate_domain=str(coordinate_domain),
            endpoint_xyz_m=endpoint,
            endpoint_digest=_endpoint_digest(
                endpoint, coordinate_domain=str(coordinate_domain)
            ),
        )

    def __post_init__(self) -> None:
        if self.role not in {AUTHORITATIVE_RUNTIME_G, PLANNER_GRAPH_TERMINAL}:
            raise ValueError("G_V2_TERMINAL_REPRESENTATION_ROLE_INVALID")
        if not self.coordinate_domain:
            raise ValueError("G_V2_TERMINAL_COORDINATE_DOMAIN_INVALID")
        expected = _endpoint_digest(
            self.endpoint_xyz_m, coordinate_domain=self.coordinate_domain
        )
        if self.endpoint_digest != expected:
            raise ValueError("G_V2_TERMINAL_ENDPOINT_DIGEST_INVALID")


@dataclass(frozen=True)
class DestinationTerminalRegion:
    region_identity: str
    semantic_destination_identity: str
    map_name: str
    source_opendrive_sha256: str
    road_id: int
    section_id: int
    lane_id: int
    lane_type: str
    geometric_relation: str
    representations: tuple[TerminalEndpointRepresentation, ...]
    evidence_method: str
    certificate_sha256: str

    @classmethod
    def bind(
        cls,
        *,
        region_identity: str,
        semantic_destination_identity: str,
        map_name: str,
        source_opendrive_sha256: str,
        road_id: int,
        section_id: int,
        lane_id: int,
        lane_type: str,
        geometric_relation: str,
        representations: Sequence[TerminalEndpointRepresentation],
        evidence_method: str,
    ) -> "DestinationTerminalRegion":
        values = _certificate_payload(
            region_identity=region_identity,
            semantic_destination_identity=semantic_destination_identity,
            map_name=map_name,
            source_opendrive_sha256=source_opendrive_sha256,
            road_id=road_id,
            section_id=section_id,
            lane_id=lane_id,
            lane_type=lane_type,
            geometric_relation=geometric_relation,
            representations=representations,
            evidence_method=evidence_method,
        )
        return cls(
            **{key: value for key, value in values.items() if key != "schema_version"},
            certificate_sha256=canonical_sha256(values),
        )

    def __post_init__(self) -> None:
        for value in (
            self.region_identity,
            self.semantic_destination_identity,
            self.map_name,
            self.lane_type,
            self.evidence_method,
        ):
            if not str(value).strip():
                raise ValueError("G_V2_TERMINAL_REGION_IDENTITY_INVALID")
        if len(self.source_opendrive_sha256) != 64:
            raise ValueError("G_V2_OPENDRIVE_HASH_INVALID")
        if self.lane_type != "Driving":
            raise ValueError("G_V2_TERMINAL_REGION_NOT_DRIVING_LANE")
        if self.geometric_relation != "SAME_LANE_FORWARD_CONNECTED":
            raise ValueError("G_V2_TERMINAL_REGION_RELATION_INVALID")
        roles = tuple(item.role for item in self.representations)
        if roles.count(AUTHORITATIVE_RUNTIME_G) != 1:
            raise ValueError("G_V2_AUTHORITATIVE_REPRESENTATION_CARDINALITY_INVALID")
        if roles.count(PLANNER_GRAPH_TERMINAL) != 1 or len(roles) != 2:
            raise ValueError("G_V2_GRAPH_REPRESENTATION_CARDINALITY_INVALID")
        expected = canonical_sha256(_certificate_payload(
            region_identity=self.region_identity,
            semantic_destination_identity=self.semantic_destination_identity,
            map_name=self.map_name,
            source_opendrive_sha256=self.source_opendrive_sha256,
            road_id=self.road_id,
            section_id=self.section_id,
            lane_id=self.lane_id,
            lane_type=self.lane_type,
            geometric_relation=self.geometric_relation,
            representations=self.representations,
            evidence_method=self.evidence_method,
        ))
        if self.certificate_sha256 != expected:
            raise ValueError("G_V2_TERMINAL_REGION_CERTIFICATE_HASH_INVALID")

    def representation(self, role: str) -> TerminalEndpointRepresentation:
        return next(item for item in self.representations if item.role == role)

    def assert_equivalent(
        self, *, global_task: GlobalTask, planner_terminal: RouteRow
    ) -> str:
        if (
            global_task.global_destination_identity
            != self.semantic_destination_identity
        ):
            raise RuntimeError("T_B2_SEMANTIC_DESTINATION_NOT_TERMINAL_REGION_G")
        authoritative = self.representation(AUTHORITATIVE_RUNTIME_G)
        if (
            global_task.coordinate_domain != authoritative.coordinate_domain
            or global_task.endpoint_xyz_m != authoritative.endpoint_xyz_m
            or global_task.destination_endpoint_digest != authoritative.endpoint_digest
        ):
            raise RuntimeError("T_B2_RUNTIME_G_NOT_REGISTERED_TERMINAL_REPRESENTATION")
        graph_terminal = self.representation(PLANNER_GRAPH_TERMINAL)
        planner_digest = _endpoint_digest(
            (planner_terminal.x_m, planner_terminal.y_m, planner_terminal.z_m),
            coordinate_domain=planner_terminal.coordinate_domain,
        )
        if (
            planner_terminal.coordinate_domain != graph_terminal.coordinate_domain
            or (
                planner_terminal.x_m,
                planner_terminal.y_m,
                planner_terminal.z_m,
            )
            != graph_terminal.endpoint_xyz_m
            or planner_digest != graph_terminal.endpoint_digest
        ):
            raise RuntimeError("T_B2_GRAPH_TERMINAL_NOT_REGISTERED_FOR_G")
        return canonical_sha256(
            {
                "schema_version": EQUIVALENCE_SCHEMA,
                "terminal_region_certificate_sha256": self.certificate_sha256,
                "semantic_destination_identity": self.semantic_destination_identity,
                "global_task_identity": global_task.identity,
                "authoritative_endpoint_digest": authoritative.endpoint_digest,
                "planner_terminal_endpoint_digest": graph_terminal.endpoint_digest,
                "geometric_relation": self.geometric_relation,
            }
        )


def load_terminal_region_registry(path: Path) -> Mapping[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != REGISTRY_SCHEMA:
        raise RuntimeError("G_V2_TERMINAL_REGION_REGISTRY_SCHEMA_INVALID")
    if raw.get("distance_acceptance_threshold_m") is not None:
        raise RuntimeError("G_V2_NUMERICAL_DISTANCE_THRESHOLD_FORBIDDEN")
    regions = raw.get("regions")
    if not isinstance(regions, dict) or not regions:
        raise RuntimeError("G_V2_TERMINAL_REGION_REGISTRY_EMPTY")
    return raw


def resolve_terminal_region(
    *,
    registry: Mapping[str, Any],
    global_task: GlobalTask,
    graph_destination: Mapping[str, Any],
    graph_town: str,
    opendrive_path: Path,
) -> DestinationTerminalRegion:
    try:
        raw = registry["regions"][global_task.global_destination_identity]
    except (KeyError, TypeError) as error:
        raise RuntimeError("G_V2_SEMANTIC_DESTINATION_REGION_UNREGISTERED") from error
    if bytes_sha256(opendrive_path.read_bytes()) != raw["source_opendrive_sha256"]:
        raise RuntimeError("G_V2_RUNTIME_OPENDRIVE_HASH_MISMATCH")
    if str(graph_town) != str(raw["map_name"]):
        raise RuntimeError("G_V2_GRAPH_MAP_BINDING_MISMATCH")
    if (
        int(graph_destination["road_id"]) != int(raw["road_id"])
        or int(graph_destination["lane_id"]) != int(raw["lane_id"])
    ):
        raise RuntimeError("G_V2_GRAPH_TERMINAL_TOPOLOGY_BINDING_MISMATCH")
    representations_list = []
    for item in raw["representations"]:
        representation = TerminalEndpointRepresentation.bind(
            role=item["role"],
            coordinate_domain=item["coordinate_domain"],
            endpoint_xyz_m=item["endpoint_xyz_m"],
        )
        if representation.endpoint_digest != item.get("endpoint_digest"):
            raise RuntimeError("G_V2_REGISTRY_ENDPOINT_DIGEST_MISMATCH")
        representations_list.append(representation)
    representations = tuple(representations_list)
    region = DestinationTerminalRegion.bind(
        region_identity=raw["region_identity"],
        semantic_destination_identity=raw["semantic_destination_identity"],
        map_name=raw["map_name"],
        source_opendrive_sha256=raw["source_opendrive_sha256"],
        road_id=raw["road_id"],
        section_id=raw["section_id"],
        lane_id=raw["lane_id"],
        lane_type=raw["lane_type"],
        geometric_relation=raw["geometric_relation"],
        representations=representations,
        evidence_method=raw["evidence_method"],
    )
    if region.certificate_sha256 != raw["certificate_sha256"]:
        raise RuntimeError("G_V2_REGISTRY_CERTIFICATE_HASH_MISMATCH")
    authoritative = region.representation(AUTHORITATIVE_RUNTIME_G)
    if global_task.destination_endpoint_digest != authoritative.endpoint_digest:
        raise RuntimeError("G_V2_RUNTIME_G_ENDPOINT_NOT_REGISTERED")
    graph_terminal = region.representation(PLANNER_GRAPH_TERMINAL)
    graph_xyz = tuple(float(value) for value in graph_destination["xyz"])
    if graph_xyz != graph_terminal.endpoint_xyz_m:
        raise RuntimeError("G_V2_GRAPH_TERMINAL_ENDPOINT_NOT_REGISTERED")
    return region
