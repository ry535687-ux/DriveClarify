"""Masked dual-head objective for Learned M1."""

from typing import Dict, Mapping

import torch
import torch.nn.functional as F


def learned_m1_loss(
    outputs: Mapping[str, torch.Tensor],
    batch: Mapping[str, torch.Tensor],
    lambda_unknown: float = 0.5,
    lambda_repeat: float = 0.1,
) -> Dict[str, torch.Tensor]:
    known_mask = batch["task_known_mask"] & batch["hard_gate"]
    if bool(known_mask.any().item()):
        task_loss = F.cross_entropy(outputs["task_logits"][known_mask], batch["task_target"][known_mask])
    else:
        task_loss = outputs["task_logits"].sum() * 0.0
    unknown_loss = F.cross_entropy(outputs["unknown_logits"], batch["unknown_target"])
    repeat = outputs["repeat_embeddings"]
    repeat_mean = repeat.mean(dim=2, keepdim=True)
    repeat_loss = ((repeat - repeat_mean) ** 2).mean()
    total = task_loss + float(lambda_unknown) * unknown_loss + float(lambda_repeat) * repeat_loss
    return {"total": total, "task": task_loss, "unknown": unknown_loss, "repeat": repeat_loss}
