"""Checkpoint save/load and round-trip comparison."""

import hashlib
from pathlib import Path
from typing import Any, Dict, Mapping, Type

import torch
from torch import nn

from .model import LearnedM1


def state_dict_sha256(state_dict: Mapping[str, torch.Tensor]) -> str:
    digest = hashlib.sha256()
    for name in sorted(state_dict):
        tensor = state_dict[name].detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(str(tuple(tensor.shape)).encode("ascii"))
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()


def save_checkpoint(
    path: Path,
    model: nn.Module,
    model_kwargs: Mapping[str, Any],
    training_config: Mapping[str, Any],
    dataset_hash: str,
    feature_hash: str,
    split_hash: str,
) -> Dict[str, Any]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "driveclarify.learned_m1_checkpoint.pilot.v1",
        "model_class": model.__class__.__name__,
        "model_kwargs": dict(model_kwargs),
        "model_state_dict": {name: value.detach().cpu() for name, value in model.state_dict().items()},
        "model_state_sha256": state_dict_sha256(model.state_dict()),
        "training_config": dict(training_config),
        "dataset_hash": dataset_hash,
        "feature_hash": feature_hash,
        "split_hash": split_hash,
    }
    torch.save(payload, str(path))
    return payload


def load_checkpoint(path: Path, device: torch.device = None, model_class: Type[nn.Module] = LearnedM1):
    device = device or torch.device("cpu")
    try:
        payload = torch.load(str(path), map_location=device, weights_only=False)
    except TypeError:
        payload = torch.load(str(path), map_location=device)
    model = model_class(**payload["model_kwargs"]).to(device)
    model.load_state_dict(payload["model_state_dict"], strict=True)
    actual_hash = state_dict_sha256(model.state_dict())
    if actual_hash != payload["model_state_sha256"]:
        raise RuntimeError("CHECKPOINT_STATE_HASH_MISMATCH")
    return model, payload


def compare_logits(first: Mapping[str, torch.Tensor], second: Mapping[str, torch.Tensor]) -> Dict[str, float]:
    return {
        name: float(torch.max(torch.abs(first[name].detach().cpu() - second[name].detach().cpu())).item())
        for name in ("task_logits", "unknown_logits")
    }
