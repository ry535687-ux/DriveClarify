"""排查所有含 rgb_sequence 的运行目录，输出其机制标签，用于挑选定性图面板。"""
import glob
import json
import os

ROOT = "/home/buaa/wrh/DriveClarify"

KEYS = (
    "decision_label",
    "decision_reason",
    "initial_decision",
    "post_event_decision",
    "pre_event_decision",
    "post_answer_decision",
    "current_persistent_decision",
    "status",
    "ambiguity_state",
    "ambiguity_type",
    "answer",
    "answer_delay_simulation_seconds",
    "answer_received_frame",
    "wait_entry_frame",
    "wait_exit_frame",
    "fresh_planning_frame",
    "invalidated_at_frame",
    "track_id",
    "track_loss_count",
    "track_id_switch_count",
    "raw_instruction",
    "authorization_status",
)


def load_receipt(d):
    for pat in ("*_LIVE_RECEIPT.json",):
        for f in sorted(glob.glob(os.path.join(d, pat))):
            try:
                return f, json.load(open(f))
            except Exception:
                pass
    return None, None


def main():
    seq_dirs = sorted(
        glob.glob(os.path.join(ROOT, "artifacts", "**", "rgb_sequence"), recursive=True)
    )
    print(f"共 {len(seq_dirs)} 个 rgb_sequence 目录\n")
    for sd in seq_dirs:
        parent = os.path.dirname(sd)
        n = len(glob.glob(os.path.join(sd, "*.png")))
        f, j = load_receipt(parent)
        rel = os.path.relpath(parent, ROOT)
        print(f"=== {rel}  (frames={n})")
        if j is None:
            # 往上一级找 receipt
            f, j = load_receipt(os.path.dirname(parent))
        if j is None:
            print("    无 LIVE_RECEIPT")
            continue
        for k in KEYS:
            if k in j:
                print(f"    {k} = {json.dumps(j[k], ensure_ascii=False)[:110]}")
        print()


if __name__ == "__main__":
    main()
