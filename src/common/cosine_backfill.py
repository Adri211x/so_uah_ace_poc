"""CLI utility to add cosine-similarity scores to an existing results JSON.

Reads a runner output file (a list of ``AgentResult`` dicts, typically
``tmp/test_3_results.json``), computes the embedding-based cosine similarity
for every entry that does not already have one, and writes the augmented file
back to disk *preserving every other field* (RCA output, golden entities,
literal score, judge verdict, ...). This lets the user enrich previous
evaluations without having to re-run the (expensive) agents.

Usage::

    uv run python -m src.common.cosine_backfill \\
        --input tmp/test_3_results.json \\
        --output tmp/test_3_results.json
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from src.common.cosine_similarity import compute_cosine_scores

logger = logging.getLogger(__name__)


def _augment_entry(entry: dict[str, Any], *, force: bool) -> dict[str, Any]:
    """Add a ``cosine_similarity`` field to one results entry.

    Args:
        entry: One ``AgentResult`` dict already loaded from JSON.
        force: When ``True``, overwrite an existing non-null ``cosine_similarity``.
            When ``False``, skip entries that already have a value.

    Returns:
        The same ``entry`` mapping with ``cosine_similarity`` populated. The
        function mutates the dict in place but returns it for convenience.
    """
    if not force and entry.get("cosine_similarity"):
        logger.debug(
            "Skipping sample %s; cosine_similarity already present.",
            entry.get("sample_id"),
        )
        return entry

    rca_output = entry.get("rca_output", "") or ""
    expected_output = entry.get("expected_output", "") or ""
    golden_entities = entry.get("golden_entities", []) or []

    if not expected_output.strip():
        logger.warning(
            "Sample %s has empty expected_output; storing cosine_similarity=null.",
            entry.get("sample_id"),
        )
        entry["cosine_similarity"] = None
        return entry

    try:
        result = compute_cosine_scores(
            rca_output=rca_output,
            expected_output=expected_output,
            golden_entities=list(golden_entities),
        )
    except Exception:
        logger.warning(
            "Cosine scorer failed for sample %s; storing cosine_similarity=null.",
            entry.get("sample_id"),
            exc_info=True,
        )
        entry["cosine_similarity"] = None
        return entry

    entry["cosine_similarity"] = result.model_dump()
    return entry


def backfill_results(
    input_path: Path,
    output_path: Path,
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Augment an existing results JSON with cosine-similarity scores.

    Args:
        input_path: JSON file to read (list of result dicts).
        output_path: JSON file to write. Can equal ``input_path``.
        force: When ``True`` recompute cosine similarities even for entries
            that already have a non-null ``cosine_similarity`` field.

    Returns:
        The augmented list of result dicts (same order as the input file).

    Raises:
        FileNotFoundError: If ``input_path`` does not exist.
        ValueError: If the file is not a JSON list of objects.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input results file not found: {input_path}")

    with input_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, list):
        raise ValueError(f"Results file {input_path!s} must contain a JSON list of result dicts.")

    augmented: list[dict[str, Any]] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise ValueError(f"Entry at index {index} in {input_path!s} must be a JSON object.")
        augmented.append(_augment_entry(entry, force=force))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(augmented, handle, indent=2, ensure_ascii=False)

    logger.info(
        "Backfilled cosine similarity for %d entries from %s to %s.",
        len(augmented),
        input_path,
        output_path,
    )
    return augmented


def _parse_args() -> argparse.Namespace:
    """Build the CLI argument parser for ``python -m src.common.cosine_backfill``."""
    parser = argparse.ArgumentParser(
        description=(
            "Add cosine-similarity scores to an existing runner results JSON. "
            "Existing fields are preserved."
        )
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the input results JSON file (e.g. tmp/test_3_results.json).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write the augmented JSON. Defaults to --input (in-place).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute cosine similarity even when an entry already has one.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint: parse flags and run ``backfill_results``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else input_path
    backfill_results(input_path=input_path, output_path=output_path, force=args.force)


if __name__ == "__main__":
    main()
