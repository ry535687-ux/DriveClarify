import importlib.util
import hashlib
import json

from driveclarify.paths import RESOURCES


def test_inert_backend_hooks_preserve_model_and_plan_objects():
    path = RESOURCES / "upstream_extra/team_code/driveclarify_probe_hook.py"
    spec = importlib.util.spec_from_file_location("identity_hook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hook = module.build_runtime_probe()
    model_input, route, speed, target_a, target_b = (object() for _ in range(5))
    assert hook.enabled is False
    assert hook.prepare_model_input(model_input) is model_input
    assert hook.select_plan_source(route, speed) == (route, speed)
    assert hook.select_connector_target_window(route, object(), (target_a, target_b)) == (target_a, target_b)
    assert hook.on_tick(object()) is None
    assert hook.should_end_scientific_horizon(1e6) is False


def test_all_220_routes_match_stored_bytes_and_unique_pair_order():
    routes = json.loads((RESOURCES / "routes.json").read_text())["pair_order"]
    assert len(routes) == len({r["route_id"] for r in routes}) == 220
    assert [r["canonical_index"] for r in routes] == list(range(220))
    for route in routes:
        assert hashlib.sha256(route["xml"].encode()).hexdigest() == route["route_sha256"]
        assert sorted(route["arm_order"]) == ["A0", "A1"]

