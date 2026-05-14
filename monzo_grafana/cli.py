"""Argparse-based CLI for the poller.

Subcommands:
    auth          One-time OAuth token exchange
    poll          Single Monzo fetch + upsert, then exit
    retag         Re-apply categories.yaml rules to existing rows
    sync-groups   Push the YAML ``groups:`` section into the groups table
    sync-splits   Rebuild synthetic ledger rows from the YAML ``splits:`` section
    snapshot      Record an external-account balance snapshot
    schedule      Scheduled polling loop with HTTP trigger server (default)
"""

from __future__ import annotations

import argparse

from .config import Config
from .db.groups import sync_groups
from .db.snapshots import record_snapshot
from .db.splits import sync_splits
from .db.transactions import poll, retag
from .logging_setup import configure_logging
from .monzo.auth import do_auth
from .scheduler import run_schedule


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
    elif command == "schedule":
        run_schedule(cfg)
    else:  # pragma: no cover — argparse rejects unknown commands first
        parser.error(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
