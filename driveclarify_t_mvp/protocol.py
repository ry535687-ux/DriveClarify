"""Shared scientific terminal hooks; contains no wait loop or runner."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScientificTimeoutReceipt:
    terminal: str
    valid_failed_episode: bool
    scientific_retry_allowed: bool
    batch_continues: bool
    simulator_elapsed_s: float
    configured_timeout_s: float
    reason_code: str


def adjudicate_scientific_timeout(
    *,
    simulator_elapsed_s: float,
    configured_timeout_s: float,
    agent_setup_complete: bool,
    model_and_controller_running: bool,
    simulator_usable: bool,
) -> ScientificTimeoutReceipt | None:
    """Return the frozen terminal only when a usable episode times out."""

    if configured_timeout_s <= 0.0:
        raise ValueError("SCIENTIFIC_TIMEOUT_CONFIGURATION_INVALID")
    if simulator_elapsed_s < configured_timeout_s:
        return None
    if not agent_setup_complete or not model_and_controller_running or not simulator_usable:
        return ScientificTimeoutReceipt(
            terminal="PROTOCOL_INVALID",
            valid_failed_episode=False,
            scientific_retry_allowed=False,
            batch_continues=True,
            simulator_elapsed_s=simulator_elapsed_s,
            configured_timeout_s=configured_timeout_s,
            reason_code="INFRASTRUCTURE_NOT_USABLE_AT_TIMEOUT",
        )
    return ScientificTimeoutReceipt(
        terminal="SCIENTIFIC_NONCOMPLETION_TIMEOUT",
        valid_failed_episode=True,
        scientific_retry_allowed=False,
        batch_continues=True,
        simulator_elapsed_s=simulator_elapsed_s,
        configured_timeout_s=configured_timeout_s,
        reason_code="FROZEN_USABLE_RUNTIME_TIMEOUT_EXPIRED",
    )
