"""Deterministic full-unit-batch training utilities."""

import os
import random
from dataclasses import replace
from typing import Any, Dict, Mapping, Sequence, Tuple, Type

import torch
from torch import nn

from .checkpoint import state_dict_sha256
from .config import PilotConfig
from .evaluate import decisions, training_accuracy
from .features import tensorize_samples
from .losses import learned_m1_loss
from .model import LearnedM1


def set_determinism(seed: int, deterministic_algorithms: bool = True) -> None:
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if deterministic_algorithms:
        torch.use_deterministic_algorithms(True)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True


def _loss_values(losses: Mapping[str, torch.Tensor]) -> Dict[str, float]:
    return {name: float(value.detach().cpu().item()) for name, value in losses.items()}


def train_model(
    samples: Sequence[Mapping[str, Any]],
    config: PilotConfig,
    device: torch.device,
    model_class: Type[nn.Module] = LearnedM1,
    model_kwargs: Mapping[str, Any] = None,
) -> Tuple[nn.Module, Dict[str, Any]]:
    set_determinism(config.seed, config.deterministic_algorithms)
    kwargs = dict(
        {"hidden_dim": config.hidden_dim, "embedding_dim": config.embedding_dim}
        if model_kwargs is None
        else model_kwargs
    )
    model = model_class(**kwargs).to(device)
    batch = tensorize_samples(samples, device)
    initial_state_hash = state_dict_sha256(model.state_dict())
    model.eval()
    with torch.no_grad():
        initial_outputs = model(batch)
        initial_losses = learned_m1_loss(initial_outputs, batch, config.lambda_unknown, config.lambda_repeat)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    curve = [{"step": 0, **_loss_values(initial_losses)}]
    stopped_early = False
    model.train()
    for step in range(1, config.max_steps + 1):
        optimizer.zero_grad(set_to_none=True)
        outputs = model(batch)
        losses = learned_m1_loss(outputs, batch, config.lambda_unknown, config.lambda_repeat)
        losses["total"].backward()
        gradient_norm = float(torch.nn.utils.clip_grad_norm_(model.parameters(), config.gradient_clip_norm).detach().cpu().item())
        optimizer.step()
        if step == 1 or step % 100 == 0 or step == config.max_steps:
            curve.append({"step": step, **_loss_values(losses), "gradient_norm_before_clip": gradient_norm})
        if config.early_stop and (step % 25 == 0 or step == config.max_steps):
            model.eval()
            with torch.no_grad():
                check_outputs = model(batch)
                check_losses = learned_m1_loss(check_outputs, batch, config.lambda_unknown, config.lambda_repeat)
                accuracy = training_accuracy(check_outputs, batch)
            model.train()
            if (
                float(check_losses["total"].item()) <= config.early_stop_loss
                and accuracy["known_task_correct"] == accuracy["known_task_total"]
                and accuracy["abstention_correct"] == accuracy["abstention_total"]
            ):
                stopped_early = True
                break
    final_step = step
    model.eval()
    with torch.no_grad():
        final_outputs = model(batch)
        final_losses = learned_m1_loss(final_outputs, batch, config.lambda_unknown, config.lambda_repeat)
    initial_total = float(initial_losses["total"].item())
    final_total = float(final_losses["total"].item())
    result = {
        "seed": config.seed,
        "device": str(device),
        "model_class": model.__class__.__name__,
        "model_kwargs": kwargs,
        "initial_state_sha256": initial_state_hash,
        "final_state_sha256": state_dict_sha256(model.state_dict()),
        "initial_losses": _loss_values(initial_losses),
        "final_losses": _loss_values(final_losses),
        "loss_reduction_fraction": (initial_total - final_total) / initial_total if initial_total else 0.0,
        "steps_completed": final_step,
        "stopped_early": stopped_early,
        "accuracy": training_accuracy(final_outputs, batch),
        "predictions": list(decisions(final_outputs, batch)),
        "loss_curve": curve,
        "forward_backward": "PASS",
    }
    return model, result


def config_with_seed(config: PilotConfig, seed: int) -> PilotConfig:
    return replace(config, seed=seed)
