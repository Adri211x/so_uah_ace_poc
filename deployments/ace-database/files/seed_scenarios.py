"""Seed scenarios and split_assignments from training files stored in S3/MinIO (DVC cache)."""

import json
import logging
import os
import sys

import boto3
import psycopg2

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

S3_ENDPOINT = os.environ["S3_ENDPOINT"]
S3_BUCKET = os.environ["S3_BUCKET"]
AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]

PG_HOST = os.environ["PG_HOST"]
PG_PORT = os.environ.get("PG_PORT", "5432")
PG_DB = os.environ.get("PG_DB", "ace")
PG_USER = os.environ["PG_USER"]
PG_PASSWORD = os.environ["PG_PASSWORD"]

# DVC cache prefix inside the bucket
DVC_PREFIX = os.environ.get("DVC_PREFIX", "dvc/files/md5")

# Training files: name -> DVC md5 hash (set via env as JSON)
TRAINING_FILES = json.loads(os.environ.get("TRAINING_FILES", "{}"))


def s3_client():
    """Create an S3 client for MinIO."""
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


def dvc_object_key(md5_hash: str) -> str:
    """Convert a DVC md5 hash to the S3 object key."""
    return f"{DVC_PREFIX}/{md5_hash[:2]}/{md5_hash[2:]}"


def download_training_file(client, md5_hash: str) -> dict:
    """Download and parse a training JSON from the DVC cache in S3."""
    key = dvc_object_key(md5_hash)
    logger.info("Downloading s3://%s/%s", S3_BUCKET, key)
    response = client.get_object(Bucket=S3_BUCKET, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def seed(conn, client) -> None:
    """Seed scenarios and split_assignments from all training files."""
    cur = conn.cursor()

    cur.execute("DELETE FROM cases.split_assignments")
    cur.execute("DELETE FROM cases.scenarios")
    logger.info("Cleared existing data")

    scenarios_seen = set()
    total_assignments = 0

    for filename, md5_hash in TRAINING_FILES.items():
        data = download_training_file(client, md5_hash)
        split_type = data["split_type"]
        logger.info("Processing %s (split_type=%s)", filename, split_type)

        if split_type == "stratified":
            folds = [{"fold_name": "default", "assignments": data["assignments"]}]
        else:
            folds = data.get("folds", [])

        for fold in folds:
            fold_name = fold.get("fold_name", "default")
            assignments = fold.get("assignments", [])

            for a in assignments:
                ref = a["id"]

                if ref not in scenarios_seen:
                    cur.execute(
                        """INSERT INTO cases.scenarios
                            (scenario_ref, scenario_id, family, root_cause_key,
                             variation_type, source_file, metadata)
                           VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                           ON CONFLICT (scenario_ref) DO NOTHING""",
                        (
                            ref,
                            a["base_scenario"],
                            a.get("root_cause_family"),
                            a.get("root_cause_key"),
                            a.get("variation_base"),
                            a.get("file"),
                            json.dumps({"variation": a.get("variation")}),
                        ),
                    )
                    scenarios_seen.add(ref)

                cur.execute(
                    """INSERT INTO cases.split_assignments
                        (scenario_ref, split_type, fold_name, split)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (scenario_ref, split_type, fold_name) DO NOTHING""",
                    (ref, split_type, fold_name, a["split"]),
                )
                total_assignments += 1

    dev_count = _create_dev_split(cur)
    total_assignments += dev_count

    conn.commit()
    cur.close()

    logger.info(
        "Seeded %d scenarios, %d split assignments (including %d dev)",
        len(scenarios_seen),
        total_assignments,
        dev_count,
    )


def _create_dev_split(cur) -> int:
    """Create a 'dev' split with 1 real (non-synthetic) case per base scenario.

    All selected cases are assigned to both train and test so they can be
    used in any direction during development.
    """
    cur.execute("""
        SELECT DISTINCT ON (scenario_id) scenario_ref, scenario_id
        FROM cases.scenarios
        WHERE variation_type = 'real'
        ORDER BY scenario_id, scenario_ref
    """)
    dev_scenarios = cur.fetchall()

    count = 0
    for ref, _ in dev_scenarios:
        cur.execute(
            """INSERT INTO cases.split_assignments
                (scenario_ref, split_type, fold_name, split)
               VALUES (%s, 'dev', 'default', 'dev')
               ON CONFLICT (scenario_ref, split_type, fold_name) DO NOTHING""",
            (ref,),
        )
        count += 1

    logger.info("Created dev split with %d scenarios", len(dev_scenarios))
    return count


def main() -> None:
    """Main entry point."""
    if not TRAINING_FILES:
        logger.error("TRAINING_FILES env is empty, nothing to seed")
        sys.exit(1)

    try:
        client = s3_client()
        conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASSWORD
        )
        seed(conn, client)
        conn.close()
    except Exception:
        logger.exception("Failed to seed scenarios")
        sys.exit(1)


if __name__ == "__main__":
    main()
