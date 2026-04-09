"""Manages the lifecycle of mock MCP servers and loads ground truth data.

Reuses ``ScenarioData`` and ``create_*_app`` from ``mock_mcp_server`` to start/stop
the mock MCP endpoints programmatically, and loads ground truth from dataset JSON
files for evaluation.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from event_system.mock_mcp_server import (
    ScenarioData,
    create_elasticsearch_app,
    create_loki_app,
    create_mcp_app,
    create_prometheus_app,
    create_tempo_app,
    run_server,
)

logger = logging.getLogger(__name__)

DEFAULT_PORTS = {
    "kubectl": 8090,
    "elasticsearch": 8092,
    "loki": 8093,
    "tempo": 8094,
    "prometheus": 8095,
}

_SERVER_FACTORIES = {
    "kubectl": create_mcp_app,
    "elasticsearch": create_elasticsearch_app,
    "loki": create_loki_app,
    "tempo": create_tempo_app,
    "prometheus": create_prometheus_app,
}


@dataclass(frozen=True)
class MockEndpoints:
    """URLs of running mock MCP server endpoints."""

    kubectl: str
    elasticsearch: str
    loki: str
    tempo: str
    prometheus: str

    def as_dict(self) -> dict[str, str]:
        """Return all endpoints as a name->url mapping."""
        return {
            "kubectl": self.kubectl,
            "elasticsearch": self.elasticsearch,
            "loki": self.loki,
            "tempo": self.tempo,
            "prometheus": self.prometheus,
        }


@dataclass(frozen=True)
class GroundTruth:
    """Expected output and evaluation data for a single case."""

    case_id: str
    input: dict[str, Any]
    expected_output: str
    golden_entities: list[str]
    metadata: dict[str, Any]


class MockManager:
    """Starts and stops mock MCP servers, and loads ground truth from dataset files.

    Args:
        cache_dir: Directory containing scenario cache JSON files.
        dataset_dir: Directory containing ground truth dataset JSON files.
        host: Bind address for MCP servers.
        ports: Override default ports per server name.
    """

    def __init__(
        self,
        cache_dir: str | Path,
        dataset_dir: str | Path,
        host: str = "127.0.0.1",
        ports: dict[str, int] | None = None,
    ):
        self._cache_dir = Path(cache_dir)
        self._dataset_dir = Path(dataset_dir)
        self._host = host
        self._ports = {**DEFAULT_PORTS, **(ports or {})}
        self._threads: list[threading.Thread] = []
        self._running = False
        self._current_source: str | None = None
        self._ground_truth_cache: dict[str, list[dict[str, Any]]] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_source(self) -> str | None:
        return self._current_source

    def start(self, source_file: str) -> MockEndpoints:
        """Start mock MCP servers for a given cache source file.

        Args:
            source_file: Filename of the cache JSON (e.g. ``kubernetes-crashloop.json``).

        Returns:
            MockEndpoints with the URLs of all running servers.

        Raises:
            FileNotFoundError: If the cache file does not exist.
            RuntimeError: If servers are already running.
        """
        if self._running:
            if self._current_source == source_file:
                return self._build_endpoints()
            self.stop()

        cache_path = self._cache_dir / source_file
        if not cache_path.exists():
            raise FileNotFoundError(f"Cache file not found: {cache_path}")

        data = ScenarioData(str(cache_path))

        self._threads = []
        for name, factory in _SERVER_FACTORIES.items():
            port = self._ports[name]
            app = factory(data, host=self._host, port=port)
            t = threading.Thread(
                target=run_server, args=(app, name), daemon=True, name=f"mock-{name}"
            )
            t.start()
            self._threads.append(t)

        self._running = True
        self._current_source = source_file

        self._wait_for_servers()

        logger.info("Mock MCP started for %s", source_file)
        return self._build_endpoints()

    def stop(self) -> None:
        """Stop all running mock MCP servers.

        Since servers run as daemon threads, we cannot gracefully stop them.
        We clear state and let garbage collection handle the rest.
        """
        if not self._running:
            return

        self._threads.clear()
        self._running = False
        prev = self._current_source
        self._current_source = None
        logger.info("Mock MCP stopped (was serving %s)", prev)

    def _build_endpoints(self) -> MockEndpoints:
        return MockEndpoints(
            kubectl=f"http://{self._host}:{self._ports['kubectl']}/mcp",
            elasticsearch=f"http://{self._host}:{self._ports['elasticsearch']}/mcp",
            loki=f"http://{self._host}:{self._ports['loki']}/mcp",
            tempo=f"http://{self._host}:{self._ports['tempo']}/mcp",
            prometheus=f"http://{self._host}:{self._ports['prometheus']}/mcp",
        )

    def _wait_for_servers(self, timeout: float = 10.0) -> None:
        """Wait until all server ports are accepting connections."""
        deadline = time.monotonic() + timeout
        for name, port in self._ports.items():
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection((self._host, port), timeout=0.5):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                logger.warning("Timeout waiting for %s on port %d", name, port)

    def load_ground_truth(self, source_file: str, scenario_ref: str) -> GroundTruth | None:
        """Load ground truth for a specific case from the dataset JSON.

        Args:
            source_file: The cache/dataset filename (e.g. ``kubernetes-crashloop.json``).
            scenario_ref: The unique scenario reference to find within the dataset.

        Returns:
            GroundTruth if found, None otherwise.
        """
        items = self._load_dataset_file(source_file)
        if not items:
            return None

        case_id_from_ref = self._ref_to_case_id(scenario_ref)
        for item in items:
            if item.get("id") == case_id_from_ref or item.get("id") == scenario_ref:
                return GroundTruth(
                    case_id=item["id"],
                    input=item.get("input", {}),
                    expected_output=item.get("expected_output", ""),
                    golden_entities=item.get("golden_entities", []),
                    metadata=item.get("metadata", {}),
                )

        logger.warning(
            "Ground truth not found for %s in %s (tried id=%s)",
            scenario_ref, source_file, case_id_from_ref,
        )
        return None

    def _load_dataset_file(self, source_file: str) -> list[dict[str, Any]]:
        if source_file in self._ground_truth_cache:
            return self._ground_truth_cache[source_file]

        dataset_path = self._dataset_dir / source_file
        if not dataset_path.exists():
            logger.warning("Dataset file not found: %s", dataset_path)
            self._ground_truth_cache[source_file] = []
            return []

        with open(dataset_path) as f:
            data = json.load(f)

        items = data if isinstance(data, list) else [data]
        self._ground_truth_cache[source_file] = items
        return items

    @staticmethod
    def _ref_to_case_id(scenario_ref: str) -> str:
        """Extract a dataset case ID from a scenario_ref.

        scenario_ref format:
            ``kubernetes-crashloop-KubeDeploymentReplicasMismatch--alert_rephrased-s1200``
        dataset id format:
            ``kubernetes-crashloop-KubeDeploymentReplicasMismatch``

        Strip variation suffix (``--<variation>[-s<seed>]``).
        """
        if "--" in scenario_ref:
            return scenario_ref.split("--")[0]
        return scenario_ref
