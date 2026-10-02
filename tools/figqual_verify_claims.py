"""逐条核对 PROVENANCE.md / CAPTION.md 里写下的 receipt 断言。

每条打印 期望值 / 实际值 / 是否一致，避免把未核实的说法写进论文溯源。
"""
import json
import os

ROOT = "/home/buaa/wrh/DriveClarify"
SUITE = os.path.join(
    ROOT, "artifacts", "driveclarify_method_v1_visual_behavioral_acceptance_suite_v1"
)
T4 = os.path.join(
    ROOT, "artifacts", "temporal_grounding_v1", "T4_train_bounded_closed_loop"
)


def load(d, name):
    return json.load(open(os.path.join(d, name)))


def show(label, actual, expected):
    ok = "一致" if actual == expected else "不一致"
    print(f"    [{ok}] {label}")
    print(f"           期望 = {json.dumps(expected, ensure_ascii=False)}")
    print(f"           实际 = {json.dumps(actual, ensure_ascii=False)}")


def dig(obj, path):
    cur = obj
    for key in path.split("."):
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return "<缺失>"
    return cur


def main():
    print("=== (a) DCVA1-02-AS-0815")
    a = load(os.path.join(SUITE, "DCVA1-02-AS-0815"),
             "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json")
    show("referent_route_order_authorization.authorization_eligible",
         dig(a, "referent_route_order_authorization.authorization_eligible"),
         False)
    print("           reason 相关键:",
          json.dumps({k: v for k, v in (
              dig(a, "referent_route_order_authorization") or {}
          ).items() if "reason" in k.lower()}, ensure_ascii=False)[:300]
          if isinstance(dig(a, "referent_route_order_authorization"), dict)
          else "<缺失>")
    show("branch_duplicate", a.get("branch_duplicate", "<缺失>"), True)
    show("label_firewall 全零", a.get("label_firewall"),
         {k: 0 for k in (a.get("label_firewall") or {})})

    print("\n=== (c) DCVA1-04-WAIT-0815")
    c = load(os.path.join(SUITE, "DCVA1-04-WAIT-0815"),
             "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json")
    show("authority_revoked_before_stale",
         c.get("authority_revoked_before_stale", "<缺失>"), True)
    show("resolution_requires_latest_fresh_replan",
         c.get("resolution_requires_latest_fresh_replan", "<缺失>"), True)
    show("answer_received_frame", c.get("answer_received_frame", "<缺失>"), 2220)
    print("    查询发出帧 / ACT 帧的可用字段:")
    for k in sorted(c):
        if any(t in k for t in ("frame", "emitted", "query_", "fresh_replan",
                                "post_answer", "source_frame")):
            print(f"       {k} = {json.dumps(c[k], ensure_ascii=False)[:150]}")

    print("\n=== (d) T4")
    d = load(T4, "TEMPORAL_GROUNDING_V1_LIVE_RECEIPT.json")
    fw = d.get("label_firewall") or {}
    show("label_firewall.gold_cleared_timestamp_reads",
         fw.get("gold_cleared_timestamp_reads", "<缺失>"), 0)
    show("label_firewall 全零", fw, {k: 0 for k in fw})

    print("\n=== (f) DCVA1-01-ACT-0815")
    f = load(os.path.join(SUITE, "DCVA1-01-ACT-0815"),
             "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json")
    show("authority_subject", f.get("authority_subject", "<缺失>"),
         "BASELINE_CONTROL")
    show("label_firewall 全零", f.get("label_firewall"),
         {k: 0 for k in (f.get("label_firewall") or {})})

    print("\n=== (b) DCVA1-03-ASK-0815 target_bindings 是否为 null")
    b = load(os.path.join(SUITE, "DCVA1-03-ASK-0815"),
             "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json")
    for case, obj in (("a", a), ("b", b), ("c", c)):
        print(f"    ({case}) target_bindings = "
              f"{json.dumps(obj.get('target_bindings', '<缺失>'), ensure_ascii=False)[:80]}")
        tbr = obj.get("target_binding_receipts") or []
        srcs = sorted({
            r.get("referring_expression_source")
            or r.get("source_provenance")
            or r.get("provenance", "<无>")
            for r in tbr
        })
        print(f"         referring expression provenance: {srcs}")


if __name__ == "__main__":
    main()
