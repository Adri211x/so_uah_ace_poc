"""Wrapper that injects ACE playbook context into Generator prompts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.agents.agent_a_ace.curator import JsonPlaybookCurator, build_generator_prompt

DEFAULT_PLAYBOOK_PATH = Path("tmp/playbook.json")
_PLAYBOOK_PATH_ENV = "ACE_PLAYBOOK_PATH"


class PlaybookInjectingAgent:
    """Minimal agent wrapper that prepends curated ACE context."""

    def __init__(self, inner: Any, playbook_path: Path | None = None) -> None:
        """Initialize the wrapper.

        Args:
            inner: Underlying pydantic-ai compatible agent.
            playbook_path: Optional JSON playbook path. ``None`` disables injection.
        """
        self._inner = inner
        self._playbook_path = playbook_path

    def run_sync(
        self,
        user_prompt: str,
        *args: Any,
        scenario: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run the inner agent after injecting playbook context when configured.

        Args:
            user_prompt: Alert text or prompt sent by the runner.
            args: Positional arguments forwarded to the inner agent.
            scenario: Optional scenario used to prefer scoped playbook entries.
            kwargs: Keyword arguments forwarded to the inner agent.

        Returns:
            Inner agent response.
        """
        if self._playbook_path is None:
            return self._inner.run_sync(user_prompt, *args, **kwargs)

        curator = JsonPlaybookCurator(self._playbook_path)
        playbook = curator.load()
        if not playbook.entries:
            return self._inner.run_sync(user_prompt, *args, **kwargs)

        prompt = build_generator_prompt(user_prompt, playbook, scenario=scenario)
        return self._inner.run_sync(prompt, *args, **kwargs)


def resolve_playbook_path() -> Path:
    """Resolve the configured ACE playbook path."""
    raw_path = os.getenv(_PLAYBOOK_PATH_ENV, "").strip()
    return Path(raw_path) if raw_path else DEFAULT_PLAYBOOK_PATH


def wrap_with_playbook(inner: Any, playbook_path: Path | None = None) -> PlaybookInjectingAgent:
    """Wrap an agent so Agent A can use curated playbook context."""
    resolved_path = playbook_path if playbook_path is not None else resolve_playbook_path()
    return PlaybookInjectingAgent(inner, resolved_path)
