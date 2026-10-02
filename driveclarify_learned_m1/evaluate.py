"""Decision and diagnostic evaluation helpers."""

from typing import Any, Dict, Mapping, Sequence

import torch


TASK_NAMES = ("TASK_EQUIVALENT", "TASK_CRITICAL")
UNKNOWN_NAMES = ("KNOWN", "UNKNOWN")


def decisions(outputs: Mapping[str, torch.Tensor], batch: Mapping[str, torch.Tensor]) -> Sequence[Dict[str, Any]]:
    task_prob = torch.softmax(outputs["task_logits"], dim=-1)
    unknown_prob = torch.softmax(outputs["unknown_logits"], dim=-1)
    task_index = task_prob.argmax(dim=-1)
    unknown_index = unknown_prob.argmax(dim=-1)
    result = []
    for i in range(task_index.shape[0]):
        hard_gate = bool(batch["hard_gate"][i].item())
        learned_unknown = int(unknown_index[i].item()) == 1
        if not hard_gate:
            final = "UNKNOWN"
            source = "HARD_EVIDENCE_GATE"
        elif learned_unknown:
            final = "UNKNOWN"
            source = "LEARNED_ABSTENTION_HEAD"
        else:
            final = TASK_NAMES[int(task_index[i].item())]
            source = "TASK_HEAD"
        result.append(
            {
                "final_decision": final,
                "decision_source": source,
                "hard_evidence_gate": hard_gate,
                "task_prediction": TASK_NAMES[int(task_index[i].item())],
                "task_confidence": float(task_prob[i, task_index[i]].item()),
                "abstention_prediction": UNKNOWN_NAMES[int(unknown_index[i].item())],
                "abstention_confidence": float(unknown_prob[i, unknown_index[i]].item()),
                "task_logits": [float(v) for v in outputs["task_logits"][i].detach().cpu().tolist()],
                "unknown_logits": [float(v) for v in outputs["unknown_logits"][i].detach().cpu().tolist()],
            }
        )
    return result


def training_accuracy(outputs: Mapping[str, torch.Tensor], batch: Mapping[str, torch.Tensor]) -> Dict[str, Any]:
    task_pred = outputs["task_logits"].argmax(dim=-1)
    unknown_pred = outputs["unknown_logits"].argmax(dim=-1)
    known_mask = batch["task_known_mask"]
    task_correct = int((task_pred[known_mask] == batch["task_target"][known_mask]).sum().item())
    task_total = int(known_mask.sum().item())
    unknown_correct = int((unknown_pred == batch["unknown_target"]).sum().item())
    return {
        "known_task_correct": task_correct,
        "known_task_total": task_total,
        "abstention_correct": unknown_correct,
        "abstention_total": int(unknown_pred.numel()),
    }
