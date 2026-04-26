"""
Enrich existing cache JSON files with data extracted directly from SQLite DBs.

This avoids needing Catafracto to run Loki/Prometheus/Tempo/ES -- we read the
raw observability data stored in the SQLite database and produce the same cache
format that the recorder would have generated from live services.

Usage:
    python -m event_system.enrich_cache --cache cache/foo.json --db data/databases/foo.db
    python -m event_system.enrich_cache --all   # enrich all matching pairs
"""

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("event_system.enrich")


def _table_has_rows(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row:
        return False
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0


def extract_loki(conn: sqlite3.Connection) -> dict[str, Any] | None:
    if not _table_has_rows(conn, "loki_logs"):
        return None

    cache: dict[str, Any] = {}

    rows = conn.execute(
        "SELECT DISTINCT labels FROM loki_logs WHERE labels IS NOT NULL"
    ).fetchall()
    all_keys: set[str] = set()
    label_values: dict[str, set[str]] = {}
    for row in rows:
        try:
            labels = json.loads(row[0])
            for k, v in labels.items():
                all_keys.add(k)
                label_values.setdefault(k, set()).add(str(v))
        except (json.JSONDecodeError, TypeError):
            pass

    cache["label_names"] = sorted(all_keys)
    cache["label_values"] = {k: sorted(v) for k, v in label_values.items()}

    cache["queries"] = {}
    for ns in label_values.get("namespace", []):
        query = f'{{namespace="{ns}"}}'
        log_rows = conn.execute(
            "SELECT timestamp, line, labels FROM loki_logs WHERE labels LIKE ? ORDER BY timestamp LIMIT 500",
            (f'%"namespace": "{ns}"%'.replace(" ", ""),),
        ).fetchall()
        if not log_rows:
            log_rows = conn.execute(
                "SELECT timestamp, line, labels FROM loki_logs WHERE labels LIKE ? ORDER BY timestamp LIMIT 500",
                (f'%"namespace":"{ns}"%',),
            ).fetchall()

        streams: dict[str, list[list[str]]] = {}
        for r in log_rows:
            ls = r[2] or "{}"
            streams.setdefault(ls, []).append([r[0] or "", r[1] or ""])
        result = []
        for ls, vals in streams.items():
            try:
                sl = json.loads(ls)
            except (json.JSONDecodeError, TypeError):
                sl = {}
            result.append({"stream": sl, "values": vals})
        cache["queries"][query] = result

    logger.info(f"  Loki: {len(cache['label_names'])} labels, {len(cache['queries'])} queries")
    return cache


def extract_prometheus(conn: sqlite3.Connection) -> dict[str, Any] | None:
    if not _table_has_rows(conn, "prometheus_metrics"):
        return None

    cache: dict[str, Any] = {}

    rows = conn.execute(
        "SELECT DISTINCT metric_name FROM prometheus_metrics ORDER BY metric_name"
    ).fetchall()
    cache["metrics"] = [r[0] for r in rows]

    cache["queries"] = {}
    for metric in ["up", "ALERTS", "ALERTS_FOR_STATE"]:
        mrows = conn.execute(
            "SELECT metric_name, labels, value, timestamp FROM prometheus_metrics "
            "WHERE metric_name = ? AND value IS NOT NULL ORDER BY timestamp DESC LIMIT 100",
            (metric,),
        ).fetchall()
        if mrows:
            result = []
            for r in mrows:
                try:
                    labels = json.loads(r[1]) if r[1] else {}
                except (json.JSONDecodeError, TypeError):
                    labels = {}
                labels["__name__"] = r[0]
                result.append({"metric": labels, "value": [r[3], str(r[2])]})
            cache["queries"][metric] = {
                "status": "success",
                "data": {"resultType": "vector", "result": result},
            }

    important_prefixes = [
        "kube_pod_", "kube_deployment_", "kube_node_", "container_",
        "node_", "kubelet_", "kube_statefulset_",
    ]
    for prefix in important_prefixes:
        matching = [m for m in cache["metrics"] if m.startswith(prefix)][:5]
        for metric in matching:
            if metric in cache["queries"]:
                continue
            mrows = conn.execute(
                "SELECT metric_name, labels, value, timestamp FROM prometheus_metrics "
                "WHERE metric_name = ? AND value IS NOT NULL ORDER BY timestamp DESC LIMIT 50",
                (metric,),
            ).fetchall()
            if mrows:
                result = []
                for r in mrows:
                    try:
                        labels = json.loads(r[1]) if r[1] else {}
                    except (json.JSONDecodeError, TypeError):
                        labels = {}
                    labels["__name__"] = r[0]
                    result.append({"metric": labels, "value": [r[3], str(r[2])]})
                cache["queries"][metric] = {
                    "status": "success",
                    "data": {"resultType": "vector", "result": result},
                }

    logger.info(f"  Prometheus: {len(cache['metrics'])} metrics, {len(cache['queries'])} pre-cached queries")
    return cache


def extract_tempo(conn: sqlite3.Connection) -> dict[str, Any] | None:
    if not _table_has_rows(conn, "traces"):
        return None

    cache: dict[str, Any] = {}

    rows = conn.execute(
        "SELECT DISTINCT root_service_name FROM traces WHERE root_service_name IS NOT NULL"
    ).fetchall()
    cache["tag_values"] = [r[0] for r in rows]

    cache["queries"] = {}
    all_traces = conn.execute(
        "SELECT trace_id, root_service_name, root_trace_name, start_time, "
        "end_time, duration_ns, span_count FROM traces ORDER BY start_time LIMIT 50"
    ).fetchall()
    cache["queries"]["{}"] = _traces_to_list(all_traces)

    for svc in cache["tag_values"]:
        query = f'{{resource.service.name = "{svc}"}}'
        svc_traces = conn.execute(
            "SELECT trace_id, root_service_name, root_trace_name, start_time, "
            "end_time, duration_ns, span_count FROM traces "
            "WHERE root_service_name = ? ORDER BY start_time LIMIT 50",
            (svc,),
        ).fetchall()
        cache["queries"][query] = _traces_to_list(svc_traces)

    has_spans = _table_has_rows(conn, "spans")
    if has_spans:
        cache["trace_details"] = {}
        for trace_row in all_traces[:20]:
            tid = trace_row[0]
            spans = conn.execute(
                "SELECT span_id, parent_span_id, name, service_name, "
                "start_time, duration_ns, status, attributes FROM spans WHERE trace_id = ?",
                (tid,),
            ).fetchall()
            cache["trace_details"][tid] = [
                {
                    "spanID": s[0],
                    "parentSpanID": s[1] or "",
                    "operationName": s[2],
                    "serviceName": s[3],
                    "startTime": s[4],
                    "duration": s[5] or 0,
                    "status": s[6],
                    "attributes": json.loads(s[7]) if s[7] else {},
                }
                for s in spans
            ]

    logger.info(f"  Tempo: {len(cache['tag_values'])} services, {len(cache['queries'])} queries")
    return cache


def _traces_to_list(rows) -> list[dict[str, Any]]:
    results = []
    for r in rows:
        results.append({
            "traceID": r[0],
            "rootServiceName": r[1],
            "rootTraceName": r[2],
            "startTimeUnixNano": r[3],
            "durationMs": (r[5] or 0) / 1_000_000,
            "spanSets": [{"matched": r[6] or 0}],
        })
    return results


def extract_elasticsearch(conn: sqlite3.Connection) -> dict[str, Any] | None:
    if not _table_has_rows(conn, "es_documents"):
        return None

    cache: dict[str, Any] = {}

    idx_rows = conn.execute(
        "SELECT index_name, COUNT(*) as cnt FROM es_documents GROUP BY index_name"
    ).fetchall()
    cache["indices"] = [
        {"index": r[0], "docs.count": r[1], "health": "green", "status": "open"}
        for r in idx_rows
    ]

    cache["mappings"] = {}
    cache["queries"] = {}
    for idx_info in cache["indices"]:
        idx = idx_info["index"]
        if idx.startswith("."):
            continue

        sample = conn.execute(
            "SELECT source_data FROM es_documents WHERE index_name = ? LIMIT 1", (idx,)
        ).fetchone()
        if sample:
            try:
                doc = json.loads(sample[0])
                props = {}
                for k, v in doc.items():
                    if isinstance(v, str):
                        props[k] = {"type": "text"}
                    elif isinstance(v, bool):
                        props[k] = {"type": "boolean"}
                    elif isinstance(v, (int, float)):
                        props[k] = {"type": "float"}
                    else:
                        props[k] = {"type": "object"}
                cache["mappings"][idx] = {idx: {"mappings": {"properties": props}}}
            except (json.JSONDecodeError, TypeError):
                pass

        docs = conn.execute(
            "SELECT document_id, source_data, index_name FROM es_documents "
            "WHERE index_name = ? LIMIT 50", (idx,)
        ).fetchall()
        hits = []
        for d in docs:
            try:
                source = json.loads(d[1])
            except (json.JSONDecodeError, TypeError):
                source = {"_raw": d[1]}
            hits.append({"_index": d[2], "_id": d[0], "_source": source})

        key = f'{idx}:{{"size": 50, "sort": [{{"_doc": "asc"}}]}}'
        cache["queries"][key] = {"hits": {"total": {"value": len(hits)}, "hits": hits}}

    cache["shards"] = []

    logger.info(f"  Elasticsearch: {len(cache['indices'])} indices, {len(cache['queries'])} queries")
    return cache


def enrich_one(cache_path: Path, db_path: Path) -> bool:
    with open(cache_path) as f:
        cache = json.load(f)

    conn = sqlite3.connect(str(db_path))

    changed = False

    if "loki" not in cache:
        loki = extract_loki(conn)
        if loki:
            cache["loki"] = loki
            changed = True

    if "prometheus" not in cache:
        prom = extract_prometheus(conn)
        if prom:
            cache["prometheus"] = prom
            changed = True

    if "tempo" not in cache:
        tempo = extract_tempo(conn)
        if tempo:
            cache["tempo"] = tempo
            changed = True

    if "elasticsearch" not in cache:
        es = extract_elasticsearch(conn)
        if es:
            cache["elasticsearch"] = es
            changed = True

    conn.close()

    if changed:
        with open(cache_path, "w") as f:
            json.dump(cache, f, indent=2, default=str)
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description="Enrich cache files with SQLite data")
    parser.add_argument("--cache", help="Path to cache JSON file")
    parser.add_argument("--db", help="Path to SQLite DB file")
    parser.add_argument("--all", action="store_true", help="Enrich all cache/DB pairs")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if args.all:
        cache_dir = Path("cache")
        db_dir = Path("data/databases")
        if not cache_dir.exists() or not db_dir.exists():
            logger.error("cache/ or data/databases/ directory not found")
            sys.exit(1)

        for cache_file in sorted(cache_dir.glob("*.json")):
            name = cache_file.stem
            db_file = db_dir / f"{name}.db"
            if not db_file.exists():
                logger.warning(f"No DB for {name}, skipping")
                continue
            logger.info(f"Enriching: {name}")
            if enrich_one(cache_file, db_file):
                size = cache_file.stat().st_size / 1024
                logger.info(f"  Updated: {cache_file} ({size:.0f} KB)")
            else:
                logger.info(f"  No changes needed")
    elif args.cache and args.db:
        cache_path = Path(args.cache)
        db_path = Path(args.db)
        if not cache_path.exists():
            logger.error(f"Cache not found: {cache_path}")
            sys.exit(1)
        if not db_path.exists():
            logger.error(f"DB not found: {db_path}")
            sys.exit(1)
        logger.info(f"Enriching: {cache_path}")
        if enrich_one(cache_path, db_path):
            size = cache_path.stat().st_size / 1024
            logger.info(f"Updated: {cache_path} ({size:.0f} KB)")
        else:
            logger.info("No changes needed")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
