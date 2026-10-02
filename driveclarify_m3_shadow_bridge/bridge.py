"""Stateless in-memory bridge from replay episodes to shadow traces."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from driveclarify_m3_offline_replay.adapters import (
    ADAPTER_ID, ADAPTER_VERSION, adapt_record,
)
from driveclarify_m3_offline_replay.contracts import (
    EpisodeRejection, ReplayEpisode, ReplayTrace,
)
from driveclarify_m3_offline_replay.runner import run_episode as replay_run_episode

from .contracts import (
    ShadowBridgeRejection,
    ShadowInputEnvelope,
    ShadowTrace,
)

_DATACLASS_SLOT_KWARGS = {"slots": True} if sys.version_info >= (3, 10) else {}


def _safe_episode_id(value: Any) -> str | None:
    episode_id = getattr(value, "episode_id", None)
    return episode_id if type(episode_id) is str else None


def _detail_code(value: Any) -> str:
    text = str(value)
    return text if text else type(value).__name__


@dataclass(frozen=True, **_DATACLASS_SLOT_KWARGS)
class M3ShadowBridge:
    """Pure adapter with no instance state and no external side effects."""

    def run_episode(self, value: ReplayEpisode | ShadowInputEnvelope
                    ) -> ShadowTrace | ShadowBridgeRejection:
        envelope: ShadowInputEnvelope | None = None
        try:
            if isinstance(value, ShadowInputEnvelope):
                envelope = value
            elif isinstance(value, ReplayEpisode):
                envelope = ShadowInputEnvelope.create(value)
            else:
                return ShadowBridgeRejection.create(
                    envelope_id=None, episode_id=_safe_episode_id(value),
                    category="INVALID_SHADOW_INPUT",
                    detail_code="REPLAY_EPISODE_OR_SHADOW_ENVELOPE_REQUIRED",
                )
            # A call-local registry prevents bridge behavior from depending on
            # the replay package's mutable default registry.
            replay_result = replay_run_episode(
                envelope.episode,
                adapter_registry={(ADAPTER_ID, ADAPTER_VERSION): adapt_record},
            )
            if isinstance(replay_result, EpisodeRejection):
                return ShadowBridgeRejection.create(
                    envelope_id=envelope.envelope_id,
                    episode_id=replay_result.episode_id,
                    category="REPLAY_EPISODE_REJECTED",
                    detail_code=_detail_code(replay_result.detail),
                    reducer_call_count=replay_result.reducer_call_count,
                )
            if not isinstance(replay_result, ReplayTrace):
                return ShadowBridgeRejection.create(
                    envelope_id=envelope.envelope_id,
                    episode_id=envelope.episode.episode_id,
                    category="UNEXPECTED_REPLAY_RESULT",
                    detail_code=type(replay_result).__name__,
                )
            return ShadowTrace.from_replay_trace(envelope, replay_result)
        except Exception as exc:
            return ShadowBridgeRejection.create(
                envelope_id=(None if envelope is None else envelope.envelope_id),
                episode_id=(_safe_episode_id(value) if envelope is None else
                            envelope.episode.episode_id),
                category="SHADOW_BRIDGE_VALIDATION_REJECTED",
                detail_code=type(exc).__name__,
            )

    def run(self, value: ReplayEpisode | ShadowInputEnvelope
            ) -> ShadowTrace | ShadowBridgeRejection:
        return self.run_episode(value)
