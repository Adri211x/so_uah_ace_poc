"""
Build golden_entities for each scenario and inject them into all dataset files.

Golden entities are 3 key terms per base scenario selected with quantitative
criteria to evaluate LLM agent root cause analysis quality:

Selection criteria (applied per entity):
  1. NOT present in the alert text (agent must discover it through investigation)
  2. Length >= 10 chars (Levenshtein 0.95 requires ~20+ chars for 1-edit tolerance;
     shorter strings demand near-exact match, which is still useful)
  3. High discriminative power (unique or rare across the 19 base scenarios)
  4. Backed by MCP evidence (the raw data exists in kubectl/logs/traces outputs)
  5. Natural in a correct RCA response (a competent SRE agent would write this)

Entity taxonomy per scenario:
  E1: Specific technical evidence — exact error message, config value, or status
      discovered via MCP tools (kubectl describe, logs, traces)
  E2: Diagnostic synthesis — phrase proving the agent connected evidence to cause
  E3: Root cause identifier — the nature/class of the underlying problem

Evaluated via normalized Levenshtein ratio >= 0.95 against agent output.
"""

import json
import os
import re
from pathlib import Path

DATASET_DIR = Path("data/datasets")

# fmt: off
GOLDEN_ENTITIES: dict[str, list[str]] = {
    # crashloop: container cmd has python3 with unterminated string → /bin/sh: syntax error
    # MCP evidence: "unterminated quoted string" (3x kubectl/logs), "python3 -c" (4x), "/bin/sh: 1:" (3x)
    # Disc: "unterminated quoted string" unique (0 other), "python3 -c" unique (0 other)
    "kubernetes-crashloop": [
        "unterminated quoted string",   # E1: exact error from shell output (26c, disc=1.0, 3x MCP)
        "invalid python3 command",      # E2: what the agent finds in container spec (23c)
        "nginx image",                  # E3: the image used with wrong command (11c, in all EOs)
    ],
    # data-pipeline: broker queue at max capacity (100 msgs), consumer not reading
    # MCP evidence: "broker-service" (251x), "8002" port (164x), "client" (77x), "full" (49x)
    # The queue/capacity terms exist only in EO, but agent would synthesize from logs
    "kubernetes-data-pipeline": [
        "broker-service",               # E1: the specific K8s service name (14c, disc=1.0, 251x MCP)
        "queue is full",                # E2: diagnostic conclusion from log analysis (13c, disc=1.0)
        "maximum capacity",             # E3: root cause classification (16c, disc=1.0)
    ],
    # elk-bad-port: Redis on 6380 but Logstash configured for default 6379
    # MCP evidence: "6380" (14x services/pods), "6379" (92x logstash configs), "logstash2-pipeline" (5x)
    # Disc: port values shared with elk-mix-errors only
    "kubernetes-elk-bad-port": [
        "port 6380",                    # E1: the actual Redis port found in k8s service (9c, 14x MCP as "6380")
        "port 6379",                    # E2: the wrong port in Logstash config (9c, 92x MCP as "6379")
        "logstash2-pipeline",           # E3: the misconfigured ConfigMap (18c, disc=0.2, 5x MCP)
    ],
    # elk-commented-output: elasticsearch output block commented with # in Logstash2 pipeline
    # MCP evidence: "logstash2-pipeline" (5x ConfigMap), "#" (252x in configs)
    # Disc: "commented out" unique, "elasticsearch output" in 2 others
    "kubernetes-elk-commented-output": [
        "output commented",             # E1: describes what was found in ConfigMap (16c, disc=1.0)
        "logstash2-pipeline",           # E2: the specific ConfigMap name (18c, 5x MCP)
        "elasticsearch output",         # E3: what's broken — the output section (20c)
    ],
    # elk-fake: FALSE ALARM — all components working correctly
    # MCP evidence: "app-docs-ingestion" (73x), "configured" (4x), "running" (106x)
    # Agent must prove it checked the full pipeline and found no issues
    "kubernetes-elk-fake": [
        "false_alarm",                  # E1: the correct diagnosis itself (11c, disc=1.0)
        "properly configured",          # E2: conclusion from checking configs (19c, disc=1.0)
        "app-docs-ingestion",           # E3: key service in pipeline the agent must trace (18c, 73x MCP)
    ],
    # elk-malformed: Logstash1 pipeline has "nput" instead of "input" (typo)
    # MCP evidence: "logstash1-pipeline" (5x ConfigMap)
    # Disc: "nput" shared with mix-errors, "logstash1-pipeline" shared with 4 others
    "kubernetes-elk-malformed-pipeline": [
        "pipeline configuration",       # E1: identifies the ConfigMap section with the error (22c, in ALL EOs)
        "config_error",                 # E2: root cause code (12c, disc=1.0, in ALL EOs)
        "'nput' instead",               # E3: the exact typo evidence (15c, in 5/6 EOs, disc=1.0)
    ],
    # elk-mix: BOTH port mismatch on Redis AND syntax error (nput) in Logstash1
    # MCP evidence: "6380" (14x), "nput" (80x), "logstash1-pipeline" (5x)
    # Agent must find TWO distinct issues
    "kubernetes-elk-mix-errors": [
        "port mismatch",                # E1: one of the two root causes (13c, disc=1.0)
        "'nput' instead",               # E2: the typo evidence from ConfigMap (14c, 6/6 EOs)
        "multiple_config_errors",       # E3: root cause code — must identify BOTH issues (22c, disc=1.0)
    ],
    # image-pull: deployment refs non-existent image from invalid registry → ImagePullBackOff
    # MCP evidence: "registry" (7x), container image ref visible in kubectl describe
    # Disc: "invalid_image_reference" unique, "non-existent container image" unique
    "kubernetes-image-pull": [
        "invalid_image_reference",      # E1: root cause code (23c, disc=1.0)
        "non-existent container image", # E2: diagnostic conclusion (28c, disc=1.0)
        "invalid registry",             # E3: the specific cause — bad registry URL (16c, disc=1.0)
    ],
    # locked-server: previous job didn't release lock → subsequent jobs fail
    # MCP evidence: "lock-server" (54x), "acquire" (10x), "release" (2x)
    # Agent must trace logs to find the lock was never released
    "kubernetes-locked-server": [
        "lock-server",                  # E1: the specific service holding the lock (11c, 54x MCP)
        "acquire the lock",             # E2: what the failing jobs try to do (16c, 10x MCP as "acquire")
        "previous job",                 # E3: root cause — identifies temporal causation (12c, disc=1.0)
    ],
    # oomkilled: containers exceed memory limits → OOM killer terminates → exit 137
    # MCP evidence: "OOMKilled" (55x pod status), "memory" (47x), "137" (6x)
    # Very high signal in kubectl describe output
    "kubernetes-oomkilled": [
        "OOMKilled",                    # E1: the exact K8s termination reason (9c, 55x MCP, disc=1.0)
        "memory limit",                 # E2: what's exceeded (12c, 47x MCP as "memory" + "limit")
        "Exit Code 137",                # E3: specific OOM kill signal (13c, 6x MCP as "137")
    ],
    # otel-payment: feature flag paymentFailure set → PaymentService returns "Invalid token"
    # MCP evidence: "Invalid token" (8x traces), "paymentFailure" (222x), "feature flag" (5x)
    # This variant HAS flagd data
    "kubernetes-otel-demo-paymentFailure": [
        "Invalid token",                # E1: the error message from payment service (13c, 8x MCP)
        "paymentFailure",               # E2: the feature flag name (14c, 222x MCP)
        "feature flag",                 # E3: root cause mechanism — flagd toggle (12c, 5x MCP, disc=1.0)
    ],
    # otel-payment-no-flagd: same failure but NO flagd data → agent must infer from traces only
    # MCP evidence: "Invalid token" (8x), "paymentFailure" (175x)
    "kubernetes-otel-demo-paymentFailure-no-flagd": [
        "Invalid token",                # E1: error from trace spans (13c, 8x MCP)
        "paymentFailure",               # E2: pattern in trace data (14c, 175x MCP)
        "payment service",              # E3: identifies which service is failing (15c)
    ],
    # otel-payment-no-flagd-no-kube-logs: minimal MCP data, traces only
    # MCP evidence: "paymentFailure" (2x), very limited data (3.6KB total)
    "kubernetes-otel-demo-paymentFailure-no-flagd-no-kube-logs": [
        "Invalid token",                # E1: the error — must be inferred from limited traces (13c)
        "paymentFailure",               # E2: the flag pattern visible in traces (14c, 2x MCP)
        "payment service",              # E3: the failing service (15c)
    ],
    # pending-pod: PVC refs non-existent volume → pod stuck Pending
    # MCP evidence: "volume" (66x), "Pending" (46x), "FailedScheduling" (10x)
    "kubernetes-pending-pod": [
        "FailedScheduling",             # E1: the K8s event reason (16c, 10x MCP, disc from events)
        "non-existent volume",          # E2: diagnostic conclusion (19c, disc=1.0)
        "PersistentVolumeClaim",        # E3: the K8s resource type involved (21c)
    ],
    # probes: liveness/readiness probes check non-existent path → pods restart
    # MCP evidence: "liveness" (20x), "readiness" (24x), "probe failed" (14x), "httpGet" (24x)
    "kubernetes-probes": [
        "liveness probe",               # E1: the specific probe type failing (14c, 20x MCP)
        "readiness probe",              # E2: the other probe type failing (15c, 24x MCP)
        "non-existent path",            # E3: root cause — the path doesn't exist (17c, 34x MCP)
    ],
    # service-routing: Service selector app=web doesn't match Deployment label app=nginx
    # MCP evidence: "app=nginx" (1x label), "selector" (25x), "mismatch" (3x)
    "kubernetes-service-routing": [
        "selector mismatch",            # E1: the diagnostic conclusion (17c, 3x MCP as parts)
        "app=nginx",                    # E2: the actual deployment label value (9c, 1x MCP)
        "connection refused",           # E3: the symptom that proves traffic fails (18c, 2x MCP)
    ],
    # service-no-endpoints: node selector "production-gpu" doesn't exist → pods unschedulable
    # MCP evidence: "Unschedulable" (3x), "PodScheduled" (21x), "environment:" (8x)
    "kubernetes-service-with-no-endpoints": [
        "production-gpu",               # E1: the non-existent node label value (14c, disc=1.0)
        "Unschedulable",                # E2: K8s condition proving scheduling failure (13c, 3x MCP)
        "node selector",                # E3: the mechanism causing the issue (13c, 2x MCP)
    ],
    # tracing-tree: database service has simulated 5s delay → high frontend response time
    # MCP evidence: "database" (147x), "backend" (147x), "delay" (42x), "Tempo" (71x)
    "kubernetes-tracing-tree": [
        "database service",             # E1: the service causing the latency (16c, 147x MCP)
        "long_running_query",           # E2: the exact Tempo span name (18c, unique evidence from traces)
        "5-second delay",               # E3: the specific delay duration (14c, disc=1.0)
    ],
    # unconfigured-server: DATABASE_URL=localhost → app can't connect → exit code 2
    # MCP evidence: "Terminated" (6x), "unreachable" (21x), "ping" (20x), "Exit Code" (2x)
    "kubernetes-unconfigured-server": [
        "unreachable",                  # E1: the connection failure evidence (11c, 21x MCP)
        "database connection",          # E2: what's broken (19c, disc=1.0)
        "Exit Code 2",                  # E3: the specific non-zero exit (11c, 2x MCP)
    ],
}
# fmt: on


def _base_scenario(filename: str) -> str:
    name = filename.replace(".json", "")
    return name.split("--")[0] if "--" in name else name


def validate_entities():
    """Verify no golden entity appears in any alert text of its scenario."""
    errors = []
    for f in sorted(os.listdir(DATASET_DIR)):
        if not f.endswith(".json") or "--" in f:
            continue
        scenario = f.replace(".json", "")
        entities = GOLDEN_ENTITIES.get(scenario, [])
        if not entities:
            errors.append(f"No golden entities defined for {scenario}")
            continue

        with open(DATASET_DIR / f) as fh:
            ds = json.load(fh)
        all_alerts = " ".join(e["input"]["alert_text"] for e in ds).lower()

        for entity in entities:
            if entity.lower() in all_alerts:
                errors.append(
                    f"{scenario}: golden entity '{entity}' found in alert text"
                )
    return errors


def inject_entities():
    """Add golden_entities to every dataset entry (real and variations)."""
    updated = 0
    for f in sorted(os.listdir(DATASET_DIR)):
        if not f.endswith(".json"):
            continue
        scenario = _base_scenario(f)
        entities = GOLDEN_ENTITIES.get(scenario)
        if entities is None:
            print(f"  SKIP {f}: no entities for {scenario}")
            continue

        path = DATASET_DIR / f
        with open(path) as fh:
            ds = json.load(fh)
        changed = False

        for entry in ds:
            effective = entities
            if "namespace_shifted" in f:
                cache_path = Path("cache") / f
                if cache_path.exists():
                    with open(cache_path) as fh:
                        cache = json.load(fh)
                    ns_map = cache.get("_meta", {}).get("mappings", {}).get("namespaces", {})
                    if ns_map:
                        effective = _apply_ns_map(entities, ns_map)

            if entry.get("golden_entities") != effective:
                entry["golden_entities"] = effective
                changed = True

        if changed:
            with open(path, "w") as fh:
                json.dump(ds, fh, indent=2, default=str)
            updated += 1

    return updated


def _apply_ns_map(entities: list[str], ns_map: dict[str, str]) -> list[str]:
    """Apply namespace mappings to golden entities for namespace_shifted variants."""
    result = []
    for entity in entities:
        mapped = entity
        for old_ns, new_ns in sorted(ns_map.items(), key=lambda x: -len(x[0])):
            mapped = mapped.replace(old_ns, new_ns)
        result.append(mapped)
    return result


def main():
    print("Validating golden entities...")
    errors = validate_entities()
    if errors:
        print(f"\nVALIDATION FAILED ({len(errors)} errors):")
        for e in errors:
            print(f"  - {e}")
        return

    print("  All entities validated (not in alert text)")
    print(f"\nInjecting golden_entities into {DATASET_DIR}...")
    updated = inject_entities()
    print(f"  Updated {updated} dataset files")

    total_files = len([f for f in os.listdir(DATASET_DIR) if f.endswith(".json")])
    print(f"\nDone. {total_files} total dataset files, {updated} updated.")


if __name__ == "__main__":
    main()
