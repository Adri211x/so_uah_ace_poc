"""
Mock MCP server that serves pre-generated responses from cache files.

Exposes the same MCP tools that real ROMA infrastructure provides:
- kubectl_impl (kubectl commands)
- loki_label_names, loki_label_values, loki_query (Loki logs)
- list_indices, get_mappings, search, esql (Elasticsearch)
- tempo_tag_values, tempo_query (Tempo traces)
- list_metrics, get_metric_metadata, execute_query, execute_range_query (Prometheus)

All responses come from a pre-generated JSON cache + SQLite DB fallback.
"""

import argparse
import json
import logging
import sqlite3
import sys
import threading
from contextlib import contextmanager
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("event_system")


class ScenarioData:
    """Loads and serves scenario data from cache JSON + SQLite DB."""

    def __init__(self, cache_path: str, db_path: str | None = None):
        self.cache: dict[str, Any] = {}
        self.db_path = db_path
        self._local = threading.local()

        cache_file = Path(cache_path)
        if cache_file.exists():
            with open(cache_file) as f:
                self.cache = json.load(f)
            logger.info(f"Loaded cache: {cache_file} ({len(self.cache)} sections)")
        else:
            logger.warning(f"Cache file not found: {cache_file}")

        if db_path and Path(db_path).exists():
            logger.info(f"SQLite DB available: {db_path}")
        elif db_path:
            logger.warning(f"SQLite DB not found: {db_path}")

    @contextmanager
    def _get_db(self):
        if not self.db_path or not Path(self.db_path).exists():
            yield None
            return
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        yield conn

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    # -- kubectl --

    def kubectl_exec(self, command: str) -> dict[str, Any]:
        kubectl_cache = self.cache.get("kubectl", {})
        full_cmd = command if command.startswith("kubectl") else f"kubectl {command}"
        normalized = full_cmd.strip()

        if normalized in kubectl_cache:
            return {
                "stdout": kubectl_cache[normalized],
                "stderr": "",
                "returncode": 0,
                "command": normalized,
                "success": True,
            }

        best_match, best_score = None, 0.0
        for cached_cmd in kubectl_cache:
            score = SequenceMatcher(None, normalized, cached_cmd).ratio()
            if score > best_score:
                best_score = score
                best_match = cached_cmd

        if best_match and best_score > 0.85:
            return {
                "stdout": kubectl_cache[best_match],
                "stderr": "",
                "returncode": 0,
                "command": best_match,
                "success": True,
            }

        return {
            "stdout": "",
            "stderr": f"Command not found in cache: {normalized}\n"
            f"Available commands ({len(kubectl_cache)}):\n"
            + "\n".join(f"  - {c}" for c in sorted(kubectl_cache.keys())[:20]),
            "returncode": 1,
            "command": normalized,
            "success": False,
        }

    # -- Loki --

    def loki_get_label_names(self) -> list[str]:
        cached = self.cache.get("loki", {}).get("label_names")
        if cached:
            return cached

        with self._get_db() as conn:
            if conn and self._table_exists(conn, "loki_logs"):
                rows = conn.execute("SELECT DISTINCT labels FROM loki_logs WHERE labels IS NOT NULL").fetchall()
                all_keys: set[str] = set()
                for row in rows:
                    try:
                        labels = json.loads(row["labels"])
                        all_keys.update(labels.keys())
                    except (json.JSONDecodeError, TypeError):
                        pass
                return sorted(all_keys)
        return []

    def loki_get_label_values(self, label: str) -> list[str]:
        cached = self.cache.get("loki", {}).get("label_values", {}).get(label)
        if cached:
            return cached

        with self._get_db() as conn:
            if conn and self._table_exists(conn, "loki_logs"):
                rows = conn.execute("SELECT DISTINCT labels FROM loki_logs WHERE labels IS NOT NULL").fetchall()
                values: set[str] = set()
                for row in rows:
                    try:
                        labels = json.loads(row["labels"])
                        if label in labels:
                            values.add(str(labels[label]))
                    except (json.JSONDecodeError, TypeError):
                        pass
                return sorted(values)
        return []

    def loki_query(self, query: str, start: str | None = None, end: str | None = None,
                   limit: int | None = None, format: str | None = None) -> dict[str, Any]:
        cached_queries = self.cache.get("loki", {}).get("queries", {})
        if query in cached_queries:
            results = cached_queries[query]
            if limit:
                results = results[:limit]
            return {"status": "success", "data": {"resultType": "streams", "result": results}}

        with self._get_db() as conn:
            if conn and self._table_exists(conn, "loki_logs"):
                sql = "SELECT timestamp, line, labels FROM loki_logs"
                params: list[Any] = []
                conditions: list[str] = []

                if start:
                    conditions.append("timestamp >= ?")
                    params.append(start)
                if end:
                    conditions.append("timestamp <= ?")
                    params.append(end)

                if conditions:
                    sql += " WHERE " + " AND ".join(conditions)
                sql += " ORDER BY timestamp"
                if limit:
                    sql += f" LIMIT {limit}"

                rows = conn.execute(sql, params).fetchall()
                streams: dict[str, list[list[str]]] = {}
                for row in rows:
                    labels_str = row["labels"] or "{}"
                    if labels_str not in streams:
                        streams[labels_str] = []
                    streams[labels_str].append([row["timestamp"], row["line"]])

                result = []
                for labels_str, values in streams.items():
                    try:
                        stream_labels = json.loads(labels_str)
                    except (json.JSONDecodeError, TypeError):
                        stream_labels = {}
                    result.append({"stream": stream_labels, "values": values})

                return {"status": "success", "data": {"resultType": "streams", "result": result}}

        return {"status": "success", "data": {"resultType": "streams", "result": []}}

    # -- Elasticsearch --

    def es_list_indices(self, index_pattern: str) -> list[dict[str, Any]]:
        cached = self.cache.get("elasticsearch", {}).get("indices")
        if cached:
            import fnmatch
            return [idx for idx in cached if fnmatch.fnmatch(idx.get("index", ""), index_pattern)]

        with self._get_db() as conn:
            if conn and self._table_exists(conn, "es_documents"):
                rows = conn.execute("SELECT DISTINCT index_name FROM es_documents").fetchall()
                import fnmatch
                indices = []
                for row in rows:
                    name = row["index_name"]
                    if fnmatch.fnmatch(name, index_pattern):
                        count = conn.execute(
                            "SELECT COUNT(*) as cnt FROM es_documents WHERE index_name = ?", (name,)
                        ).fetchone()["cnt"]
                        indices.append({"index": name, "docs.count": count, "health": "green", "status": "open"})
                return indices
        return []

    def es_get_mappings(self, index: str) -> dict[str, Any]:
        cached = self.cache.get("elasticsearch", {}).get("mappings", {}).get(index)
        if cached:
            return cached

        with self._get_db() as conn:
            if conn and self._table_exists(conn, "es_documents"):
                row = conn.execute(
                    "SELECT source_data FROM es_documents WHERE index_name = ? LIMIT 1", (index,)
                ).fetchone()
                if row:
                    try:
                        doc = json.loads(row["source_data"])
                        properties = {}
                        for key, value in doc.items():
                            if isinstance(value, str):
                                properties[key] = {"type": "text"}
                            elif isinstance(value, (int, float)):
                                properties[key] = {"type": "float"}
                            elif isinstance(value, bool):
                                properties[key] = {"type": "boolean"}
                            else:
                                properties[key] = {"type": "object"}
                        return {index: {"mappings": {"properties": properties}}}
                    except (json.JSONDecodeError, TypeError):
                        pass
        return {}

    def es_search(self, index: str, query_body: dict[str, Any]) -> dict[str, Any]:
        cached_queries = self.cache.get("elasticsearch", {}).get("queries", {})
        cache_key = f"{index}:{json.dumps(query_body, sort_keys=True)}"
        if cache_key in cached_queries:
            return cached_queries[cache_key]

        with self._get_db() as conn:
            if conn and self._table_exists(conn, "es_documents"):
                size = query_body.get("size", 10)
                rows = conn.execute(
                    "SELECT document_id, source_data, index_name FROM es_documents WHERE index_name LIKE ? LIMIT ?",
                    (index.replace("*", "%"), size),
                ).fetchall()
                hits = []
                for row in rows:
                    try:
                        source = json.loads(row["source_data"])
                    except (json.JSONDecodeError, TypeError):
                        source = {"_raw": row["source_data"]}
                    hits.append({"_index": row["index_name"], "_id": row["document_id"], "_source": source})
                return {"hits": {"total": {"value": len(hits)}, "hits": hits}}
        return {"hits": {"total": {"value": 0}, "hits": []}}

    def es_esql(self, query: str) -> dict[str, Any]:
        cached = self.cache.get("elasticsearch", {}).get("esql_queries", {}).get(query)
        if cached:
            return cached
        return {"columns": [], "values": [], "_note": "ES|QL queries only available from cache"}

    def es_get_shards(self) -> list[dict[str, Any]]:
        cached = self.cache.get("elasticsearch", {}).get("shards")
        if cached:
            return cached
        return []

    # -- Tempo --

    def tempo_get_tag_values(self) -> list[str]:
        cached = self.cache.get("tempo", {}).get("tag_values")
        if cached:
            return cached

        with self._get_db() as conn:
            if conn and self._table_exists(conn, "traces"):
                rows = conn.execute("SELECT DISTINCT root_service_name FROM traces WHERE root_service_name IS NOT NULL").fetchall()
                return [row["root_service_name"] for row in rows]
        return []

    def tempo_query(self, query: str) -> list[dict[str, Any]]:
        cached_queries = self.cache.get("tempo", {}).get("queries", {})
        if query in cached_queries:
            return cached_queries[query]

        with self._get_db() as conn:
            if conn and self._table_exists(conn, "traces"):
                rows = conn.execute(
                    "SELECT trace_id, root_service_name, root_trace_name, start_time, "
                    "end_time, duration_ns, span_count FROM traces ORDER BY start_time LIMIT 50"
                ).fetchall()
                results = []
                for row in rows:
                    results.append({
                        "traceID": row["trace_id"],
                        "rootServiceName": row["root_service_name"],
                        "rootTraceName": row["root_trace_name"],
                        "startTimeUnixNano": row["start_time"],
                        "durationMs": (row["duration_ns"] or 0) / 1_000_000,
                        "spanSets": [{"matched": row["span_count"] or 0}],
                    })
                return results
        return []

    # -- Prometheus --

    def prom_list_metrics(self, limit: int | None = None, offset: int = 0,
                          filter_pattern: str | None = None) -> dict[str, Any]:
        cached = self.cache.get("prometheus", {}).get("metrics")
        if cached:
            metrics = cached
        else:
            metrics = []
            with self._get_db() as conn:
                if conn and self._table_exists(conn, "prometheus_metrics"):
                    rows = conn.execute("SELECT DISTINCT metric_name FROM prometheus_metrics ORDER BY metric_name").fetchall()
                    metrics = [row["metric_name"] for row in rows]

        if filter_pattern:
            import re
            try:
                pattern = re.compile(filter_pattern, re.IGNORECASE)
                metrics = [m for m in metrics if pattern.search(m)]
            except re.error:
                metrics = [m for m in metrics if filter_pattern.lower() in m.lower()]

        total = len(metrics)
        if limit:
            metrics = metrics[offset : offset + limit]
        else:
            metrics = metrics[offset:]

        return {
            "metrics": metrics,
            "total_count": total,
            "returned_count": len(metrics),
            "offset": offset,
            "has_more": offset + len(metrics) < total,
        }

    def prom_get_metric_metadata(self, metric: str | None = None,
                                  filter_pattern: str | None = None) -> dict[str, Any]:
        cached = self.cache.get("prometheus", {}).get("metadata", {})
        if metric and metric in cached:
            return cached[metric]
        if cached:
            return cached
        return {"_note": "Metric metadata only available from cache"}

    def prom_execute_query(self, query: str, time: str | None = None) -> dict[str, Any]:
        cached = self.cache.get("prometheus", {}).get("queries", {})
        if query in cached:
            return cached[query]

        with self._get_db() as conn:
            if conn and self._table_exists(conn, "prometheus_metrics"):
                metric_name = query.strip().split("{")[0].split("(")[-1].strip(")")
                rows = conn.execute(
                    "SELECT metric_name, labels, value, timestamp FROM prometheus_metrics "
                    "WHERE metric_name = ? AND value IS NOT NULL ORDER BY timestamp DESC LIMIT 100",
                    (metric_name,),
                ).fetchall()
                if rows:
                    result = []
                    for row in rows:
                        try:
                            labels = json.loads(row["labels"]) if row["labels"] else {}
                        except (json.JSONDecodeError, TypeError):
                            labels = {}
                        labels["__name__"] = row["metric_name"]
                        result.append({"metric": labels, "value": [row["timestamp"], str(row["value"])]})
                    return {"status": "success", "data": {"resultType": "vector", "result": result}}

        return {"status": "success", "data": {"resultType": "vector", "result": []}}

    def prom_execute_range_query(self, query: str, start: str, end: str, step: str) -> dict[str, Any]:
        cached = self.cache.get("prometheus", {}).get("range_queries", {})
        cache_key = f"{query}|{start}|{end}|{step}"
        if cache_key in cached:
            return cached[cache_key]
        return {"status": "success", "data": {"resultType": "matrix", "result": []},
                "_note": "Range queries only available from cache"}


def create_mcp_app(data: ScenarioData, name: str = "kubectl",
                   host: str = "127.0.0.1", port: int = 8090) -> FastMCP:
    """Create MCP app for kubectl tools."""
    mcp = FastMCP(name, host=host, port=port, stateless_http=True)

    @mcp.tool()
    def kubectl_impl(command: str) -> dict[str, Any]:
        """Execute a kubectl command. Pass the command without the 'kubectl' prefix."""
        return data.kubectl_exec(command)

    return mcp


def create_loki_app(data: ScenarioData, host: str = "127.0.0.1", port: int = 8093) -> FastMCP:
    """Create MCP app for Loki tools."""
    mcp = FastMCP("loki", host=host, port=port, stateless_http=True)

    @mcp.tool()
    def loki_label_names() -> list[str]:
        """Get all available label names in Loki."""
        return data.loki_get_label_names()

    @mcp.tool()
    def loki_label_values(label: str) -> list[str]:
        """Get all values for a specific label."""
        return data.loki_get_label_values(label)

    @mcp.tool()
    def loki_query(query: str, start: str | None = None, end: str | None = None,
                   limit: int | None = None, format: str | None = None) -> dict[str, Any]:
        """Run a LogQL query against Loki."""
        return data.loki_query(query, start, end, limit, format)

    return mcp


def create_elasticsearch_app(data: ScenarioData, host: str = "127.0.0.1", port: int = 8092) -> FastMCP:
    """Create MCP app for Elasticsearch tools."""
    mcp = FastMCP("elasticsearch", host=host, port=port, stateless_http=True)

    @mcp.tool()
    def list_indices(index_pattern: str) -> list[dict[str, Any]]:
        """List available Elasticsearch indices matching a pattern."""
        return data.es_list_indices(index_pattern)

    @mcp.tool()
    def get_mappings(index: str) -> dict[str, Any]:
        """Get field mappings for an Elasticsearch index."""
        return data.es_get_mappings(index)

    @mcp.tool()
    def get_shards() -> list[dict[str, Any]]:
        """Get shard information for all indices."""
        return data.es_get_shards()

    @mcp.tool()
    def search(index: str, query_body: dict[str, Any]) -> dict[str, Any]:
        """Search an Elasticsearch index using Query DSL."""
        return data.es_search(index, query_body)

    @mcp.tool()
    def esql(query: str) -> dict[str, Any]:
        """Run an ES|QL query."""
        return data.es_esql(query)

    return mcp


def create_tempo_app(data: ScenarioData, host: str = "127.0.0.1", port: int = 8094) -> FastMCP:
    """Create MCP app for Tempo tools."""
    mcp = FastMCP("tempo", host=host, port=port, stateless_http=True)

    @mcp.tool()
    def tempo_tag_values() -> list[str]:
        """Get possible values for resource.service.name in Tempo."""
        return data.tempo_get_tag_values()

    @mcp.tool()
    def tempo_query(query: str) -> list[dict[str, Any]]:
        """Run a TraceQL query against Grafana Tempo."""
        return data.tempo_query(query)

    return mcp


def create_prometheus_app(data: ScenarioData, host: str = "127.0.0.1", port: int = 8095) -> FastMCP:
    """Create MCP app for Prometheus tools."""
    mcp = FastMCP("prometheus", host=host, port=port, stateless_http=True)

    @mcp.tool()
    def list_metrics(limit: int | None = None, offset: int = 0,
                     filter_pattern: str | None = None) -> dict[str, Any]:
        """List available Prometheus metrics."""
        return data.prom_list_metrics(limit, offset, filter_pattern)

    @mcp.tool()
    def get_metric_metadata(metric: str | None = None,
                            filter_pattern: str | None = None) -> dict[str, Any]:
        """Get metadata for Prometheus metrics."""
        return data.prom_get_metric_metadata(metric, filter_pattern)

    @mcp.tool()
    def execute_query(query: str, time: str | None = None) -> dict[str, Any]:
        """Execute an instant PromQL query."""
        return data.prom_execute_query(query, time)

    @mcp.tool()
    def execute_range_query(query: str, start: str, end: str, step: str) -> dict[str, Any]:
        """Execute a range PromQL query."""
        return data.prom_execute_range_query(query, start, end, step)

    return mcp


def run_server(app: FastMCP, name: str):
    """Run a single MCP server in a thread."""
    logger.info(f"Starting {name} MCP on http://{app.settings.host}:{app.settings.port}/mcp")
    app.run(transport="streamable-http")


def main():
    parser = argparse.ArgumentParser(description="Mock MCP Server for agent evaluation")
    parser.add_argument("--cache", required=True, help="Path to scenario cache JSON")
    parser.add_argument("--db", default=None, help="Path to scenario SQLite DB (optional fallback)")
    parser.add_argument("--kubectl-port", type=int, default=8090)
    parser.add_argument("--es-port", type=int, default=8092)
    parser.add_argument("--loki-port", type=int, default=8093)
    parser.add_argument("--tempo-port", type=int, default=8094)
    parser.add_argument("--prometheus-port", type=int, default=8095)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--only", nargs="*", default=None,
                        help="Only start specific servers (kubectl, loki, elasticsearch, tempo, prometheus)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    data = ScenarioData(args.cache, args.db)

    servers_config = {
        "kubectl": (create_mcp_app, args.kubectl_port),
        "elasticsearch": (create_elasticsearch_app, args.es_port),
        "loki": (create_loki_app, args.loki_port),
        "tempo": (create_tempo_app, args.tempo_port),
        "prometheus": (create_prometheus_app, args.prometheus_port),
    }

    enabled = args.only if args.only else list(servers_config.keys())

    threads: list[threading.Thread] = []
    print("\n" + "=" * 60)
    print("  Event System - Mock MCP Server")
    print("=" * 60)

    for name in enabled:
        if name not in servers_config:
            logger.warning(f"Unknown server: {name}")
            continue
        factory, port = servers_config[name]
        app = factory(data, host=args.host, port=port)
        t = threading.Thread(target=run_server, args=(app, name), daemon=True)
        t.start()
        threads.append(t)
        print(f"  {name:15s} → http://{args.host}:{port}/mcp")

    print(f"\n  Cache: {args.cache}")
    if args.db:
        print(f"  DB:    {args.db}")
    print("=" * 60)
    print("  Press Ctrl+C to stop\n")

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()
