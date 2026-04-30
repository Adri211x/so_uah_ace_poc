"""Shared system prompt for all RCA agents.

Agent A and Agent B must use the same ``SYSTEM_PROMPT`` until ACE-specific
behavior is introduced; prompt text lives in ``prompts.yaml`` for easier iteration.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_PROMPTS_FILE = Path(__file__).resolve().parent / "prompts.yaml"
_SYSTEM_PROMPT_KEY = "system_prompt"


class PromptDefinitionError(ValueError):
    """Raised when prompts.yaml is missing or invalid."""


def _load_prompts(path: Path = _PROMPTS_FILE) -> dict[str, Any]:
    """Load prompt definitions from YAML.

    Args:
        path: Location of the prompts file (defaults next to this module).

    Returns:
        Parsed mapping from the YAML root object.

    Raises:
        PromptDefinitionError: If the file is missing or not a mapping.
    """
    if not path.is_file():
        raise PromptDefinitionError(f"Prompts file not found: {path}")

    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    if not isinstance(data, dict):
        raise PromptDefinitionError(f"Prompts file root must be a mapping, got {type(data)}")
    return data


def _get_system_prompt(data: dict[str, Any]) -> str:
    raw = data.get(_SYSTEM_PROMPT_KEY)
    if raw is None:
        raise PromptDefinitionError(f"Missing required key {_SYSTEM_PROMPT_KEY!r} in prompts YAML")
    if not isinstance(raw, str):
        raise PromptDefinitionError(f"Key {_SYSTEM_PROMPT_KEY!r} must be a string, got {type(raw)}")
    text = raw.strip()
    if not text:
        raise PromptDefinitionError(f"Key {_SYSTEM_PROMPT_KEY!r} must not be empty")
    return text


_data = _load_prompts()
SYSTEM_PROMPT = _get_system_prompt(_data)
logger.debug("Loaded system prompt from %s (%d chars)", _PROMPTS_FILE, len(SYSTEM_PROMPT))
