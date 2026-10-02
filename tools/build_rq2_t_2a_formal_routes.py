#!/usr/bin/env python3
"""Mechanically build the four recovered non-Stage6 RQ2-T route assets."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "driveclarify_rq2_t" / "formal_routes"
ACCEPTED = (
    ROOT
    / "reports"
    / "driveclarify_rq2_t_scene_certification_and_tfixed_calibration_v1"
)
SOURCES = {
    "REF-01": (
        ROOT
        / "reports"
        / "driveclarify_a1_integrated_route_switch_smoke_v1"
        / "candidates"
        / "DC-A1-SMOKE-03-LEFT"
        / "selected_route_b_clear_no_scenarios.xml"
    ),
    "LMK-01": (
        ROOT
        / "driveclarify_paper_mvp_scenarios"
        / "generated"
        / "routes"
        / "dc-runtime-06cccfa4723c09ca09a9bc2b.xml"
    ),
    "ORD-01": (
        ROOT
        / "reports"
        / "driveclarify_a1_integrated_route_switch_smoke_v1"
        / "candidates"
        / "DC-A1-SMOKE-02-RIGHT"
        / "selected_route_b_clear_no_scenarios.xml"
    ),
}
# The scientific episode terminates at the certified boundary (indices 12 and
# 14 respectively).  Retain a generous post-boundary topology tail while
# excluding thousands of unreachable downstream waypoints that caused
# pathological wall compute without adding a scientific observation.
WAYPOINT_LIMITS = {"REF-01": 192, "ORD-01": 192}


def _trim_unreachable_postterminal_tail(route, scene):
    limit = WAYPOINT_LIMITS.get(scene)
    if limit is None:
        return
    waypoints = route.find("waypoints")
    if waypoints is None:
        raise RuntimeError("RQ2_T_ROUTE_WAYPOINT_NODE_MISSING:" + scene)
    points = list(waypoints.findall("position"))
    if len(points) <= limit:
        return
    for point in points[limit:]:
        waypoints.remove(point)


def _add_scenario(route, scene, configuration_sha256):
    old = route.find("scenarios")
    if old is not None:
        route.remove(old)
    scenarios = ET.SubElement(route, "scenarios")
    scenario = ET.SubElement(
        scenarios,
        "scenario",
        {
            "name": "RQ2T-2A-{}-FORMAL".format(scene),
            "type": "DriveClarifyRQ2T2AFormalScenario",
        },
    )
    points = route.findall("./waypoints/position")
    if len(points) < 2:
        raise RuntimeError("RQ2_T_ROUTE_HAS_TOO_FEW_POINTS:" + scene)
    left, right = points[0].attrib, points[1].attrib
    yaw = math.degrees(
        math.atan2(float(right["y"]) - float(left["y"]), float(right["x"]) - float(left["x"]))
    )
    ET.SubElement(
        scenario,
        "trigger_point",
        {
            "x": left["x"],
            "y": left["y"],
            "z": left.get("z", "0"),
            "yaw": "{:.12f}".format(yaw),
        },
    )
    ET.SubElement(
        scenario,
        "rq2_t",
        {
            "scene_key": scene,
            "scenario_configuration_sha256": configuration_sha256,
        },
    )


def _ord02_tree():
    receipt = json.loads(
        (
            ACCEPTED
            / "ENGINEERING_RAW"
            / "ENG-RQ2T-SCENEQUAL-005"
            / "ENGINEERING_SCENE_QUALIFICATION_RECEIPT.json"
        ).read_text(encoding="utf-8")
    )
    trace = receipt["ord02"]["route_trace"]
    if len(trace) != 631:
        raise RuntimeError("RQ2_T_ORD02_CERTIFIED_TRACE_LENGTH_CHANGED")
    root = ET.Element("routes")
    route = ET.SubElement(root, "route", {"id": "RQ2T-ORD02", "town": "Town01"})
    waypoints = ET.SubElement(route, "waypoints")
    for row in trace:
        transform = row["transform"]
        ET.SubElement(
            waypoints,
            "position",
            {
                "x": str(transform["x"]),
                "y": str(transform["y"]),
                "z": str(transform["z"]),
            },
        )
    ET.SubElement(route, "scenarios")
    return ET.ElementTree(root)


def main():
    bindings = json.loads(
        (ROOT / "driveclarify_rq2_t" / "owner_bindings_v1.json").read_text(
            encoding="utf-8"
        )
    )["bindings"]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for scene in ("REF-01", "LMK-01", "ORD-01", "ORD-02"):
        tree = ET.parse(SOURCES[scene]) if scene in SOURCES else _ord02_tree()
        route = tree.getroot().find("route")
        if route is None:
            raise RuntimeError("RQ2_T_ROUTE_NODE_MISSING:" + scene)
        route.set("id", "RQ2T-2A-" + scene)
        _trim_unreachable_postterminal_tail(route, scene)
        _add_scenario(
            route, scene, bindings[scene]["scenario_configuration_sha256"]
        )
        target = OUTPUT / (scene.lower() + ".xml")
        tree.write(target, encoding="utf-8", xml_declaration=True)
        rows.append(
            {
                "scene_key": scene,
                "path": str(target.relative_to(ROOT)),
                "waypoint_count": len(route.findall("./waypoints/position")),
                "scenario_count": len(route.findall("./scenarios/scenario")),
            }
        )
    print(json.dumps(rows, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
