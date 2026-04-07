"""
Recorder: runs a Catafracto simulation and records all kubectl + observability
responses into a cache JSON file for offline use by the mock MCP server.

Usage:
    python -m event_system.recorder \
        --db data/databases/kubernetes-crashloop.db \
        --output cache/kubernetes-crashloop.json \
        --catafracto-image nexusregistry.datadope.io/smartops/catafracto:latest

This script:
1. Starts a Catafracto Docker container with the given SQLite DB
2. Waits for all services to be healthy
3. Discovers namespaces, pods, services, etc. from the DB
4. Runs all relevant kubectl commands against the simulated k3s
5. Queries Loki, Prometheus, Tempo, and Elasticsearch APIs
6. Saves everything to a JSON cache file
7. Cleans up the container
"""

import argparse
import io
import json
import logging
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("event_system.recorder")

CATAFRACTO_IMAGE_DEFAULT = "nexusregistry.datadope.io/smartops/catafracto:latest"
K3S_PORT = 6443
LOKI_PORT = 3100
PROMETHEUS_PORT = 9090
TEMPO_PORT = 4100
ES_PORT = 9200


def wait_for_k3s_ready(session_id: str, timeout: int = 180) -> str | None:
    """Wait for k3s to be ready by polling kubectl get nodes.
    Returns kubeconfig path on success, None on failure."""
    start = time.time()
    kubeconfig = None
    while time.time() - start < timeout:
        if kubeconfig is None:
            kubeconfig = extract_kubeconfig(session_id)
            if kubeconfig:
                logger.info(f"Kubeconfig extracted after {time.time() - start:.0f}s")
        if kubeconfig:
            output = run_kubectl(kubeconfig, "get nodes")
            if output and "Ready" in output:
                logger.info(f"k3s ready after {time.time() - start:.0f}s")
                return kubeconfig
        time.sleep(3)
    logger.error(f"k3s not ready within {timeout}s")
    return None


def check_service_port(host: str, port: int, path: str = "/") -> bool:
    """Quick check whether an HTTP service is responding on a given port."""
    try:
        resp = httpx.get(f"http://{host}:{port}{path}", timeout=3)
        return resp.status_code < 500
    except Exception:
        return False


def discover_from_db(db_path: str) -> dict[str, Any]:
    """Read the SQLite DB to discover namespaces, pods, etc. for kubectl commands."""
    info: dict[str, Any] = {
        "namespaces": [],
        "pods": {},
        "has_loki": False,
        "has_prometheus": False,
        "has_tempo": False,
        "has_es": False,
    }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    if "pod_logs" in tables:
        rows = conn.execute("SELECT DISTINCT namespace FROM pod_logs").fetchall()
        info["namespaces"] = [r["namespace"] for r in rows]

        for ns in info["namespaces"]:
            pods = conn.execute(
                "SELECT DISTINCT pod_name, container_name FROM pod_logs WHERE namespace = ?",
                (ns,),
            ).fetchall()
            info["pods"][ns] = [
                {"pod": r["pod_name"], "container": r["container_name"]} for r in pods
            ]

    info["has_loki"] = "loki_logs" in tables and conn.execute(
        "SELECT COUNT(*) as c FROM loki_logs"
    ).fetchone()["c"] > 0

    info["has_prometheus"] = "prometheus_metrics" in tables and conn.execute(
        "SELECT COUNT(*) as c FROM prometheus_metrics WHERE value IS NOT NULL"
    ).fetchone()["c"] > 0

    info["has_tempo"] = "traces" in tables and conn.execute(
        "SELECT COUNT(*) as c FROM traces"
    ).fetchone()["c"] > 0

    info["has_es"] = "es_documents" in tables and conn.execute(
        "SELECT COUNT(*) as c FROM es_documents"
    ).fetchone()["c"] > 0

    conn.close()
    return info


def run_kubectl(kubeconfig: str, command: str) -> str | None:
    """Execute a kubectl command and return stdout."""
    full_cmd = ["kubectl", "--kubeconfig", kubeconfig, *command.split()]
    try:
        result = subprocess.run(
            full_cmd, capture_output=True, text=True, timeout=30
        )
        return result.stdout if result.returncode == 0 else result.stderr
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        logger.warning("kubectl error for '%s': %s", command, e)
        return None


def record_kubectl(kubeconfig: str, db_info: dict[str, Any]) -> dict[str, str]:
    """Record kubectl command outputs."""
    cache: dict[str, str] = {}

    global_commands = [
        "get namespaces",
        "get nodes",
        "get nodes -o wide",
        "cluster-info",
        "get namespaces -o yaml",
    ]

    for cmd in global_commands:
        output = run_kubectl(kubeconfig, cmd)
        if output is not None:
            cache[f"kubectl {cmd}"] = output
            logger.info(f"  Recorded: kubectl {cmd}")

    for ns in db_info.get("namespaces", []):
        ns_commands = [
            f"get pods -n {ns}",
            f"get pods -n {ns} -o wide",
            f"get pods -n {ns} -o yaml",
            f"get deployments -n {ns}",
            f"get deployments -n {ns} -o yaml",
            f"get services -n {ns}",
            f"get services -n {ns} -o yaml",
            f"get configmaps -n {ns}",
            f"get events -n {ns} --sort-by=.lastTimestamp",
            f"get events -n {ns}",
            f"get ingress -n {ns}",
            f"get pvc -n {ns}",
            f"get replicasets -n {ns}",
            f"get daemonsets -n {ns}",
            f"get statefulsets -n {ns}",
            f"get jobs -n {ns}",
            f"get endpoints -n {ns}",
            f"get all -n {ns}",
        ]

        for cmd in ns_commands:
            output = run_kubectl(kubeconfig, cmd)
            if output is not None:
                cache[f"kubectl {cmd}"] = output
                logger.info(f"  Recorded: kubectl {cmd}")

        seen_pods: set[str] = set()
        for pod_info in db_info.get("pods", {}).get(ns, []):
            pod = pod_info["pod"]
            container = pod_info["container"]

            if pod not in seen_pods:
                seen_pods.add(pod)
                pod_commands = [
                    f"describe pod {pod} -n {ns}",
                    f"get pod {pod} -n {ns} -o yaml",
                    f"logs {pod} -n {ns}",
                    f"logs {pod} -n {ns} --previous",
                ]
                for cmd in pod_commands:
                    output = run_kubectl(kubeconfig, cmd)
                    if output is not None:
                        cache[f"kubectl {cmd}"] = output
                        logger.info(f"  Recorded: kubectl {cmd}")

            container_log_cmd = f"logs {pod} -c {container} -n {ns}"
            output = run_kubectl(kubeconfig, container_log_cmd)
            if output is not None:
                cache[f"kubectl {container_log_cmd}"] = output

    logger.info(f"Recorded {len(cache)} kubectl commands")
    return cache


def record_loki(host: str, port: int) -> dict[str, Any]:
    """Record Loki responses."""
    cache: dict[str, Any] = {}
    base = f"http://{host}:{port}"

    try:
        resp = httpx.get(f"{base}/loki/api/v1/labels", timeout=10)
        if resp.status_code == 200:
            labels_data = resp.json()
            cache["label_names"] = labels_data.get("data", [])
            logger.info(f"  Loki labels: {cache['label_names']}")

            cache["label_values"] = {}
            for label in cache["label_names"]:
                resp = httpx.get(f"{base}/loki/api/v1/label/{label}/values", timeout=10)
                if resp.status_code == 200:
                    cache["label_values"][label] = resp.json().get("data", [])

            cache["queries"] = {}
            if "namespace" in cache.get("label_values", {}):
                for ns in cache["label_values"]["namespace"]:
                    query = f'{{namespace="{ns}"}}'
                    resp = httpx.get(
                        f"{base}/loki/api/v1/query_range",
                        params={"query": query, "limit": 200},
                        timeout=30,
                    )
                    if resp.status_code == 200:
                        result = resp.json().get("data", {}).get("result", [])
                        cache["queries"][query] = result
                        logger.info(f"  Loki query '{query}': {len(result)} streams")
    except Exception as e:
        logger.warning(f"Loki recording error: {e}")

    return cache


def record_prometheus(host: str, port: int) -> dict[str, Any]:
    """Record Prometheus responses."""
    cache: dict[str, Any] = {}
    base = f"http://{host}:{port}"

    try:
        resp = httpx.get(f"{base}/api/v1/label/__name__/values", timeout=10)
        if resp.status_code == 200:
            cache["metrics"] = resp.json().get("data", [])
            logger.info(f"  Prometheus metrics: {len(cache['metrics'])}")

        resp = httpx.get(f"{base}/api/v1/targets", timeout=10)
        if resp.status_code == 200:
            cache["targets"] = resp.json().get("data", {})

        cache["metadata"] = {}
        resp = httpx.get(f"{base}/api/v1/metadata", timeout=10)
        if resp.status_code == 200:
            cache["metadata"] = resp.json().get("data", {})

        cache["queries"] = {}
        important_metrics = ["up", "ALERTS", "ALERTS_FOR_STATE"]
        for m in important_metrics:
            resp = httpx.get(f"{base}/api/v1/query", params={"query": m}, timeout=10)
            if resp.status_code == 200:
                cache["queries"][m] = resp.json().get("data", {})

    except Exception as e:
        logger.warning(f"Prometheus recording error: {e}")

    return cache


def record_tempo(host: str, port: int) -> dict[str, Any]:
    """Record Tempo responses."""
    cache: dict[str, Any] = {}
    base = f"http://{host}:{port}"

    try:
        resp = httpx.get(f"{base}/api/v2/search/tag/resource.service.name/values", timeout=10)
        if resp.status_code == 200:
            tag_values = resp.json().get("tagValues", [])
            cache["tag_values"] = [tv.get("value", tv) if isinstance(tv, dict) else tv for tv in tag_values]
            logger.info(f"  Tempo services: {cache['tag_values']}")

        cache["queries"] = {}
        resp = httpx.get(
            f"{base}/api/search",
            params={"q": "{}", "limit": 50},
            timeout=30,
        )
        if resp.status_code == 200:
            traces = resp.json().get("traces", [])
            cache["queries"]["{}"] = traces

        for svc in cache.get("tag_values", []):
            query = f'{{resource.service.name = "{svc}"}}'
            resp = httpx.get(
                f"{base}/api/search",
                params={"q": query, "limit": 50},
                timeout=30,
            )
            if resp.status_code == 200:
                traces = resp.json().get("traces", [])
                cache["queries"][query] = traces
                logger.info(f"  Tempo query '{query}': {len(traces)} traces")

    except Exception as e:
        logger.warning(f"Tempo recording error: {e}")

    return cache


def record_elasticsearch(host: str, port: int) -> dict[str, Any]:
    """Record Elasticsearch responses."""
    cache: dict[str, Any] = {}
    base = f"http://{host}:{port}"

    try:
        resp = httpx.get(f"{base}/_cat/indices?format=json", timeout=10,
                         auth=("elastic", "elastic"))
        if resp.status_code == 200:
            cache["indices"] = resp.json()
            logger.info(f"  ES indices: {len(cache['indices'])}")

        cache["mappings"] = {}
        cache["queries"] = {}
        for idx_info in cache.get("indices", []):
            idx = idx_info.get("index", "")
            if idx.startswith("."):
                continue
            resp = httpx.get(f"{base}/{idx}/_mapping", timeout=10,
                             auth=("elastic", "elastic"))
            if resp.status_code == 200:
                cache["mappings"][idx] = resp.json()

            resp = httpx.post(
                f"{base}/{idx}/_search",
                json={"size": 50, "sort": [{"_doc": "asc"}]},
                timeout=10,
                auth=("elastic", "elastic"),
            )
            if resp.status_code == 200:
                key = f'{idx}:{{"size": 50, "sort": [{{"_doc": "asc"}}]}}'
                cache["queries"][key] = resp.json()

        cache["shards"] = []
        resp = httpx.get(f"{base}/_cat/shards?format=json", timeout=10,
                         auth=("elastic", "elastic"))
        if resp.status_code == 200:
            cache["shards"] = resp.json()

    except Exception as e:
        logger.warning(f"Elasticsearch recording error: {e}")

    return cache


def start_catafracto(db_path: str, image: str, network_name: str = "event-system-net",
                     session_id: str = "record") -> str | None:
    """Start Catafracto Docker container. Returns container ID."""
    import docker

    client = docker.from_env()

    try:
        network = client.networks.get(network_name)
    except docker.errors.NotFound:
        network = client.networks.create(network_name, driver="bridge")

    container_name = f"SC-{session_id}"

    try:
        old = client.containers.get(container_name)
        old.stop(timeout=5)
        old.remove()
    except docker.errors.NotFound:
        pass

    cmd = [
        "/app/simulate_from_db.py",
        "--kubeconfig-path", "/k8s/kubeconfig",
        "--id", session_id,
        "/app/db",
    ]

    db_abs = str(Path(db_path).absolute())

    container = client.containers.run(
        image,
        command=cmd,
        volumes={
            "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
            db_abs: {"bind": "/app/db", "mode": "ro"},
        },
        ports={
            "6443/tcp": K3S_PORT,
            "3100/tcp": LOKI_PORT,
            "9090/tcp": PROMETHEUS_PORT,
            "4100/tcp": TEMPO_PORT,
        },
        detach=True,
        remove=False,
        name=container_name,
        network=network_name,
    )

    logger.info(f"Started Catafracto container: {container.id[:12]}")
    return container.id


def extract_kubeconfig(session_id: str = "record") -> str | None:
    """Extract kubeconfig from the Catafracto container."""
    import docker

    client = docker.from_env()
    container = client.containers.get(f"SC-{session_id}")

    try:
        bits, _ = container.get_archive("/k8s/kubeconfig")
        buf = io.BytesIO()
        for chunk in bits:
            buf.write(chunk)
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r") as tar:
            member = tar.getmembers()[0]
            f = tar.extractfile(member)
            if f:
                kubeconfig_path = Path("/tmp/event_system_kubeconfig")
                kubeconfig_path.write_bytes(f.read())
                kubeconfig_content = kubeconfig_path.read_text()
                kubeconfig_content = kubeconfig_content.replace(
                    "127.0.0.1:6443", f"127.0.0.1:{K3S_PORT}"
                )
                kubeconfig_path.write_text(kubeconfig_content)
                return str(kubeconfig_path)
    except Exception as e:
        logger.error(f"Failed to extract kubeconfig: {e}")
    return None


def stop_catafracto(session_id: str = "record", network_name: str = "event-system-net"):
    """Stop and remove Catafracto container and ES simulation."""
    import docker

    client = docker.from_env()
    for name in [f"SC-{session_id}", f"es_simulation_{session_id}"]:
        try:
            c = client.containers.get(name)
            c.stop(timeout=10)
            c.remove()
            logger.info(f"Removed container: {name}")
        except docker.errors.NotFound:
            pass
        except Exception as e:
            logger.warning(f"Error removing {name}: {e}")

    try:
        net = client.networks.get(network_name)
        net.remove()
    except Exception:
        pass


def _stream_container_logs(session_id: str, stop_event: threading.Event):
    """Stream Docker container logs in a background thread."""
    import docker

    try:
        client = docker.from_env()
        container = client.containers.get(f"SC-{session_id}")
        for line in container.logs(stream=True, follow=True):
            if stop_event.is_set():
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                logger.info(f"[catafracto] {text}")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Record scenario responses into cache JSON")
    parser.add_argument("--db", required=True, help="Path to scenario SQLite DB")
    parser.add_argument("--output", required=True, help="Output cache JSON path")
    parser.add_argument("--catafracto-image", default=CATAFRACTO_IMAGE_DEFAULT)
    parser.add_argument("--session-id", default="record")
    parser.add_argument("--timeout", type=int, default=180, help="Health check timeout in seconds")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    db_path = str(Path(args.db).absolute())
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Discovering scenario from DB: {db_path}")
    db_info = discover_from_db(db_path)
    logger.info(f"  Namespaces: {db_info['namespaces']}")
    logger.info(f"  Services: loki={db_info['has_loki']}, prometheus={db_info['has_prometheus']}, "
                f"tempo={db_info['has_tempo']}, elasticsearch={db_info['has_es']}")

    cache: dict[str, Any] = {"_meta": {"db": args.db, "image": args.catafracto_image}}

    log_stop = threading.Event()
    log_thread = None

    try:
        logger.info("Starting Catafracto...")
        container_id = start_catafracto(db_path, args.catafracto_image, session_id=args.session_id)
        if not container_id:
            logger.error("Failed to start Catafracto")
            sys.exit(1)

        log_thread = threading.Thread(
            target=_stream_container_logs,
            args=(args.session_id, log_stop),
            daemon=True,
        )
        log_thread.start()

        logger.info("Waiting for k3s to be ready...")
        kubeconfig = wait_for_k3s_ready(args.session_id, timeout=args.timeout)
        if not kubeconfig:
            logger.error("k3s did not become ready — aborting")
            sys.exit(1)

        log_stop.set()

        logger.info(f"Kubeconfig: {kubeconfig}")
        logger.info("Recording kubectl commands...")
        cache["kubectl"] = record_kubectl(kubeconfig, db_info)

        host = "127.0.0.1"

        if db_info["has_loki"] and check_service_port(host, LOKI_PORT, "/ready"):
            logger.info("Recording Loki responses...")
            cache["loki"] = record_loki(host, LOKI_PORT)
        elif db_info["has_loki"]:
            logger.warning("Loki port not responding — skipping")

        if db_info["has_prometheus"] and check_service_port(host, PROMETHEUS_PORT, "/-/ready"):
            logger.info("Recording Prometheus responses...")
            cache["prometheus"] = record_prometheus(host, PROMETHEUS_PORT)
        elif db_info["has_prometheus"]:
            logger.warning("Prometheus port not responding — skipping")

        if db_info["has_tempo"] and check_service_port(host, TEMPO_PORT, "/ready"):
            logger.info("Recording Tempo responses...")
            cache["tempo"] = record_tempo(host, TEMPO_PORT)
        elif db_info["has_tempo"]:
            logger.warning("Tempo port not responding — skipping")

        if db_info["has_es"] and check_service_port(host, ES_PORT):
            logger.info("Recording Elasticsearch responses...")
            cache["elasticsearch"] = record_elasticsearch(host, ES_PORT)
        elif db_info["has_es"]:
            logger.warning("Elasticsearch port not responding — skipping")

    finally:
        log_stop.set()
        logger.info("Cleaning up Catafracto...")
        stop_catafracto(args.session_id)

    with open(output_path, "w") as f:
        json.dump(cache, f, indent=2, default=str)

    total_entries = sum(
        len(v) if isinstance(v, dict) else 0
        for k, v in cache.items() if k != "_meta"
    )
    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info(f"Cache saved: {output_path} ({size_mb:.1f} MB, {total_entries} top-level entries)")


if __name__ == "__main__":
    main()
