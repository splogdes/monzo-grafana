"""Argparse-based CLI for the poller.

Subcommands:
    auth              One-time OAuth token exchange
    poll              Single Monzo fetch + upsert, then exit
    retag             Re-apply categories.yaml rules to existing rows
    sync-groups       Push the YAML ``groups:`` section into the groups table
    sync-splits       Rebuild synthetic ledger rows from the YAML ``splits:`` section
    snapshot          Record an external-account balance snapshot
    import-santander  Import Santander statement exports
    import-vanguard   Import Vanguard ISA CSV export
    top-merchants     List the top merchants per account
    cluster-merchants Group Santander variants and cross-reference Monzo
    schedule          Scheduled polling loop with HTTP trigger server (default)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .cluster_merchants import cluster_merchants
from .config import Config
from .db.groups import sync_groups
from .db.snapshots import record_snapshot
from .db.splits import sync_splits
from .db.transactions import poll, retag
from .dry_run_rules import dry_run
from .logging_setup import configure_logging
from .monzo.auth import do_auth
from .revolut.importer import import_paths as import_revolut_paths
from .santander.importer import import_paths as import_santander_paths
from .scheduler import run_schedule
from .top_merchants import top_merchants
from .vanguard.importer import import_csv as import_vanguard_csv


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="poller", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("auth", help="One-time OAuth token exchange")
    sub.add_parser("poll", help="Single Monzo fetch + upsert")
    sub.add_parser("retag", help="Re-apply categories.yaml rules to existing rows")
    sub.add_parser("sync-groups", help="Push YAML groups: into the groups table")
    sub.add_parser("sync-splits", help="Rebuild ledger rows from YAML splits:")
    sub.add_parser("schedule", help="Scheduled polling loop (default)")

    snap = sub.add_parser("snapshot", help="Record an external-account balance snapshot")
    snap.add_argument("account_id")
    snap.add_argument("observed_at", help="YYYY-MM-DD")
    snap.add_argument("balance", type=float)
    snap.add_argument("contributions", type=float, nargs="?", default=None)

    imp = sub.add_parser("import-santander", help="Import Santander statement exports")
    imp.add_argument("paths", nargs="*", help="Files or directories (default: cwd)")

    imp_v = sub.add_parser("import-vanguard", help="Import Vanguard ISA CSV export")
    imp_v.add_argument("csv_path", help="Path to Vanguard ISA CSV file")

    imp_r = sub.add_parser("import-revolut", help="Import Revolut CSV export")
    imp_r.add_argument("paths", nargs="*", help="Files or directories (default: cwd)")

    top = sub.add_parser(
        "top-merchants",
        help="List the top merchants per account by transaction count",
    )
    top.add_argument("--limit", type=int, default=30, help="Rows per account (default: 30)")
    top.add_argument(
        "--min-count", type=int, default=1, help="Skip merchants with fewer rows (default: 1)"
    )

    clu = sub.add_parser(
        "cluster-merchants",
        help="Group Santander merchants by shared tokens and cross-reference Monzo",
    )
    clu.add_argument("--min-rows", type=int, default=3, help="Skip clusters with fewer rows")
    clu.add_argument("--min-spend", type=float, default=20.0, help="Skip clusters below £N spend")
    clu.add_argument("--output", help="Write report to file instead of stdout")

    dry = sub.add_parser(
        "dry-run-rules",
        help="Apply the Santander rules file to live data and report what each rule matches",
    )
    dry.add_argument(
        "--file", default="data/santander_rules.yaml", help="Path to the rules file"
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)
    cfg = Config.from_env()

    command = args.command or "schedule"
    if command == "auth":
        do_auth(cfg)
    elif command == "poll":
        poll(cfg)
    elif command == "retag":
        retag(cfg)
    elif command == "sync-groups":
        sync_groups(cfg)
    elif command == "sync-splits":
        sync_splits(cfg)
    elif command == "snapshot":
        record_snapshot(cfg, args.account_id, args.observed_at, args.balance, args.contributions)
    elif command == "import-santander":
        import_santander_paths(cfg, args.paths)
    elif command == "import-vanguard":
        import_vanguard_csv(cfg, Path(args.csv_path))
    elif command == "import-revolut":
        import_revolut_paths(cfg, args.paths)
    elif command == "top-merchants":
        top_merchants(cfg, args.limit, args.min_count)
    elif command == "cluster-merchants":
        cluster_merchants(cfg, args.min_rows, args.min_spend, args.output)
    elif command == "dry-run-rules":
        dry_run(cfg, args.file)
    elif command == "schedule":
        run_schedule(cfg)
    else:  # pragma: no cover — argparse rejects unknown commands first
        parser.error(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
