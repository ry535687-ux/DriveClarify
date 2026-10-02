"""Small shared-encoder Learned M1 models."""

from typing import Dict, Mapping

import torch
from torch import nn


def _masked_pool(values: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    mask_f = mask.to(values.dtype).unsqueeze(-1)
    count = mask_f.sum(dim=-2).clamp_min(1.0)
    mean = (values * mask_f).sum(dim=-2) / count
    max_values = values.masked_fill(~mask.unsqueeze(-1), -torch.finfo(values.dtype).max)
    maximum = max_values.max(dim=-2).values
    any_valid = mask.any(dim=-1, keepdim=True)
    maximum = torch.where(any_valid, maximum, torch.zeros_like(maximum))
    return torch.cat([mean, maximum], dim=-1)


class SequenceEncoder(nn.Module):
    """Point-wise MLP followed by masked mean/max pooling."""

    def __init__(self, hidden_dim: int = 64, embedding_dim: int = 32):
        super().__init__()
        self.point_mlp = nn.Sequential(
            nn.Linear(2, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, embedding_dim),
            nn.ReLU(),
        )
        self.pool_projection = nn.Sequential(nn.Linear(embedding_dim * 2, embedding_dim), nn.ReLU())

    def forward(self, sequence: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        encoded = self.point_mlp(sequence)
        return self.pool_projection(_masked_pool(encoded, mask))


class TopologyEncoder(nn.Module):
    """Encode frozen straight/right centerlines and non-label geometry."""

    def __init__(self, hidden_dim: int = 64, embedding_dim: int = 32):
        super().__init__()
        self.branch_encoder = SequenceEncoder(hidden_dim, embedding_dim)
        self.projection = nn.Sequential(
            nn.Linear(embedding_dim * 2 + 8, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, embedding_dim),
            nn.ReLU(),
        )

    def forward(self, topology: torch.Tensor, mask: torch.Tensor, scalars: torch.Tensor) -> torch.Tensor:
        batch_size = topology.shape[0]
        branches = self.branch_encoder(topology.reshape(-1, topology.shape[-2], 2) / 25.0, mask.reshape(-1, mask.shape[-1]))
        branches = branches.reshape(batch_size, 2, -1)
        return self.projection(torch.cat([branches[:, 0], branches[:, 1], scalars / 25.0], dim=-1))


class LearnedM1(nn.Module):
    """V2: shared plan encoders and an A/B-symmetric comparator."""

    def __init__(
        self,
        hidden_dim: int = 64,
        embedding_dim: int = 32,
        evidence_dim: int = 6,
        use_topology: bool = True,
        use_roles: bool = True,
        repeat_aggregation: str = "mean_std_variation",
        comparator_mode: str = "symmetric",
    ):
        super().__init__()
        if repeat_aggregation not in {"mean_std_variation", "repeat_1", "mean_only"}:
            raise ValueError("UNKNOWN_REPEAT_AGGREGATION")
        if comparator_mode not in {"symmetric", "asymmetric"}:
            raise ValueError("UNKNOWN_COMPARATOR_MODE")
        self.hidden_dim = hidden_dim
        self.embedding_dim = embedding_dim
        self.use_topology = bool(use_topology)
        self.use_roles = bool(use_roles)
        self.repeat_aggregation = repeat_aggregation
        self.comparator_mode = comparator_mode
        self.route_encoder = SequenceEncoder(hidden_dim, embedding_dim)
        self.speed_encoder = SequenceEncoder(hidden_dim, embedding_dim)
        self.repeat_projection = nn.Sequential(
            nn.Linear(embedding_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, embedding_dim),
            nn.ReLU(),
        )
        self.candidate_aggregator = nn.Sequential(
            nn.Linear(embedding_dim * 2 + 1 + 2, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        self.topology_encoder = TopologyEncoder(hidden_dim, embedding_dim)
        comparator_dim = hidden_dim * 3 + 2 + embedding_dim + evidence_dim
        self.comparator = nn.Sequential(
            nn.Linear(comparator_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, embedding_dim),
            nn.ReLU(),
        )
        self.task_head = nn.Linear(embedding_dim, 2)
        self.abstention_head = nn.Linear(embedding_dim, 2)

    def forward(self, batch: Mapping[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        route = batch["route"] / 20.0
        speed = batch["speed"] / 12.0
        if self.repeat_aggregation == "repeat_1":
            route = route[:, :, :1]
            speed = speed[:, :, :1]
            route_mask = batch["route_mask"][:, :, :1]
            speed_mask = batch["speed_mask"][:, :, :1]
        else:
            route_mask = batch["route_mask"]
            speed_mask = batch["speed_mask"]
        batch_size = route.shape[0]
        repeat_count = route.shape[2]
        route_embedding = self.route_encoder(route.reshape(-1, 20, 2), route_mask.reshape(-1, 20))
        speed_embedding = self.speed_encoder(speed.reshape(-1, 10, 2), speed_mask.reshape(-1, 10))
        repeat_embedding = self.repeat_projection(torch.cat([route_embedding, speed_embedding], dim=-1))
        repeat_embedding = repeat_embedding.reshape(batch_size, 2, repeat_count, self.embedding_dim)
        repeat_mean = repeat_embedding.mean(dim=2)
        if self.repeat_aggregation in {"repeat_1", "mean_only"}:
            repeat_std = torch.zeros_like(repeat_mean)
            within_variation = repeat_mean.new_zeros((batch_size, 2, 1))
        else:
            repeat_std = repeat_embedding.std(dim=2, unbiased=False)
            within_variation = ((repeat_embedding - repeat_mean.unsqueeze(2)) ** 2).mean(dim=(2, 3), keepdim=False).unsqueeze(-1)
        roles = batch["roles"] if self.use_roles else torch.zeros_like(batch["roles"])
        candidate_input = torch.cat([repeat_mean, repeat_std, within_variation, roles], dim=-1)
        candidate_embedding = self.candidate_aggregator(candidate_input)
        z_a, z_b = candidate_embedding[:, 0], candidate_embedding[:, 1]
        variation_a, variation_b = within_variation[:, 0], within_variation[:, 1]
        if self.use_topology:
            topology_embedding = self.topology_encoder(batch["topology"], batch["topology_mask"], batch["topology_scalars"])
        else:
            topology_embedding = z_a.new_zeros((batch_size, self.embedding_dim))
        if self.comparator_mode == "symmetric":
            comparator_terms = [0.5 * (z_a + z_b), torch.abs(z_a - z_b), z_a * z_b]
        else:
            comparator_terms = [z_a, z_b, z_a - z_b]
        comparator_input = torch.cat(
            [
                *comparator_terms,
                0.5 * (variation_a + variation_b),
                torch.abs(variation_a - variation_b),
                topology_embedding,
                batch["evidence"],
            ],
            dim=-1,
        )
        pair_embedding = self.comparator(comparator_input)
        return {
            "task_logits": self.task_head(pair_embedding),
            "unknown_logits": self.abstention_head(pair_embedding),
            "repeat_embeddings": repeat_embedding,
            "candidate_embeddings": candidate_embedding,
            "pair_embedding": pair_embedding,
        }


class LinearComparator(nn.Module):
    """V1 diagnostic: linear heads over the same allowed raw evidence."""

    def __init__(self):
        super().__init__()
        # Each candidate: route mean/std (80), speed mean/std (40), role (2).
        # Symmetric mean/abs-diff/product: 366. Topology: 100+8. Evidence: 6.
        self.input_dim = 480
        self.task_head = nn.Linear(self.input_dim, 2)
        self.abstention_head = nn.Linear(self.input_dim, 2)

    def _candidate_raw(self, batch: Mapping[str, torch.Tensor]) -> torch.Tensor:
        route = batch["route"] / 20.0
        speed = batch["speed"] / 12.0
        route_mean = route.mean(dim=2).flatten(start_dim=2)
        route_std = route.std(dim=2, unbiased=False).flatten(start_dim=2)
        speed_mean = speed.mean(dim=2).flatten(start_dim=2)
        speed_std = speed.std(dim=2, unbiased=False).flatten(start_dim=2)
        return torch.cat([route_mean, route_std, speed_mean, speed_std, batch["roles"]], dim=-1)

    def forward(self, batch: Mapping[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        candidate = self._candidate_raw(batch)
        a, b = candidate[:, 0], candidate[:, 1]
        topology = (batch["topology"] / 25.0).flatten(start_dim=1)
        inputs = torch.cat(
            [0.5 * (a + b), torch.abs(a - b), a * b, topology, batch["topology_scalars"] / 25.0, batch["evidence"]],
            dim=-1,
        )
        if inputs.shape[-1] != self.input_dim:
            raise RuntimeError("LINEAR_COMPARATOR_INPUT_DIM_MISMATCH")
        empty_repeat = inputs.new_zeros((inputs.shape[0], 2, 3, 1))
        return {
            "task_logits": self.task_head(inputs),
            "unknown_logits": self.abstention_head(inputs),
            "repeat_embeddings": empty_repeat,
            "pair_embedding": inputs,
        }


def trainable_parameter_count(model: nn.Module) -> int:
    return sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
