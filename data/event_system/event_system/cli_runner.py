"""CLI for exploring splits, folds, and cases without writing Python code.

Usage::

    event-runner list-splits
    event-runner list-folds leave_family_out
    event-runner summary stratified default
    event-runner export --split stratified --fold default --partition test -o cases.json
    event-runner serve --split dev
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from event_system.case_provider import CaseProvider, DatabaseCaseProvider, LocalCaseProvider

logger = logging.getLogger(__name__)


def _resolve_provider(args: argparse.Namespace) -> CaseProvider:
    """Build a CaseProvider from CLI args or environment."""
    db_url = args.db_url or os.environ.get("ACE_DATABASE_URL")
    if db_url:
        return DatabaseCaseProvider(db_url=db_url)

    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).parent.parent
    return LocalCaseProvider(data_dir=data_dir)


def _resolve_dirs(args: argparse.Namespace) -> tuple[Path, Path]:
    """Resolve cache and dataset directories."""
    base = Path(args.data_dir) if args.data_dir else Path(__file__).parent.parent
    cache_dir = base / "cache"
    dataset_dir = base / "data" / "datasets"
    return cache_dir, dataset_dir


def cmd_list_splits(args: argparse.Namespace) -> None:
    """List available split types."""
    provider = _resolve_provider(args)
    splits = provider.list_split_types()
    if not splits:
        print("No splits found. Check your connection or run 'dvc pull'.")
        return

    print("Available split types:")
    for st in splits:
        folds = provider.list_folds(st)
        suffix = "s" if len(folds) != 1 else ""
        print(f"  {st:<25s} ({len(folds)} fold{suffix})")


def cmd_list_folds(args: argparse.Namespace) -> None:
    """List folds for a given split type."""
    provider = _resolve_provider(args)
    folds = provider.list_folds(args.split_type)
    if not folds:
        print(f"No folds found for split_type={args.split_type!r}")
        return

    print(f"Folds for '{args.split_type}':")
    for fold in folds:
        counts = provider.summary(args.split_type, fold)
        parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"  {fold:<55s} {parts}")


def cmd_summary(args: argparse.Namespace) -> None:
    """Show case counts for a split/fold."""
    provider = _resolve_provider(args)
    counts = provider.summary(args.split_type, args.fold_name)
    if not counts:
        print(f"No data found for split_type={args.split_type!r}, fold={args.fold_name!r}")
        return

    total = sum(counts.values())
    print(f"Summary: {args.split_type} / {args.fold_name}")
    for partition, count in sorted(counts.items()):
        print(f"  {partition:<10s} {count} cases")
    print(f"  {'total':<10s} {total} cases")


def cmd_export(args: argparse.Namespace) -> None:
    """Export cases for a split/fold/partition to JSON."""
    provider = _resolve_provider(args)
    cases = provider.get_cases(args.split, args.fold, args.partition)
    if not cases:
        print("No cases found.", file=sys.stderr)
        sys.exit(1)

    output = [
        {
            "scenario_ref": c.scenario_ref,
            "scenario_id": c.scenario_id,
            "family": c.family,
            "root_cause_key": c.root_cause_key,
            "variation_type": c.variation_type,
            "source_file": c.source_file,
            "split": c.split,
        }
        for c in cases
    ]

    text = json.dumps(output, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text)
        print(f"Exported {len(output)} cases to {args.output}")
    else:
        print(text)


def cmd_serve(args: argparse.Namespace) -> None:
    """Start mock MCP and iterate through cases interactively."""
    from event_system.split_runner import SplitRunner

    provider = _resolve_provider(args)
    cache_dir, dataset_dir = _resolve_dirs(args)

    runner = SplitRunner(
        provider=provider,
        cache_dir=cache_dir,
        dataset_dir=dataset_dir,
    )

    split_type = args.split
    fold_name = args.fold
    partition = args.partition

    cases = runner.get_cases(split_type, fold_name, partition)
    if not cases:
        print(f"No cases found for {split_type}/{fold_name}/{partition}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  Split Runner - Interactive Mode")
    print("=" * 60)
    print(f"  Split:     {split_type}")
    print(f"  Fold:      {fold_name}")
    print(f"  Partition:  {partition}")
    print(f"  Cases:     {len(cases)}")
    print("=" * 60)

    try:
        for run_case in runner.iter_cases(split_type, fold_name, partition):
            print(f"\n--- Case {run_case.index + 1}/{run_case.total} ---")
            print(f"  Ref:       {run_case.case.scenario_ref}")
            print(f"  Scenario:  {run_case.case.scenario_id}")
            print(f"  Family:    {run_case.case.family}")
            print(f"  Source:    {run_case.case.source_file}")

            if run_case.input:
                alert = run_case.input.get("alert_text", "")
                if alert:
                    preview = alert[:100] + "..." if len(alert) > 100 else alert
                    print(f"  Alert:     {preview}")

            print("  Endpoints:")
            for name, url in run_case.endpoints.as_dict().items():
                print(f"    {name:<15s} {url}")

            try:
                input("\n  Press Enter for next case (Ctrl+C to stop)...")
            except EOFError:
                break
    except KeyboardInterrupt:
        print("\nStopped.")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="event-runner",
        description="Explore and run ACE evaluation splits",
    )
    parser.add_argument(
        "--db-url", default=None,
        help="PostgreSQL URL (or set ACE_DATABASE_URL env var). "
             "If not set, uses local training JSON files.",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="Path to data/event_system/ directory (default: auto-detect)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-splits", help="List available split types")

    p_folds = sub.add_parser("list-folds", help="List folds for a split type")
    p_folds.add_argument("split_type", help="Split type to list folds for")

    p_summary = sub.add_parser("summary", help="Show case counts for a split/fold")
    p_summary.add_argument("split_type", help="Split type")
    p_summary.add_argument("fold_name", nargs="?", default="default", help="Fold name")

    p_export = sub.add_parser("export", help="Export cases to JSON")
    p_export.add_argument("--split", required=True, help="Split type")
    p_export.add_argument("--fold", default="default", help="Fold name")
    p_export.add_argument("--partition", default="test", help="Partition (train/test/dev)")
    p_export.add_argument("-o", "--output", default=None, help="Output file (default: stdout)")

    p_serve = sub.add_parser("serve", help="Start mock and iterate cases interactively")
    p_serve.add_argument("--split", default="dev", help="Split type")
    p_serve.add_argument("--fold", default="default", help="Fold name")
    p_serve.add_argument("--partition", default="test", help="Partition (train/test/dev)")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    commands = {
        "list-splits": cmd_list_splits,
        "list-folds": cmd_list_folds,
        "summary": cmd_summary,
        "export": cmd_export,
        "serve": cmd_serve,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
