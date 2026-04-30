"""High-level runner that iterates through split cases with automatic mock MCP management.

This is the main interface for agents. It combines a CaseProvider (DB or local) with
a MockManager to present cases one by one, with the mock MCP already running and
ground truth loaded.

Usage::

    from event_system.split_runner import SplitRunner
    from event_system.case_provider import DatabaseCaseProvider

    provider = DatabaseCaseProvider(db_url="postgresql://ace-admin:...@host/ace")
    runner = SplitRunner(provider, cache_dir="cache/", dataset_dir="data/datasets/")

    for run_case in runner.iter_cases("dev"):
        # run_case.endpoints.kubectl -> "http://127.0.0.1:8090/mcp"
        # run_case.input -> {"alert_text": "ALERT: KubePodCrashLooping ..."}
        # run_case.expected_output -> "Root cause: ..."
        # run_case.golden_entities -> ["entity1", "entity2", "entity3"]
        result = my_agent.solve(run_case.input, run_case.endpoints)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from event_system.case_provider import Case, CaseProvider
from event_system.mock_manager import MockEndpoints, MockManager

logger = logging.getLogger(__name__)


@dataclass
class RunCase:
    """A case ready for agent execution, with live mock endpoints and ground truth.

    Attributes:
        case: The catalog metadata for this case.
        endpoints: URLs of running mock MCP servers.
        input: Alert input data (contains ``alert_text`` and more).
        expected_output: The expected root cause analysis text.
        golden_entities: Key entities that should appear in the analysis.
        metadata: Additional metadata from the dataset.
        index: Position in the current iteration (0-based).
        total: Total number of cases in the current iteration.
    """

    case: Case
    endpoints: MockEndpoints
    input: dict[str, Any] = field(default_factory=dict)
    expected_output: str = ""
    golden_entities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    index: int = 0
    total: int = 0


class SplitRunner:
    """Iterates through cases in a split, managing mock MCP servers automatically.

    Cases sharing the same ``source_file`` are grouped so the mock MCP is started
    only once per unique cache file, reducing overhead from 559 to 247 restarts.

    Args:
        provider: A CaseProvider (DatabaseCaseProvider or LocalCaseProvider).
        cache_dir: Path to the directory with cache JSON files.
        dataset_dir: Path to the directory with dataset JSON files (ground truth).
        host: Bind address for mock MCP servers.
        ports: Optional port overrides per server name.
    """

    def __init__(
        self,
        provider: CaseProvider,
        cache_dir: str | Path,
        dataset_dir: str | Path,
        host: str = "127.0.0.1",
        ports: dict[str, int] | None = None,
    ):
        self._provider = provider
        self._mock = MockManager(
            cache_dir=cache_dir,
            dataset_dir=dataset_dir,
            host=host,
            ports=ports,
        )

    @property
    def provider(self) -> CaseProvider:
        """Access the underlying case provider for discovery queries."""
        return self._provider

    @property
    def mock(self) -> MockManager:
        """Access the underlying mock manager."""
        return self._mock

    def list_split_types(self) -> list[str]:
        """Shortcut to provider.list_split_types()."""
        return self._provider.list_split_types()

    def list_folds(self, split_type: str) -> list[str]:
        """Shortcut to provider.list_folds()."""
        return self._provider.list_folds(split_type)

    def summary(self, split_type: str, fold_name: str = "default") -> dict[str, int]:
        """Shortcut to provider.summary()."""
        return self._provider.summary(split_type, fold_name)

    def get_cases(
        self,
        split_type: str,
        fold_name: str = "default",
        split: str = "test",
    ) -> list[Case]:
        """Get raw cases without starting mock servers."""
        return self._provider.get_cases(split_type, fold_name, split)

    def iter_cases(
        self,
        split_type: str,
        fold_name: str = "default",
        split: str = "test",
    ) -> Iterator[RunCase]:
        """Iterate through cases with automatic mock MCP management.

        For each unique ``source_file``, the mock servers are started once and all
        cases sharing that file are yielded. The mock is stopped and restarted only
        when moving to a new source file.

        Args:
            split_type: One of ``dev``, ``stratified``, ``leave_family_out``,
                ``leave_scenario_out``, ``leave_variation_out``.
            fold_name: Fold identifier (default ``"default"``).
            split: Partition: ``train``, ``test``, or ``dev``.

        Yields:
            RunCase instances with live mock endpoints and ground truth.
        """
        if split_type == "dev":
            split = "dev"

        cases = self._provider.get_cases(split_type, fold_name, split)
        if not cases:
            logger.warning(
                "No cases found for split_type=%s, fold=%s, split=%s",
                split_type, fold_name, split,
            )
            return

        total = len(cases)
        groups = self._group_by_source(cases)

        logger.info(
            "Starting iteration: %d cases, %d unique source files",
            total, len(groups),
        )

        global_idx = 0
        try:
            for source_file, group_cases in groups.items():
                try:
                    endpoints = self._mock.start(source_file)
                except FileNotFoundError:
                    logger.error(
                        "Cache file missing for %s, skipping %d cases",
                        source_file, len(group_cases),
                    )
                    global_idx += len(group_cases)
                    continue

                for case in group_cases:
                    ground_truth = self._mock.load_ground_truth(
                        source_file, case.scenario_ref
                    )

                    run_case = RunCase(
                        case=case,
                        endpoints=endpoints,
                        input=ground_truth.input if ground_truth else {},
                        expected_output=ground_truth.expected_output if ground_truth else "",
                        golden_entities=ground_truth.golden_entities if ground_truth else [],
                        metadata=ground_truth.metadata if ground_truth else {},
                        index=global_idx,
                        total=total,
                    )
                    global_idx += 1
                    yield run_case
        finally:
            self._mock.stop()

    @staticmethod
    def _group_by_source(cases: list[Case]) -> dict[str, list[Case]]:
        """Group cases by source_file, preserving order."""
        groups: dict[str, list[Case]] = defaultdict(list)
        for case in cases:
            groups[case.source_file].append(case)
        return dict(groups)
