"""Import parsed Revolut records into the `transactions` and
`account_balances` tables.

Idempotent: row IDs are derived deterministically from
``description|amount|balance|intra_day_index`` so re-importing the same
file yields ON CONFLICT no-ops.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg

from ..config import Config
from ..db.snapshots import SNAPSHOT_UPSERT_SQL
from ..grafana_api import get_split_annotations
from ..rules import load_overrides, resolve_amortise_days, rule_to_columns
from .parser import RevolutRecord, parse_paths
from .staging_rules import load_rules, resolve

log = logging.getLogger(__name__)

_DEFAULT_CATEGORY: dict[str, str] = {
    "exchange": "internal",
}

_UPSERT_SQL = """
INSERT INTO transactions
    (id, occurred_at, amount, merchant, description,
     monzo_category, category, amortise_days, raw,
     account_id, my_share, group_id, offset_for_tx, offset_for_group)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    amount           = EXCLUDED.amount,
    merchant         = EXCLUDED.merchant,
    description      = EXCLUDED.description,
    monzo_category   = EXCLUDED.monzo_category,
    category         = EXCLUDED.category,
    amortise_days    = EXCLUDED.amortise_days,
    raw              = EXCLUDED.raw,
    my_share         = EXCLUDED.my_share,
    group_id         = EXCLUDED.group_id,
    offset_for_tx    = EXCLUDED.offset_for_tx,
    offset_for_group = EXCLUDED.offset_for_group
"""


def _intra_day_indices(records: Iterable[RevolutRecord]) -> list[int]:
    seen: dict[tuple[str, Any], int] = defaultdict(int)
    out: list[int] = []
    for r in records:
        key = (r.source_file, r.started_at.date())
        out.append(seen[key])
        seen[key] += 1
    return out


def _make_id(r: RevolutRecord, intra_day: int) -> str:
    digest_input = f"{r.description}|{r.amount}|{r.balance}|{intra_day}"
    h = hashlib.sha1(digest_input.encode("utf-8")).hexdigest()[:12]
    return f"revolut_{r.started_at.strftime('%Y%m%d')}_{h}"


def _raw_payload(r: RevolutRecord, intra_day: int) -> str:
    return json.dumps({
        "source": "revolut",
        "file": r.source_file,
        "file_index": r.file_index,
        "intra_day_index": intra_day,
        "type": r.type,
        "product": r.product,
        "started_at": r.started_at.isoformat(),
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "description": r.description,
        "amount": str(r.amount),
        "fee": str(r.fee),
        "balance": str(r.balance) if r.balance is not None else None,
    })


def import_records(cfg: Config, records: list[RevolutRecord]) -> None:
    if not records:
        log.info("No Revolut records to import")
        return

    overrides = load_overrides(cfg.categories_file)
    staging_rules = load_rules(cfg.revolut_rules_file)
    annotations = get_split_annotations(cfg)
    intra = _intra_day_indices(records)

    tx_rows: list[tuple[Any, ...]] = []
    balance_by_date: dict[Any, Decimal] = {}

    for r, idx in zip(records, intra, strict=True):
        tx_id = _make_id(r, idx)
        monzo_cat = r.type.lower().replace(" ", "_")
        fallback = _DEFAULT_CATEGORY.get(r.type.lower(), "unknown")
        merchant, category, override = resolve(
            tx_id, r.description, staging_rules, overrides, fallback_category=fallback
        )
        occurred_at = datetime.combine(r.started_at.date(), datetime.min.time(), tzinfo=UTC)
        days = resolve_amortise_days(occurred_at, override, annotations)
        share, group_id, off_tx, off_grp = rule_to_columns(override)
        tx_rows.append((
            tx_id, occurred_at, r.amount, merchant, r.description,
            monzo_cat, category, days, _raw_payload(r, idx),
            "revolut", share, group_id, off_tx, off_grp,
        ))
        # Keep the last seen balance per date (file is date-ascending)
        if r.balance is not None:
            balance_by_date[r.started_at.date()] = r.balance

    balance_rows = [
        ("revolut", d.isoformat(), str(bal), None)
        for d, bal in balance_by_date.items()
    ]

    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.executemany(_UPSERT_SQL, tx_rows)
        if balance_rows:
            cur.executemany(SNAPSHOT_UPSERT_SQL, balance_rows)
        conn.commit()

    log.info(
        "Upserted %d Revolut transaction(s) and %d balance snapshot(s)",
        len(tx_rows), len(balance_rows),
    )


def import_paths(cfg: Config, paths: list[str]) -> None:
    log.info("--- Revolut import start ---")
    records = parse_paths(paths)
    if not records:
        log.warning("No Revolut records found")
        return
    import_records(cfg, records)
    log.info("--- Revolut import done ---")
