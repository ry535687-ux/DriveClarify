#!/usr/bin/env python3
"""Materialize hash-bound engineering route XML from prospectively chosen bases."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t_v2.scene_bindings import SCENE_BINDINGS, frozen_binding


OUTPUT = ROOT / "driveclarify_rq2_t_v2/engineering_routes"


def materialize(scene_config_id: str) -> Path:
    binding = frozen_binding(scene_config_id)
    source = ROOT / str(binding["base_route"])
    tree = ET.parse(source)
    root = tree.getroot()
    route = root.find("route")
    if route is None:
        raise RuntimeError("RQ2_T_V2_BASE_ROUTE_MISSING")
    route.set("id", "RQ2TV2-" + scene_config_id)
    scenarios = route.find("scenarios")
    if scenarios is None:
        scenarios = ET.SubElement(route, "scenarios")
    for child in list(scenarios):
        scenarios.remove(child)
    old_tree = ET.parse(source)
    old_route = old_tree.getroot().find("route")
    old_scenario = None if old_route is None else old_route.find("scenarios/scenario")
    old_trigger = None if old_scenario is None else old_scenario.find("trigger_point")
    scenario = ET.SubElement(scenarios, "scenario", {
        "name": "RQ2TV2-" + scene_config_id,
        "type": "DriveClarifyRQ2TV2EngineeringScenario",
    })
    if old_trigger is None:
        first = route.find("waypoints/position")
        if first is None:
            raise RuntimeError("RQ2_T_V2_BASE_TRIGGER_UNAVAILABLE")
        ET.SubElement(scenario, "trigger_point", {
            "x": first.get("x", "0"), "y": first.get("y", "0"), "z": first.get("z", "0"), "yaw": "0",
        })
    else:
        ET.SubElement(scenario, "trigger_point", dict(old_trigger.attrib))
    ET.SubElement(scenario, "rq2_t_v2", {
        "scene_config_id": scene_config_id,
        "scene_configuration_sha256": str(binding["scene_configuration_sha256"]),
    })
    ET.indent(tree, space="   ")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / (scene_config_id.lower() + ".xml")
    temporary = target.with_name("." + target.name + ".tmp")
    tree.write(temporary, encoding="utf-8", xml_declaration=True)
    temporary.replace(target)
    return target


def main() -> int:
    rows = []
    for scene_config_id in SCENE_BINDINGS:
        path = materialize(scene_config_id)
        rows.append({"scene_config_id": scene_config_id, "route_path": str(path.relative_to(ROOT))})
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
