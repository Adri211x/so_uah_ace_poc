"""Shared prompts for RCA agents, evaluator, and ACE components.

Agent A and Agent B must use the same ``SYSTEM_PROMPT`` until ACE-specific
behavior is introduced; the judge prompts (``JUDGE_SYSTEM_PROMPT`` and
``JUDGE_USER_PROMPT_TEMPLATE``) feed ``src.common.judge``. The reflector prompts
feed ``src.agents.agent_a_ace.reflector``. ACE Generator and Curator prompt
fragments live here too. All prompts live in the same YAML so prompt iteration
does not require Python changes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_PROMPTS_FILE = Path(__file__).resolve().parent / "prompts.yaml"
_SYSTEM_PROMPT_KEY = "system_prompt"
_JUDGE_SYSTEM_PROMPT_KEY = "judge_system_prompt"
_JUDGE_USER_PROMPT_TEMPLATE_KEY = "judge_user_prompt_template"
_REFLECTOR_SYSTEM_PROMPT_KEY = "reflector_system_prompt"
_REFLECTOR_USER_PROMPT_TEMPLATE_KEY = "reflector_user_prompt_template"
_ACE_GENERATOR_PLAYBOOK_PROMPT_KEY = "ace_generator_playbook_prompt"
_ACE_CURATOR_SYSTEM_PROMPT_KEY = "ace_curator_system_prompt"
_ACE_CURATOR_USER_PROMPT_TEMPLATE_KEY = "ace_curator_user_prompt_template"


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


def _get_string_prompt(data: dict[str, Any], key: str) -> str:
    """Read a required non-empty string prompt by key from the loaded YAML.

    Args:
        data: Parsed prompts mapping.
        key: YAML key to look up.

    Returns:
        Stripped prompt text.

    Raises:
        PromptDefinitionError: If the key is missing, not a string, or empty.
    """
    raw = data.get(key)
    if raw is None:
        raise PromptDefinitionError(f"Missing required key {key!r} in prompts YAML")
    if not isinstance(raw, str):
        raise PromptDefinitionError(f"Key {key!r} must be a string, got {type(raw)}")
    text = raw.strip()
    if not text:
        raise PromptDefinitionError(f"Key {key!r} must not be empty")
    return text


_data = _load_prompts()
SYSTEM_PROMPT = _get_string_prompt(_data, _SYSTEM_PROMPT_KEY)
JUDGE_SYSTEM_PROMPT = _get_string_prompt(_data, _JUDGE_SYSTEM_PROMPT_KEY)
JUDGE_USER_PROMPT_TEMPLATE = _get_string_prompt(_data, _JUDGE_USER_PROMPT_TEMPLATE_KEY)
REFLECTOR_SYSTEM_PROMPT = _get_string_prompt(_data, _REFLECTOR_SYSTEM_PROMPT_KEY)
REFLECTOR_USER_PROMPT_TEMPLATE = _get_string_prompt(_data, _REFLECTOR_USER_PROMPT_TEMPLATE_KEY)
ACE_GENERATOR_PLAYBOOK_PROMPT = _get_string_prompt(_data, _ACE_GENERATOR_PLAYBOOK_PROMPT_KEY)
ACE_CURATOR_SYSTEM_PROMPT = _get_string_prompt(_data, _ACE_CURATOR_SYSTEM_PROMPT_KEY)
ACE_CURATOR_USER_PROMPT_TEMPLATE = _get_string_prompt(
    _data,
    _ACE_CURATOR_USER_PROMPT_TEMPLATE_KEY,
)

logger.debug("Loaded system prompt from %s (%d chars)", _PROMPTS_FILE, len(SYSTEM_PROMPT))
logger.debug(
    "Loaded judge prompts: system=%d chars, template=%d chars",
    len(JUDGE_SYSTEM_PROMPT),
    len(JUDGE_USER_PROMPT_TEMPLATE),
)
logger.debug(
    "Loaded reflector prompts: system=%d chars, template=%d chars",
    len(REFLECTOR_SYSTEM_PROMPT),
    len(REFLECTOR_USER_PROMPT_TEMPLATE),
)
logger.debug(
    "Loaded ACE prompts: generator=%d chars, curator_system=%d chars, curator_template=%d chars",
    len(ACE_GENERATOR_PLAYBOOK_PROMPT),
    len(ACE_CURATOR_SYSTEM_PROMPT),
    len(ACE_CURATOR_USER_PROMPT_TEMPLATE),
)
