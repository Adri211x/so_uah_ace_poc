"""ACE Curator skeleton backed by a local JSON playbook.

The production version is expected to use the contexts database schema and
pgvector. For now this keeps the same merge semantics locally: candidate
insights are normalized, deduplicated, counted, and persisted as a playbook.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from src.agents.agent_a_ace.ablations import PlaybookScope
from src.common.schemas import AceInsight, Playbook, PlaybookEntry

logger = logging.getLogger(__name__)


class JsonPlaybookCurator:
    """Merge ACE insights into a deterministic JSON playbook."""

    def __init__(self, playbook_path: Path) -> None:
        """Initialize the curator.

        Args:
            playbook_path: JSON file used to persist the curated playbook.
        """
        self._playbook_path = playbook_path

    def load(self) -> Playbook:
        """Load the playbook, returning an empty one if it does not exist."""
        if not self._playbook_path.exists():
            return Playbook()

        with self._playbook_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return Playbook.model_validate(payload)

    def save(self, playbook: Playbook) -> None:
        """Persist playbook as formatted UTF-8 JSON."""
        self._playbook_path.parent.mkdir(parents=True, exist_ok=True)
        with self._playbook_path.open("w", encoding="utf-8") as handle:
            json.dump(playbook.model_dump(), handle, indent=2, ensure_ascii=False)
            handle.write("\n")

    def curate(self, insights: list[AceInsight]) -> Playbook:
        """Merge candidate insights into the persisted playbook.

        Args:
            insights: Candidate lessons from the Reflector.

        Returns:
            Updated playbook after persistence.
        """
        playbook = self.load()
        entries_by_id = {entry.id: entry for entry in playbook.entries}

        for insight in insights:
            entry_id = _entry_id(insight.text, insight.scenario)
            entry = entries_by_id.get(entry_id)
            if entry is None:
                entry = PlaybookEntry(id=entry_id, text=insight.text, scenario=insight.scenario)
                entries_by_id[entry_id] = entry

            if insight.outcome == "success":
                entry.helpful_count += 1
            else:
                entry.harmful_count += 1

            if insight.source_sample_id not in entry.source_sample_ids:
                entry.source_sample_ids.append(insight.source_sample_id)

        playbook.entries = sorted(entries_by_id.values(), key=lambda item: item.text.lower())
        self.save(playbook)
        logger.info(
            "Curated %d insights into %d playbook entries.",
            len(insights),
            len(playbook.entries),
        )
        return playbook


def select_playbook_entries(
    playbook: Playbook,
    *,
    scenario: str | None = None,
    max_entries: int = 20,
    playbook_scope: PlaybookScope = "fallback",
) -> list[PlaybookEntry]:
    """Select and rank playbook entries for prompt injection.

    Args:
        playbook: Curated playbook.
        scenario: Optional scenario used to prefer scoped entries.
        max_entries: Maximum number of entries to include.
        playbook_scope: Selection behavior for scenario-scoped entries.

    Returns:
        Ranked playbook entries.
    """
    entries = list(playbook.entries)
    if playbook_scope == "strict":
        entries = [entry for entry in entries if entry.scenario == scenario]
    elif scenario:
        scoped_entries = [entry for entry in entries if entry.scenario == scenario]
        if scoped_entries:
            entries = scoped_entries

    ranked_entries = sorted(
        entries,
        key=lambda entry: (entry.helpful_count - entry.harmful_count, entry.helpful_count),
        reverse=True,
    )
    return ranked_entries[:max_entries]


def render_playbook_context(
    playbook: Playbook,
    *,
    scenario: str | None = None,
    max_entries: int = 20,
    playbook_scope: PlaybookScope = "fallback",
) -> str:
    """Render a playbook into prompt context for the Generator.

    Args:
        playbook: Curated playbook.
        scenario: Optional scenario used to prefer scoped entries.
        max_entries: Maximum number of entries to include.
        playbook_scope: Selection behavior for scenario-scoped entries.

    Returns:
        Plain text block suitable for appending to an agent prompt.
    """
    if not playbook.entries:
        return "No learned RCA playbook entries are available yet."

    selected_entries = select_playbook_entries(
        playbook,
        scenario=scenario,
        max_entries=max_entries,
        playbook_scope=playbook_scope,
    )
    if not selected_entries:
        return "No learned RCA playbook entries are available yet."

    lines = ["Learned RCA playbook:"]
    for entry in selected_entries:
        lines.append(f"- {entry.text}")
    return "\n".join(lines)


def build_generator_prompt(
    alert_text: str,
    playbook: Playbook | None = None,
    *,
    scenario: str | None = None,
    playbook_scope: PlaybookScope = "fallback",
) -> str:
    """Build the Agent A user prompt with optional ACE playbook context.

    Args:
        alert_text: Monitoring alert sent to the RCA agent.
        playbook: Optional curated playbook.
        scenario: Optional scenario used to prefer scoped entries.
        playbook_scope: Selection behavior for scenario-scoped entries.

    Returns:
        User prompt for the Generator role.
    """
    if playbook is None or not playbook.entries:
        return alert_text

    context = render_playbook_context(
        playbook,
        scenario=scenario,
        playbook_scope=playbook_scope,
    )
    if context == "No learned RCA playbook entries are available yet.":
        return alert_text
    return f"{context}\n\nAlert:\n{alert_text}"


def _entry_id(text: str, scenario: str | None) -> str:
    """Create a stable short id for a playbook entry."""
    default_scenario = scenario or "*"
    normalized = f"{default_scenario}::{_normalize_text(text)}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _normalize_text(text: str) -> str:
    """Normalize text for deterministic deduplication."""
    return re.sub(r"\s+", " ", text.strip().lower())
