"""Configuration for ACE ablation experiment variants."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

InsightOutcome = Literal["success", "failure"]
PlaybookScope = Literal["fallback", "strict"]

_ABLATION_MODE_ENV = "ACE_ABLATION_MODE"
DEFAULT_LEARNING_EPOCHS = 3
DEFAULT_EXPERIMENT_ROOT = Path("tmp/ace_ablations")


class AceAblationMode(StrEnum):
    """Supported ACE ablation experiment modes."""

    BASELINE = "baseline"
    FULL = "full"
    SINGLE_EPOCH = "single_epoch"
    ONLINE_NO_WARMUP = "online_no_warmup"
    SCENARIO_SCOPED_PLAYBOOK = "scenario_scoped_playbook"
    FAILURE_ONLY_REFLECTION = "failure_only_reflection"


@dataclass(frozen=True)
class AceExperimentConfig:
    """Resolved behavior for one ACE ablation variant."""

    mode: AceAblationMode
    agent_name: Literal["agent_a_ace", "agent_b_baseline"]
    warmup_enabled: bool
    learning_epochs: int
    reflection_outcomes: frozenset[InsightOutcome]
    playbook_scope: PlaybookScope

    @property
    def uses_ace(self) -> bool:
        """Return whether this mode should run Agent A with ACE behavior."""
        return self.agent_name == "agent_a_ace"


def parse_ablation_mode(raw_mode: str | AceAblationMode) -> AceAblationMode:
    """Parse and validate an ACE ablation mode."""
    if isinstance(raw_mode, AceAblationMode):
        return raw_mode
    try:
        return AceAblationMode(raw_mode.strip())
    except ValueError as exc:
        valid_modes = ", ".join(mode.value for mode in AceAblationMode)
        raise ValueError(
            f"Unsupported ACE ablation mode {raw_mode!r}. Use one of: {valid_modes}."
        ) from exc


def resolve_ablation_mode(default: AceAblationMode = AceAblationMode.FULL) -> AceAblationMode:
    """Resolve the configured ACE ablation mode from the environment."""
    raw_mode = os.getenv(_ABLATION_MODE_ENV, "").strip()
    if not raw_mode:
        return default
    return parse_ablation_mode(raw_mode)


def build_experiment_config(
    mode: str | AceAblationMode,
    *,
    default_epochs: int = DEFAULT_LEARNING_EPOCHS,
) -> AceExperimentConfig:
    """Build the experiment configuration for one supported ablation mode."""
    resolved_mode = parse_ablation_mode(mode)
    if default_epochs < 1:
        raise ValueError("default_epochs must be greater than or equal to 1.")

    if resolved_mode == AceAblationMode.BASELINE:
        return AceExperimentConfig(
            mode=resolved_mode,
            agent_name="agent_b_baseline",
            warmup_enabled=False,
            learning_epochs=0,
            reflection_outcomes=frozenset({"success", "failure"}),
            playbook_scope="fallback",
        )

    if resolved_mode == AceAblationMode.SINGLE_EPOCH:
        return AceExperimentConfig(
            mode=resolved_mode,
            agent_name="agent_a_ace",
            warmup_enabled=True,
            learning_epochs=1,
            reflection_outcomes=frozenset({"success", "failure"}),
            playbook_scope="fallback",
        )

    if resolved_mode == AceAblationMode.ONLINE_NO_WARMUP:
        return AceExperimentConfig(
            mode=resolved_mode,
            agent_name="agent_a_ace",
            warmup_enabled=False,
            learning_epochs=0,
            reflection_outcomes=frozenset({"success", "failure"}),
            playbook_scope="fallback",
        )

    if resolved_mode == AceAblationMode.SCENARIO_SCOPED_PLAYBOOK:
        return AceExperimentConfig(
            mode=resolved_mode,
            agent_name="agent_a_ace",
            warmup_enabled=True,
            learning_epochs=default_epochs,
            reflection_outcomes=frozenset({"success", "failure"}),
            playbook_scope="strict",
        )

    if resolved_mode == AceAblationMode.FAILURE_ONLY_REFLECTION:
        return AceExperimentConfig(
            mode=resolved_mode,
            agent_name="agent_a_ace",
            warmup_enabled=True,
            learning_epochs=default_epochs,
            reflection_outcomes=frozenset({"failure"}),
            playbook_scope="fallback",
        )

    return AceExperimentConfig(
        mode=resolved_mode,
        agent_name="agent_a_ace",
        warmup_enabled=True,
        learning_epochs=default_epochs,
        reflection_outcomes=frozenset({"success", "failure"}),
        playbook_scope="fallback",
    )
