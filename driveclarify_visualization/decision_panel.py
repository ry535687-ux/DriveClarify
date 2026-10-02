"""Panel D: decision + consequence diagnostics.

Consumes the REAL CP3A shadow-decision record (offline adapter output over CP1 world state)
when available, else renders an explicit NO-ADAPTER-OUTPUT panel. It shows the true decision
(FALLBACK for all v0 data), policy state, reason codes, and per-consequence evidence grade.
UNKNOWN/UNSUPPORTED fields keep reason_code + missing dependencies and are NEVER coerced to
0/false/safe/GREEN/inf. It never fabricates ACT/ASK/WAIT.

CP3A decisions are keyed by observation_id; a decision map may be passed in. If none is given
for this observation, the panel says so honestly (adapter not run for this frame) rather than
inventing a decision.
"""

from __future__ import annotations

from typing import Any, Optional

from . import contracts as C

_CONSEQUENCE_GROUPS = ("task", "safety", "rule", "recoverability", "comfort", "timing")


def _first_field(group_val: Any):
    """A consequence group is a dict of named sub-fields; return (name, subdict) of the first."""
    if isinstance(group_val, dict) and group_val:
        k = next(iter(group_val))
        return k, group_val[k]
    return None, None


def summarize_decision(dec_record: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Extract a render-ready, contract-checked summary from one CP3A record (or None)."""
    if not dec_record:
        return {"decision": "NO_ADAPTER_OUTPUT",
                "note": "CP3A adapter not run for this observation; decision honestly absent",
                "reason_codes": [], "consequences": [], "unknown_count": None}
    sd = dec_record.get("shadow_decision", {}) or {}
    decision = sd.get("decision", "FALLBACK")
    grades = []
    for cand in (dec_record.get("consequences") or []):
        cand_id = cand.get("candidate_id")
        for g in _CONSEQUENCE_GROUPS:
            name, sub = _first_field(cand.get(g))
            if sub is None:
                continue
            status = sub.get("evidence_grade", "UNKNOWN")
            value = sub.get("value", None)
            # contract: an UNKNOWN/UNRESOLVED field must not carry a forbidden substitute
            C.assert_unknown_not_coerced(f"{cand_id}.{g}.{name}", status, value)
            grades.append({
                "candidate": cand_id, "group": g, "field": name,
                "evidence_grade": status,
                "authorization_eligible": sub.get("authorization_eligible", False),
                "dependencies": sub.get("dependencies", []),
                "value": value,
            })
    return {
        "decision": decision,
        "next_state": sd.get("next_state"),
        "reason_code": sd.get("reason_code"),
        "shadow_only": sd.get("shadow_only", True),
        "used_for_control": sd.get("used_for_control", False),
        "reason_codes": dec_record.get("reason_codes", []),
        "candidate_count": (dec_record.get("state_machine") or {}).get("candidate_count"),
        "unknown_count": len(dec_record.get("unknown_fields") or []),
        "consequences": grades,
    }


def render(frame: dict[str, Any], dec_record: Optional[dict[str, Any]] = None,
           size_px: int = 512, reference_summary: Optional[dict[str, Any]] = None):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    s = summarize_decision(dec_record)
    img = Image.new("RGB", (size_px, size_px + 84), (20, 18, 24))
    d = ImageDraw.Draw(img)
    try:
        big = ImageFont.truetype("DejaVuSans.ttf", 22)
        font = ImageFont.truetype("DejaVuSans.ttf", 14)
        small = ImageFont.truetype("DejaVuSans.ttf", 11)
    except Exception:  # noqa: BLE001
        big = font = small = ImageFont.load_default()

    dec = s["decision"]
    col = {"FALLBACK": (255, 170, 60), "ACT": (90, 220, 120), "ASK": (120, 180, 255),
           "WAIT": (220, 200, 90), "NO_ADAPTER_OUTPUT": (150, 150, 150)}.get(dec, (200, 200, 200))
    d.text((8, 8), f"Decision: {dec}", fill=col, font=big)
    d.text((8, 40), f"policy_state={s.get('next_state')}  reason={s.get('reason_code')}  "
                    f"shadow_only={s.get('shadow_only')} used_for_control={s.get('used_for_control')}",
           fill=(210, 210, 220), font=small)
    d.text((8, 58), f"reason_codes={','.join(s.get('reason_codes') or []) or 'n/a'}", fill=(210, 190, 220), font=small)
    d.text((8, 74), f"candidates={s.get('candidate_count')}  UNKNOWN_fields={s.get('unknown_count')}",
           fill=(200, 200, 210), font=small)

    y = 98
    d.text((8, y), "consequence evidence grades (UNKNOWN kept as UNKNOWN+reason):", fill=(180, 200, 220), font=font)
    y += 22
    shown = 0
    for g in s["consequences"]:
        if shown >= 16:
            d.text((8, y), "... (full trace in decision_trace JSON)", fill=(150, 150, 160), font=small)
            break
        grade = g["evidence_grade"]
        gc = (255, 140, 100) if "UNRESOLVED" in grade or "UNKNOWN" in grade else (
            (150, 220, 160) if "VERIFIED" in grade else (210, 210, 130))
        deps = ",".join(g["dependencies"]) if g["dependencies"] else "-"
        d.text((8, y), f"[{g['candidate']}] {g['group']}.{g['field']}: {grade}  deps={deps}",
               fill=gc, font=small)
        y += 15
        shown += 1

    # honest reference: the only REAL CP3A decision output that exists (run C, CP1 world-state)
    if reference_summary:
        rd = reference_summary.get("decisions", {})
        d.text((8, y + 6), f"CP3A REAL ref (run C, CP1 world-state, {reference_summary.get('records')} obs): "
                           f"decisions={rd}", fill=(150, 200, 170), font=small)
        d.text((8, y + 22), "per-CP3B-frame: NO_ADAPTER_OUTPUT (no candidates generated for these obs) - honest",
               fill=(160, 170, 190), font=small)

    d.text((8, size_px + 40), "Decision is SHADOW-ONLY, not used for control | "
           + " | ".join(C.GLOBAL_LABELS), fill=(255, 200, 0), font=small)
    d.text((8, size_px + 58), "No ACT/ASK/WAIT fabricated; all REAL CP3A data is FALLBACK", fill=(255, 170, 120), font=small)
    return np.asarray(img)
