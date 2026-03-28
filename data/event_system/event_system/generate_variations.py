"""
Generate synthetic variations of existing scenarios by mutating names,
IPs, timestamps, and UUIDs while preserving the root cause and structure.

Supported variation types:
    namespace_shifted  - Rename namespaces, pods, IPs, timestamps, UUIDs
    noise_injected     - Add healthy decoy pods/namespaces to the cache
    alert_rephrased    - Rewrite alerts with minimal/different detail level

Usage:
    python -m event_system.generate_variations --type namespace_shifted --all
    python -m event_system.generate_variations --type noise_injected --all
    python -m event_system.generate_variations --type alert_rephrased --all
"""

import argparse
import copy
import json
import logging
import random
import re
import string
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("event_system.variations")

SYSTEM_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease", "default", "prometheus"}
ENV_SUFFIXES = ["-prod", "-staging", "-dev", "-qa", "-preprod", "-uat", "-test2", "-integration"]
NS_PREFIXES = ["app-", "svc-", "platform-", "team-", "infra-", "workload-", "deploy-", "env-"]

VARIATION_TYPES = ["namespace_shifted", "noise_injected", "alert_rephrased"]


class NameMapper:
    """Builds a deterministic name mapping for a scenario and applies it to text."""

    def __init__(self, scenario_name: str, cache: dict, dataset: list, seed: int = 42):
        self.rng = random.Random(seed)
        self.scenario_name = scenario_name
        self.replacements: list[tuple[str, str]] = []
        self._mappings_log: dict[str, dict[str, str]] = {}

        kubectl_cache = cache.get("kubectl", {})

        self._build_namespace_map(kubectl_cache)
        self._build_pod_map(kubectl_cache)
        self._build_node_map(kubectl_cache)
        self._build_ip_map(kubectl_cache)
        self._build_uid_map(kubectl_cache, dataset)
        self._build_timestamp_shift()

        self.replacements.sort(key=lambda t: -len(t[0]))

    def _build_namespace_map(self, kubectl_cache: dict):
        ns_set: set[str] = set()
        pattern = re.compile(r"-n\s+([\w][\w.-]*)")
        for key in kubectl_cache:
            for m in pattern.finditer(key):
                ns = m.group(1)
                if ns not in SYSTEM_NAMESPACES:
                    ns_set.add(ns)

        ns_output = kubectl_cache.get("kubectl get namespaces", "")
        for line in ns_output.strip().split("\n")[1:]:
            parts = line.split()
            if parts and parts[0] not in SYSTEM_NAMESPACES:
                ns_set.add(parts[0])

        self._mappings_log["namespaces"] = {}
        for ns in sorted(ns_set):
            prefix = self.rng.choice(NS_PREFIXES)
            suffix = self.rng.choice(ENV_SUFFIXES)
            base = re.sub(r"^(kubernetes-)?", "", ns)
            new_ns = f"{prefix}{base}{suffix}"
            self._mappings_log["namespaces"][ns] = new_ns
            self.replacements.append((ns, new_ns))

    def _build_pod_map(self, kubectl_cache: dict):
        pod_pattern = re.compile(r"([\w][\w.-]*)-([0-9a-f]{6,10})-([a-z0-9]{5})\b")
        job_pattern = re.compile(r"([\w][\w.-]*)-(\d{8,})-([a-z0-9]{5})\b")
        pods_seen: set[str] = set()
        self._mappings_log["pods"] = {}

        skip_prefixes = ("k3d-", "sha256-", "kube-", "10.")

        for key, val in kubectl_cache.items():
            text = f"{key} {val}"
            for m in pod_pattern.finditer(text):
                full = m.group(0)
                if full in pods_seen or any(full.startswith(p) for p in skip_prefixes):
                    continue
                pods_seen.add(full)
                new_rs_hash = "".join(self.rng.choices("0123456789abcdef", k=len(m.group(2))))
                new_pod_hash = "".join(self.rng.choices(string.ascii_lowercase + string.digits, k=5))
                new_pod = f"{m.group(1)}-{new_rs_hash}-{new_pod_hash}"
                self._mappings_log["pods"][full] = new_pod
                self.replacements.append((full, new_pod))

            for m in job_pattern.finditer(text):
                full = m.group(0)
                if full in pods_seen or any(full.startswith(p) for p in skip_prefixes):
                    continue
                pods_seen.add(full)
                old_num = int(m.group(2))
                new_num = old_num + self.rng.randint(100, 999)
                new_suffix = "".join(self.rng.choices(string.ascii_lowercase + string.digits, k=5))
                new_job = f"{m.group(1)}-{new_num}-{new_suffix}"
                self._mappings_log["pods"][full] = new_job
                self.replacements.append((full, new_job))

    def _build_node_map(self, kubectl_cache: dict):
        node_output = kubectl_cache.get("kubectl get nodes", "")
        self._mappings_log["nodes"] = {}
        for line in node_output.strip().split("\n")[1:]:
            if not line.strip():
                continue
            node_name = line.split()[0]
            ns_map = self._mappings_log.get("namespaces", {})
            new_node = node_name
            for old_ns, new_ns in ns_map.items():
                base = old_ns.replace("-test", "").replace("-", "-")
                new_base = new_ns
                new_node = new_node.replace(base, new_base)
            if new_node == node_name:
                new_node = node_name.replace("server-0", f"node-{self.rng.randint(1,9)}")
            self._mappings_log["nodes"][node_name] = new_node
            self.replacements.append((node_name, new_node))

    def _build_ip_map(self, kubectl_cache: dict):
        ip_pattern = re.compile(r"10\.42\.0\.(\d+)")
        ips_seen: set[str] = set()
        self._mappings_log["ips"] = {}

        for val in kubectl_cache.values():
            for m in ip_pattern.finditer(val):
                ips_seen.add(m.group(0))

        shift = self.rng.randint(30, 180)
        for ip in sorted(ips_seen):
            old_last = int(ip.split(".")[-1])
            new_last = ((old_last + shift) % 245) + 2
            new_ip = f"10.42.1.{new_last}"
            self._mappings_log["ips"][ip] = new_ip
            self.replacements.append((ip, new_ip))

    def _build_uid_map(self, kubectl_cache: dict, dataset: list):
        uid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
        uids: set[str] = set()
        self._mappings_log["uids"] = {}

        all_text = json.dumps(kubectl_cache) + json.dumps(dataset)
        for m in uid_pattern.finditer(all_text):
            uids.add(m.group(0))

        for old_uid in sorted(uids):
            new_uid = str(uuid.UUID(int=self.rng.getrandbits(128), version=4))
            self._mappings_log["uids"][old_uid] = new_uid
            self.replacements.append((old_uid, new_uid))

    def _build_timestamp_shift(self):
        self.time_shift = timedelta(days=self.rng.randint(2, 14),
                                     hours=self.rng.randint(0, 23),
                                     minutes=self.rng.randint(0, 59))
        self._mappings_log["time_shift_hours"] = str(self.time_shift.total_seconds() / 3600)

    def apply_to_text(self, text: str) -> str:
        for old, new in self.replacements:
            text = text.replace(old, new)
        text = self._shift_timestamps(text)
        return text

    def _shift_timestamps(self, text: str) -> str:
        iso_pattern = re.compile(
            r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?([+-]\d{2}:\d{2}|Z)?"
        )

        def _replace_ts(m: re.Match) -> str:
            base = m.group(1)
            frac = m.group(2) or ""
            tz_part = m.group(3) or ""
            try:
                dt = datetime.fromisoformat(base)
                dt += self.time_shift
                new_base = dt.strftime("%Y-%m-%dT%H:%M:%S")
                return f"{new_base}{frac}{tz_part}"
            except ValueError:
                return m.group(0)

        return iso_pattern.sub(_replace_ts, text)

    @property
    def mappings(self) -> dict:
        return self._mappings_log


def mutate_cache(cache: dict, mapper: NameMapper, variation_type: str) -> dict:
    """Apply name mapping to all cache sections."""
    new_cache: dict[str, Any] = {}

    new_cache["_meta"] = copy.deepcopy(cache.get("_meta", {}))
    new_cache["_meta"]["case_type"] = variation_type
    new_cache["_meta"]["source_scenario"] = mapper.scenario_name
    new_cache["_meta"]["variation_seed"] = mapper.rng.getrandbits(32)
    new_cache["_meta"]["mappings"] = mapper.mappings

    if "kubectl" in cache:
        new_kubectl = {}
        for key, val in cache["kubectl"].items():
            new_key = mapper.apply_to_text(key)
            new_val = mapper.apply_to_text(val)
            new_kubectl[new_key] = new_val
        new_cache["kubectl"] = new_kubectl

    if "prometheus" in cache:
        new_prom = copy.deepcopy(cache["prometheus"])
        if "queries" in new_prom:
            new_queries = {}
            for qkey, qval in new_prom["queries"].items():
                new_qkey = mapper.apply_to_text(qkey)
                new_qval_str = mapper.apply_to_text(json.dumps(qval))
                new_queries[new_qkey] = json.loads(new_qval_str)
            new_prom["queries"] = new_queries
        new_cache["prometheus"] = new_prom

    if "loki" in cache:
        new_loki = copy.deepcopy(cache["loki"])
        if "label_values" in new_loki:
            new_lv = {}
            for lk, lvals in new_loki["label_values"].items():
                new_lv[lk] = [mapper.apply_to_text(v) for v in lvals]
            new_loki["label_values"] = new_lv
        if "queries" in new_loki:
            new_q = {}
            for qk, qv in new_loki["queries"].items():
                new_qk = mapper.apply_to_text(qk)
                new_qv_str = mapper.apply_to_text(json.dumps(qv))
                new_q[new_qk] = json.loads(new_qv_str)
            new_loki["queries"] = new_q
        new_cache["loki"] = new_loki

    if "tempo" in cache:
        new_tempo_str = mapper.apply_to_text(json.dumps(cache["tempo"]))
        new_cache["tempo"] = json.loads(new_tempo_str)

    if "elasticsearch" in cache:
        new_es_str = mapper.apply_to_text(json.dumps(cache["elasticsearch"]))
        new_cache["elasticsearch"] = json.loads(new_es_str)

    return new_cache


def mutate_dataset(dataset: list, mapper: NameMapper, variation_type: str,
                   scenario_name: str) -> list:
    """Apply name mapping to alert text and expected output."""
    new_dataset = []
    for entry in dataset:
        new_entry = copy.deepcopy(entry)
        new_entry["id"] = f"{entry['id']}--{variation_type}"

        new_entry["input"]["alert_text"] = mapper.apply_to_text(entry["input"]["alert_text"])
        if "metadata" in new_entry["input"]:
            new_entry["input"]["metadata"]["name"] = f"{entry['input']['metadata'].get('name', scenario_name)}--{variation_type}"

        new_entry["expected_output"] = mapper.apply_to_text(entry["expected_output"])

        if "metadata" in new_entry:
            new_entry["metadata"]["name"] = f"{entry['metadata'].get('name', scenario_name)}--{variation_type}"
            new_entry["metadata"]["variation_type"] = variation_type
            new_entry["metadata"]["source_scenario"] = scenario_name

        new_dataset.append(new_entry)
    return new_dataset


# ---------------------------------------------------------------------------
# noise_injected: add healthy decoy namespaces/pods to the kubectl cache
# ---------------------------------------------------------------------------

NOISE_APPS = [
    {"ns": "payments-api", "deploy": "payments-server", "image": "payments-api:3.2.1", "replicas": 2},
    {"ns": "user-service", "deploy": "user-backend", "image": "user-svc:1.8.0", "replicas": 3},
    {"ns": "notifications", "deploy": "notifier", "image": "notifier:2.0.4", "replicas": 1},
    {"ns": "inventory-mgmt", "deploy": "inventory-api", "image": "inventory:4.1.0", "replicas": 2},
    {"ns": "logging-infra", "deploy": "log-collector", "image": "fluentbit:2.1.8", "replicas": 2},
    {"ns": "cache-layer", "deploy": "redis-proxy", "image": "redis:7.2-alpine", "replicas": 1},
    {"ns": "api-gateway", "deploy": "gateway", "image": "envoy:1.28.0", "replicas": 2},
    {"ns": "search-engine", "deploy": "search-api", "image": "opensearch:2.11", "replicas": 2},
]


def _gen_pod_hash(rng: random.Random) -> str:
    rs = "".join(rng.choices("0123456789abcdef", k=10))
    pod = "".join(rng.choices(string.ascii_lowercase + string.digits, k=5))
    return f"{rs}-{pod}"


def _gen_noise_kubectl(rng: random.Random, num_noise: int = 3) -> dict[str, str]:
    """Generate kubectl outputs for healthy decoy namespaces."""
    chosen = rng.sample(NOISE_APPS, min(num_noise, len(NOISE_APPS)))
    entries: dict[str, str] = {}
    base_ip = rng.randint(50, 200)

    for idx, app in enumerate(chosen):
        ns = app["ns"]
        deploy = app["deploy"]
        image = app["image"]
        replicas = app["replicas"]

        pods = []
        for r in range(replicas):
            h = _gen_pod_hash(rng)
            ip = f"10.42.0.{base_ip + idx * 10 + r}"
            pods.append({"name": f"{deploy}-{h}", "ip": ip})

        pod_lines = "\n".join(
            f"{p['name']}   1/1     Running   0          4d" for p in pods
        )
        entries[f"kubectl get pods -n {ns}"] = f"NAME{' '*40}READY   STATUS    RESTARTS   AGE\n{pod_lines}\n"

        wide_lines = "\n".join(
            f"{p['name']}   1/1     Running   0          4d   {p['ip']}   <none>   Ubuntu 22.04   6.5.0   containerd://1.7.11"
            for p in pods
        )
        entries[f"kubectl get pods -n {ns} -o wide"] = (
            f"NAME{' '*40}READY   STATUS    RESTARTS   AGE   IP{' '*12}NODE{' '*20}NOMINATED NODE   READINESS GATES\n{wide_lines}\n"
        )

        entries[f"kubectl get deployments -n {ns}"] = (
            f"NAME{' '*20}READY   UP-TO-DATE   AVAILABLE   AGE\n"
            f"{deploy}{' '*(24-len(deploy))}{replicas}/{replicas}     {replicas}            {replicas}           4d\n"
        )

        entries[f"kubectl get services -n {ns}"] = (
            f"NAME{' '*20}TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE\n"
            f"{deploy}{' '*(24-len(deploy))}ClusterIP   10.43.{rng.randint(1,254)}.{rng.randint(1,254)}   <none>        8080/TCP   4d\n"
        )

        entries[f"kubectl get events -n {ns}"] = "No resources found.\n"

        for p in pods:
            entries[f"kubectl describe pod {p['name']} -n {ns}"] = (
                f"Name:             {p['name']}\n"
                f"Namespace:        {ns}\n"
                f"Status:           Running\n"
                f"IP:               {p['ip']}\n"
                f"Containers:\n"
                f"  {deploy}:\n"
                f"    Image:          {image}\n"
                f"    State:          Running\n"
                f"    Ready:          True\n"
                f"    Restart Count:  0\n"
                f"Events:           <none>\n"
            )
            entries[f"kubectl logs {p['name']} -n {ns}"] = (
                f"[INFO] {deploy} started successfully\n"
                f"[INFO] Listening on :8080\n"
                f"[INFO] Health check passed\n"
            )

    return entries


def generate_noise_injected(
    cache: dict, dataset: list, scenario: str, seed: int, tag: str = "noise_injected"
) -> tuple[dict, list]:
    """Add healthy noise pods to the cache, keep dataset unchanged except metadata."""
    rng = random.Random(seed)
    new_cache = copy.deepcopy(cache)

    new_cache["_meta"]["case_type"] = tag
    new_cache["_meta"]["source_scenario"] = scenario

    noise_kubectl = _gen_noise_kubectl(rng, num_noise=rng.randint(2, 4))
    noise_ns = set()
    for key in noise_kubectl:
        m = re.search(r"-n\s+([\w-]+)", key)
        if m:
            noise_ns.add(m.group(1))

    if "kubectl" in new_cache:
        new_cache["kubectl"].update(noise_kubectl)

        ns_output = new_cache["kubectl"].get("kubectl get namespaces", "")
        if ns_output:
            for ns in sorted(noise_ns):
                ns_output = ns_output.rstrip() + f"\n{ns}{' '*(18-len(ns))}Active   4d\n"
            new_cache["kubectl"]["kubectl get namespaces"] = ns_output

    new_cache["_meta"]["noise_namespaces"] = sorted(noise_ns)

    new_dataset = []
    for entry in dataset:
        new_entry = copy.deepcopy(entry)
        new_entry["id"] = f"{entry['id']}--{tag}"
        if "metadata" in new_entry["input"]:
            new_entry["input"]["metadata"]["name"] = f"{entry['input']['metadata'].get('name', scenario)}--{tag}"
        if "metadata" in new_entry:
            new_entry["metadata"]["name"] = f"{entry['metadata'].get('name', scenario)}--{tag}"
            new_entry["metadata"]["variation_type"] = tag
            new_entry["metadata"]["source_scenario"] = scenario
        new_dataset.append(new_entry)

    return new_cache, new_dataset


# ---------------------------------------------------------------------------
# alert_rephrased: rewrite alerts with minimal/different detail level
# ---------------------------------------------------------------------------

ALERT_TEMPLATES_MINIMAL = [
    "Alert: {alert_name} firing in namespace {namespace}. Please investigate.",
    "{alert_name} triggered for {namespace}. Status: {severity}.",
    "Incident: {alert_name}\nNamespace: {namespace}\nSeverity: {severity}\nInvestigate immediately.",
]

ALERT_TEMPLATES_TERSE = [
    "{alert_name} - {namespace} - {severity}\n\nSomething is wrong. Check the affected resources.",
    "ALERT {alert_name} ({severity})\nAffected namespace: {namespace}\nNo additional context available.",
    "[{severity}] {alert_name} detected in {namespace}. Further details unavailable.",
]


def generate_alert_rephrased(
    cache: dict, dataset: list, scenario: str, seed: int, tag: str = "alert_rephrased"
) -> tuple[dict, list]:
    """Rewrite alerts with less detail to test agent robustness."""
    rng = random.Random(seed)
    new_cache = copy.deepcopy(cache)

    new_cache["_meta"]["case_type"] = tag
    new_cache["_meta"]["source_scenario"] = scenario

    templates = ALERT_TEMPLATES_MINIMAL + ALERT_TEMPLATES_TERSE

    new_dataset = []
    for entry in dataset:
        new_entry = copy.deepcopy(entry)
        new_entry["id"] = f"{entry['id']}--{tag}"

        meta = entry.get("input", {}).get("metadata", {})
        alert_name = meta.get("alert_name", "Unknown")
        namespace = meta.get("name", scenario).split("-", 1)[-1] if meta.get("name") else scenario
        severity = entry.get("input", {}).get("severity", "warning")

        ns_from_alert = ""
        alert_text = entry["input"].get("alert_text", "")
        ns_match = re.search(r"[Nn]amespace[:\s]+(\S+)", alert_text)
        if ns_match:
            ns_from_alert = ns_match.group(1).rstrip(",.")

        tmpl = rng.choice(templates)
        new_alert = tmpl.format(
            alert_name=alert_name,
            namespace=ns_from_alert or namespace,
            severity=severity.upper(),
        )
        new_entry["input"]["alert_text"] = new_alert

        if "metadata" in new_entry["input"]:
            new_entry["input"]["metadata"]["name"] = f"{meta.get('name', scenario)}--{tag}"
        if "metadata" in new_entry:
            new_entry["metadata"]["name"] = f"{entry['metadata'].get('name', scenario)}--{tag}"
            new_entry["metadata"]["variation_type"] = tag
            new_entry["metadata"]["source_scenario"] = scenario

        new_dataset.append(new_entry)

    return new_cache, new_dataset


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def generate_variation(
    scenario: str,
    variation_type: str,
    cache_dir: Path,
    dataset_dir: Path,
    output_cache_dir: Path,
    output_dataset_dir: Path,
    seed: int = 42,
    name_suffix: str | None = None,
) -> bool:
    cache_file = cache_dir / f"{scenario}.json"
    dataset_file = dataset_dir / f"{scenario}.json"

    if not cache_file.exists():
        logger.warning(f"Cache not found: {cache_file}")
        return False
    if not dataset_file.exists():
        logger.warning(f"Dataset not found: {dataset_file}")
        return False

    with open(cache_file) as fh:
        cache = json.load(fh)
    with open(dataset_file) as fh:
        dataset = json.load(fh)

    tag = variation_type if name_suffix is None else f"{variation_type}-{name_suffix}"

    if variation_type == "namespace_shifted":
        mapper = NameMapper(scenario, cache, dataset, seed=seed)
        new_cache = mutate_cache(cache, mapper, tag)
        new_dataset = mutate_dataset(dataset, mapper, tag, scenario)
        ns_map = mapper.mappings.get("namespaces", {})
        logger.info(f"  NS: {ns_map}, pods mutated: {len(mapper.mappings.get('pods', {}))}")

    elif variation_type == "noise_injected":
        new_cache, new_dataset = generate_noise_injected(cache, dataset, scenario, seed, tag)
        noise_ns = new_cache["_meta"].get("noise_namespaces", [])
        logger.info(f"  Injected noise namespaces: {noise_ns}")

    elif variation_type == "alert_rephrased":
        new_cache, new_dataset = generate_alert_rephrased(cache, dataset, scenario, seed, tag)
        logger.info(f"  Rephrased {len(new_dataset)} alerts")

    else:
        logger.error(f"Unknown variation type: {variation_type}")
        return False

    suffix = f"--{variation_type}" if name_suffix is None else f"--{variation_type}-{name_suffix}"
    output_name = f"{scenario}{suffix}"
    output_cache_dir.mkdir(parents=True, exist_ok=True)
    output_dataset_dir.mkdir(parents=True, exist_ok=True)

    new_cache["_meta"]["variation_name"] = output_name

    with open(output_cache_dir / f"{output_name}.json", "w") as f:
        json.dump(new_cache, f, indent=2, default=str)

    with open(output_dataset_dir / f"{output_name}.json", "w") as f:
        json.dump(new_dataset, f, indent=2, default=str)

    return True


def main():
    parser = argparse.ArgumentParser(description="Generate scenario variations")
    parser.add_argument("--type", required=True, choices=VARIATION_TYPES,
                        help="Variation type to generate")
    parser.add_argument("--scenario", help="Single scenario name")
    parser.add_argument("--all", action="store_true", help="Generate for all scenarios")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--suffix", default=None,
                        help="Name suffix appended to variation type (e.g. 's500' -> namespace_shifted-s500)")
    parser.add_argument("--cache-dir", default="cache", help="Input cache directory")
    parser.add_argument("--dataset-dir", default="data/datasets", help="Input dataset directory")
    parser.add_argument("--output-cache-dir", default="cache", help="Output cache directory")
    parser.add_argument("--output-dataset-dir", default="data/datasets", help="Output dataset directory")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    cache_dir = Path(args.cache_dir)
    dataset_dir = Path(args.dataset_dir)
    output_cache_dir = Path(args.output_cache_dir)
    output_dataset_dir = Path(args.output_dataset_dir)

    if args.all:
        scenarios = sorted(
            f.stem for f in cache_dir.glob("*.json")
            if "--" not in f.stem
        )
    elif args.scenario:
        scenarios = [args.scenario]
    else:
        parser.print_help()
        return

    ok, fail = 0, 0
    for i, scenario in enumerate(scenarios):
        scenario_seed = args.seed + i * 1000
        logger.info(f"Generating {args.type} for: {scenario} (seed={scenario_seed})")
        if generate_variation(scenario, args.type, cache_dir, dataset_dir,
                              output_cache_dir, output_dataset_dir,
                              seed=scenario_seed, name_suffix=args.suffix):
            ok += 1
        else:
            fail += 1

    logger.info(f"Done: {ok} generated, {fail} skipped")


if __name__ == "__main__":
    main()
