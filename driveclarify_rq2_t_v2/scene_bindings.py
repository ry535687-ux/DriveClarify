"""Prospectively frozen engineering-only scene bindings (never runtime evidence input)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


SCENE_BINDINGS: dict[str, dict[str, Any]] = {
    "ENG-V2-REF-REVEAL": {
        "phase": "A", "family": "REFERENTIAL", "town": "Town12",
        "base_route": "driveclarify_rq2_t/formal_routes/ref-01.xml",
        "instruction": "Turn after the white car.", "reveal_after_simulation_s": 1.0,
        "actors": [
            {"blueprint": "vehicle.audi.a2", "color": "255,255,255", "initial": [18.8954,4512.1411,372.7745,179.8666]},
            {"blueprint": "vehicle.lincoln.mkz_2020", "color": "255,255,255", "initial": [33.1850,4536.2354,372.6740,89.9204], "revealed": [33.1850,4533.2354,372.6740,89.9204]},
        ],
        "reveal_contract": "SECOND_WHITE_VEHICLE_MOVES_3M_INTO_DISTINCT_RUNTIME_CAMERA_POSITION",
    },
    "ENG-V2-LMK-REVEAL": {
        "phase": "A", "family": "LANDMARK", "town": "Town10HD",
        "base_route": "driveclarify_rq2_t/formal_routes/lmk-01.xml",
        "instruction": "Use the opening after the kiosk.", "reveal_after_simulation_s": 1.0,
        "actors": [
            {"blueprint": "static.prop.kiosk_01", "color": None, "initial": [95.8803,71.2211,0.20,90.3907]},
            {"blueprint": "static.prop.kiosk_01", "color": None, "initial": [82.9305,66.3546,0.20,179.9267], "revealed": [82.9305,63.3546,0.20,179.9267]},
        ],
        "reveal_contract": "SECOND_KIOSK_MOVES_3M_TO_DISTINCT_CENTROID_AFTER_TRIGGER",
    },
    "ENG-V2-ORD-REVEAL": {
        "phase": "A", "family": "ORDER", "town": "Town12",
        "base_route": "driveclarify_rq2_t/formal_routes/ord-01.xml",
        "instruction": "Take the second right turn.", "reveal_after_simulation_s": None,
        "actors": [],
        "reveal_contract": "SECOND_ROUTE_JUNCTION_ENTERS_100M_ACTIVE_ROUTE_PREFIX_BY_EGO_PROGRESS",
    },
    "ENG-V2-E7-INVALIDATION": {
        "phase": "A", "family": "REFERENTIAL", "town": "Town12",
        "base_route": "driveclarify_rq2_t/formal_routes/ref-01.xml",
        "instruction": "Turn after the white car.", "reveal_after_simulation_s": 1.0,
        "actors": [
            {"blueprint": "vehicle.audi.a2", "color": "255,255,255", "initial": [18.8954,4512.1411,372.7745,179.8666]},
            {"blueprint": "vehicle.lincoln.mkz_2020", "color": "255,255,255", "initial": [33.1850,4533.2354,372.6740,89.9204]},
            {"blueprint": "vehicle.tesla.model3", "color": "0,0,0", "initial": [47.4,4504.0,372.2,90.0], "revealed": [47.4,4512.1,372.2,90.0]},
        ],
        "reveal_contract": "CROSS_TRAFFIC_ACTOR_ENTERS_APPROACH_AND_INVALIDATES_CURRENT_SAFETY_STATE",
    },
    "ENG-V2-ASYNC-MEMORY": {
        "phase": "A", "family": "REFERENTIAL", "town": "Town12",
        "base_route": "driveclarify_rq2_t/formal_routes/ref-01.xml",
        "instruction": "Turn after the white car.", "reveal_after_simulation_s": 0.5,
        "actors": [
            {"blueprint": "vehicle.audi.a2", "color": "255,255,255", "initial": [18.8954,4512.1411,372.7745,179.8666]},
            {"blueprint": "vehicle.lincoln.mkz_2020", "color": "255,255,255", "initial": [33.1850,4536.7354,372.6740,89.9204], "revealed": [33.1850,4533.2354,372.6740,89.9204]},
        ],
        "reveal_contract": "E2_CUE_PRECEDES_LATER_ROUTE_AND_HOLDING_EVIDENCE_WITHIN_FROZEN_TTLS",
    },
    "ENG-V2-NONREVEAL": {
        "phase": "A", "family": "REFERENTIAL", "town": "Town12",
        "base_route": "driveclarify_rq2_t/formal_routes/ref-01.xml",
        "instruction": "Turn after the white car.", "reveal_after_simulation_s": None,
        "actors": [
            {"blueprint": "vehicle.audi.a2", "color": "255,255,255", "initial": [18.8954,4512.1411,372.7745,179.8666]},
            {"blueprint": "vehicle.lincoln.mkz_2020", "color": "255,255,255", "initial": [18.8954,4514.1411,372.7745,179.8666]},
        ],
        "reveal_contract": "NO_DISCRIMINATING_CUE_BEFORE_ENGINEERING_TERMINAL",
    },
    "ENG-V2-USC-NEGATIVE-CONTROL": {
        "phase": "A", "family": "UNDERSPECIFIED_CONSTRAINT", "town": "Town06",
        "base_route": "driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-7091eedbf681e076baca527f.xml",
        "instruction": "Pull over when convenient.", "reveal_after_simulation_s": None,
        "actors": [
            {"blueprint": "static.prop.kiosk_01", "color": None, "initial": [-7.9101,140.5796,0.05,86.9850]},
            {"blueprint": "static.prop.kiosk_01", "color": None, "initial": [-17.8491,138.9720,0.05,172.2397]},
        ],
        "reveal_contract": "ENVIRONMENT_CANNOT_SUPPLY_MISSING_PASSENGER_SIDE_OR_TARGET_INTENT",
    },
    "BLIND-V2-REF": {
        "phase": "B", "family": "REFERENTIAL", "town": "Town03",
        "base_route": "driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-0bd82fb9b9bda9e4ca49040e.xml",
        "instruction": "Turn after the white van.", "reveal_after_simulation_s": 1.0,
        "actors": [
            {"blueprint": "vehicle.mercedes.sprinter", "color": "255,255,255", "initial": [149.5334,-190.7814,0.35,-11.9649]},
            {"blueprint": "vehicle.volkswagen.t2", "color": "255,255,255", "initial": [151.0941,-171.1510,2.7547,90.9965], "revealed": [151.0941,-167.6510,2.7547,90.9965]},
        ],
        "reveal_contract": "HELD_OUT_TOWN03_SECOND_VAN_MOVES_3_5M_TO_DISTINCT_CAMERA_POSITION",
    },
    "BLIND-V2-LMK": {
        "phase": "B", "family": "LANDMARK", "town": "Town02",
        "base_route": "driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-ed0e96eb054a43cd26e73b95.xml",
        "instruction": "Use the second opening after the bus stop.", "reveal_after_simulation_s": 1.0,
        "actors": [
            {"blueprint": "static.prop.kiosk_01", "color": None, "initial": [45.8546,242.8998,0.05,-90.0050]},
            {"blueprint": "static.prop.kiosk_01", "color": None, "initial": [55.8592,244.3775,0.05,0.0348], "revealed": [55.8592,240.8775,0.05,0.0348]},
        ],
        "reveal_contract": "HELD_OUT_TOWN02_SECOND_MARKER_MOVES_3_5M_TO_DISTINCT_CENTROID",
    },
    "BLIND-V2-ORD": {
        "phase": "B", "family": "ORDER", "town": "Town12",
        "base_route": "driveclarify_rq2_t/formal_routes/ord-02.xml",
        "instruction": "Take the second right turn.", "reveal_after_simulation_s": None,
        "actors": [],
        "reveal_contract": "HELD_OUT_TOWN12_ROUTE_SEGMENT_SECOND_JUNCTION_ENTERS_100M_PREFIX",
    },
    "BLIND-V2-USC": {
        "phase": "B", "family": "UNDERSPECIFIED_CONSTRAINT", "town": "Town10HD",
        "base_route": "driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-06cccfa4723c09ca09a9bc2b.xml",
        "instruction": "Choose the branch beyond the plaza.", "reveal_after_simulation_s": None,
        "actors": [
            {"blueprint": "static.prop.kiosk_01", "color": None, "initial": [98.9143,66.2212,0.05,90.3907]},
            {"blueprint": "static.prop.kiosk_01", "color": None, "initial": [82.9305,66.3546,0.05,-179.9267]},
        ],
        "reveal_contract": "HELD_OUT_INTRINSIC_LANGUAGE_CONSTRAINT_REMAINS_UNOBSERVABLE",
    },
}


def frozen_binding(scene_config_id: str) -> Mapping[str, Any]:
    value = SCENE_BINDINGS[scene_config_id]
    return {**value, "scene_config_id": scene_config_id, "scene_configuration_sha256": canonical_sha256(value)}


__all__ = ["SCENE_BINDINGS", "canonical_sha256", "frozen_binding"]
