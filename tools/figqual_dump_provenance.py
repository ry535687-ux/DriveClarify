"""导出六面板定性图所需的溯源信息（场景身份、检查点、CARLA 版本、决策字段）。"""
import glob
import json
import os

ROOT = "/home/buaa/wrh/DriveClarify"
SUITE = os.path.join(
    ROOT, "artifacts", "driveclarify_method_v1_visual_behavioral_acceptance_suite_v1"
)
T4 = os.path.join(ROOT, "artifacts", "temporal_grounding_v1", "T4_train_bounded_closed_loop")

CASES = [
    ("a", os.path.join(SUITE, "DCVA1-02-AS-0815")),
    ("b", os.path.join(SUITE, "DCVA1-03-ASK-0815")),
    ("c", os.path.join(SUITE, "DCVA1-04-WAIT-0815")),
    ("d", T4),
    ("e", os.path.join(SUITE, "DCVA1-06-TL-0815")),
    ("f", os.path.join(SUITE, "DCVA1-01-ACT-0815")),
]


def first(pattern):
    hits = sorted(glob.glob(pattern))
    return hits[0] if hits else None


def main():
    for letter, d in CASES:
        lc = first(os.path.join(d, "*_LAUNCH_CONTRACT.json"))
        lr = first(os.path.join(d, "*_LIVE_RECEIPT.json"))
        nr = first(os.path.join(d, "*_NATIVE_RUN_RECEIPT.json"))
        pf = first(os.path.join(d, "*_NATIVE_PREFLIGHT.json"))
        c = json.load(open(lc)) if lc else {}
        j = json.load(open(lr)) if lr else {}
        ep = c.get("episode", {}) or {}
        print(f"===== ({letter})  {os.path.basename(d)}")
        for k in (
            "episode_id",
            "scenario_id",
            "route_id",
            "seed",
            "town",
            "split",
            "raw_instruction",
            "information_expected",
        ):
            if k in ep:
                print(f"    {k} = {json.dumps(ep[k], ensure_ascii=False)}")
        print(f"    control_mode = {c.get('control_mode')}")
        # 检查点与端口从启动命令里取
        cmd = c.get("command") or []
        for tok in cmd:
            if "agent-config" in tok or "--port" in tok or "traffic-manager-seed" in tok:
                print(f"    cmd: {tok}")
        # 版本类信息
        for src, tag in ((j, "live"), (json.load(open(pf)) if pf else {}, "preflight"),
                         (json.load(open(nr)) if nr else {}, "native_run")):
            for k in sorted(src):
                if any(t in k.lower() for t in ("carla_version", "checkpoint", "commit",
                                                "git_sha", "head")):
                    print(f"    [{tag}] {k} = {json.dumps(src[k], ensure_ascii=False)[:120]}")
        for k in ("decision_label", "decision_reason", "decision_why", "initial_decision",
                  "status", "ambiguity_type", "effective_k", "raw_k",
                  "pre_event_decision", "post_event_decision", "plausibility_decision",
                  "label_firewall"):
            if k in j:
                print(f"    {k} = {json.dumps(j[k], ensure_ascii=False)[:160]}")
        print()


if __name__ == "__main__":
    main()
