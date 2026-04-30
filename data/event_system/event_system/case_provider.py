"""Case providers that resolve scenarios and splits from the ACE database or local files.

Supports two backends:
- DatabaseCaseProvider: queries PostgreSQL (ACE cluster)
- LocalCaseProvider: reads training_*.json files (offline, after dvc pull)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Case:
    """A single scenario case with its catalog metadata."""

    scenario_ref: str
    scenario_id: str
    family: str
    root_cause_key: str
    variation_type: str
    source_file: str
    split: str
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class CaseProvider(Protocol):
    """Interface for resolving cases from a split catalog."""

    def get_cases(
        self,
        split_type: str,
        fold_name: str = "default",
        split: str = "test",
    ) -> list[Case]: ...

    def list_split_types(self) -> list[str]: ...

    def list_folds(self, split_type: str) -> list[str]: ...

    def summary(self, split_type: str, fold_name: str = "default") -> dict[str, int]: ...


class DatabaseCaseProvider:
    """Resolves cases by querying the ACE PostgreSQL database.

    Args:
        db_url: Full connection string, e.g. ``postgresql://user:pass@host:5432/ace``.
            Falls back to env var ``ACE_DATABASE_URL``.
    """

    def __init__(self, db_url: str | None = None):
        try:
            import psycopg2  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "psycopg2 is required for DatabaseCaseProvider. "
                "Install with: uv add psycopg2-binary"
            ) from exc

        self._db_url = db_url or os.environ.get("ACE_DATABASE_URL")
        if not self._db_url:
            raise ValueError(
                "Database URL required. Pass db_url or set ACE_DATABASE_URL env var."
            )

    def _connect(self):
        import psycopg2

        return psycopg2.connect(self._db_url)

    def get_cases(
        self,
        split_type: str,
        fold_name: str = "default",
        split: str = "test",
    ) -> list[Case]:
        """Return all cases for a given split/fold/partition."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT s.scenario_ref, s.scenario_id, s.family, s.root_cause_key,
                       s.variation_type, s.source_file, sa.split, s.metadata
                FROM cases.scenarios s
                JOIN cases.split_assignments sa ON s.scenario_ref = sa.scenario_ref
                WHERE sa.split_type = %s AND sa.fold_name = %s AND sa.split = %s
                ORDER BY s.scenario_id, s.scenario_ref
                """,
                (split_type, fold_name, split),
            )
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()

        cases = []
        for row in rows:
            meta = row[7] if isinstance(row[7], dict) else {}
            cases.append(
                Case(
                    scenario_ref=row[0],
                    scenario_id=row[1],
                    family=row[2] or "",
                    root_cause_key=row[3] or "",
                    variation_type=row[4] or "",
                    source_file=row[5] or "",
                    split=row[6],
                    metadata=meta,
                )
            )
        logger.info(
            "Loaded %d cases from DB: split_type=%s, fold=%s, split=%s",
            len(cases), split_type, fold_name, split,
        )
        return cases

    def list_split_types(self) -> list[str]:
        """Return all available split types."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT split_type FROM cases.split_assignments ORDER BY split_type"
            )
            result = [row[0] for row in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
        return result

    def list_folds(self, split_type: str) -> list[str]:
        """Return all fold names for a given split type."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT fold_name FROM cases.split_assignments "
                "WHERE split_type = %s ORDER BY fold_name",
                (split_type,),
            )
            result = [row[0] for row in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
        return result

    def summary(self, split_type: str, fold_name: str = "default") -> dict[str, int]:
        """Return case counts per partition (train/test/dev)."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT split, count(*) FROM cases.split_assignments "
                "WHERE split_type = %s AND fold_name = %s "
                "GROUP BY split ORDER BY split",
                (split_type, fold_name),
            )
            result = {row[0]: row[1] for row in cur.fetchall()}
            cur.close()
        finally:
            conn.close()
        return result


class LocalCaseProvider:
    """Resolves cases from local training JSON files (offline mode).

    Reads the same training_*.json files that ``seed_scenarios.py`` uses to populate
    the database. Requires ``dvc pull`` to have been run first.

    Args:
        data_dir: Path to the ``data/event_system/`` directory containing
            ``training_*.json`` files.
    """

    _SPLIT_TYPE_FILES = {
        "stratified": "training_stratified.json",
        "leave_family_out": "training_leave_family_out.json",
        "leave_scenario_out": "training_leave_scenario_out.json",
        "leave_variation_out": "training_leave_variation_out.json",
    }

    def __init__(self, data_dir: str | Path | None = None):
        self._data_dir = Path(data_dir) if data_dir else Path(__file__).parent.parent
        self._cache: dict[str, dict] = {}
        self._dev_cases: list[Case] | None = None

    def _load_file(self, split_type: str) -> dict:
        if split_type in self._cache:
            return self._cache[split_type]

        filename = self._SPLIT_TYPE_FILES.get(split_type)
        if not filename:
            available = ", ".join(sorted(self._SPLIT_TYPE_FILES))
            raise ValueError(
                f"Unknown split_type {split_type!r}. Available: {available}"
            )

        path = self._data_dir / filename
        if not path.exists():
            raise FileNotFoundError(
                f"Training file not found: {path}. Run 'dvc pull' first."
            )

        with open(path) as f:
            data = json.load(f)
        self._cache[split_type] = data
        logger.info("Loaded training file: %s", path)
        return data

    def _build_dev_split(self) -> list[Case]:
        """Build the dev split: 1 real case per base scenario."""
        if self._dev_cases is not None:
            return self._dev_cases

        all_cases = self.get_cases("stratified", "default", "train") + self.get_cases(
            "stratified", "default", "test"
        )

        seen: dict[str, Case] = {}
        for case in all_cases:
            if case.variation_type == "real" and case.scenario_id not in seen:
                seen[case.scenario_id] = Case(
                    scenario_ref=case.scenario_ref,
                    scenario_id=case.scenario_id,
                    family=case.family,
                    root_cause_key=case.root_cause_key,
                    variation_type=case.variation_type,
                    source_file=case.source_file,
                    split="dev",
                    metadata=case.metadata,
                )
        self._dev_cases = sorted(seen.values(), key=lambda c: c.scenario_id)
        return self._dev_cases

    @staticmethod
    def _assignment_to_case(assignment: dict, split_value: str) -> Case:
        return Case(
            scenario_ref=assignment["id"],
            scenario_id=assignment.get("base_scenario", ""),
            family=assignment.get("root_cause_family", ""),
            root_cause_key=assignment.get("root_cause_key", ""),
            variation_type=assignment.get("variation_base", ""),
            source_file=assignment.get("file", ""),
            split=split_value,
            metadata={"variation": assignment.get("variation")},
        )

    def get_cases(
        self,
        split_type: str,
        fold_name: str = "default",
        split: str = "test",
    ) -> list[Case]:
        """Return all cases for a given split/fold/partition."""
        if split_type == "dev":
            return self._build_dev_split()

        data = self._load_file(split_type)

        if data["split_type"] == "stratified":
            folds = [{"fold_name": "default", "assignments": data["assignments"]}]
        else:
            folds = data.get("folds", [])

        for fold in folds:
            if fold.get("fold_name", "default") != fold_name:
                continue
            cases = []
            for a in fold.get("assignments", []):
                if a["split"] == split:
                    cases.append(self._assignment_to_case(a, split))
            logger.info(
                "Loaded %d cases from local file: split_type=%s, fold=%s, split=%s",
                len(cases), split_type, fold_name, split,
            )
            return sorted(cases, key=lambda c: (c.scenario_id, c.scenario_ref))

        logger.warning(
            "Fold %r not found in split_type=%s", fold_name, split_type,
        )
        return []

    def list_split_types(self) -> list[str]:
        """Return split types that have local training files available."""
        available = []
        for st, filename in sorted(self._SPLIT_TYPE_FILES.items()):
            if (self._data_dir / filename).exists():
                available.append(st)
        if self._data_dir.exists():
            available.insert(0, "dev")
        return available

    def list_folds(self, split_type: str) -> list[str]:
        """Return all fold names for a given split type."""
        if split_type == "dev":
            return ["default"]

        data = self._load_file(split_type)
        if data["split_type"] == "stratified":
            return ["default"]
        return [fold.get("fold_name", "default") for fold in data.get("folds", [])]

    def summary(self, split_type: str, fold_name: str = "default") -> dict[str, int]:
        """Return case counts per partition."""
        if split_type == "dev":
            dev_cases = self._build_dev_split()
            return {"dev": len(dev_cases)}

        data = self._load_file(split_type)

        if data["split_type"] == "stratified":
            folds = [{"fold_name": "default", "assignments": data["assignments"]}]
        else:
            folds = data.get("folds", [])

        for fold in folds:
            if fold.get("fold_name", "default") != fold_name:
                continue
            counts: dict[str, int] = {}
            for a in fold.get("assignments", []):
                s = a["split"]
                counts[s] = counts.get(s, 0) + 1
            return counts

        return {}
