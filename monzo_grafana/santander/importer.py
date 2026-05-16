"""Import parsed Santander records into the `transactions` and
`account_balances` tables.

Idempotent: row IDs are derived deterministically from
``date|description|amount|balance|intra_day_index`` so re-importing the same
file yields ON CONFLICT no-ops.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from ..config import Config
from ..db.snapshots import SNAPSHOT_UPSERT_SQL
from ..grafana_api import get_split_annotations
from ..rules import (
    load_overrides,
    resolve_amortise_days,
    rule_to_columns,
)
from .merchant import normalise_description
from .parser import SantanderRecord, parse_file
from .staging_rules import load_rules, resolve

log = logging.getLogger(__name__)

FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.txt$")

SANTANDER_UPSERT_SQL = """
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


def _intra_day_indices(records: Iterable[SantanderRecord]) -> list[int]:
    """Per-record count of prior records sharing the same date in the same file."""
    seen: dict[tuple[str, Any], int] = defaultdict(int)
    out: list[int] = []
    for r in records:
        key = (r.source_file, r.date)
        out.append(seen[key])
        seen[key] += 1
    return out


def _make_id(r: SantanderRecord, intra_day: int) -> str:
    digest_input = f"{r.description}|{r.amount}|{r.balance}|{intra_day}"
    h = hashlib.sha1(digest_input.encode("utf-8")).hexdigest()[:12]
    return f"santander_{r.date.strftime('%Y%m%d')}_{h}"


def _raw_payload(r: SantanderRecord, intra_day: int) -> str:
    return json.dumps(
        {
            "source": "santander",
            "file": r.source_file,
            "file_index": r.file_index,
            "intra_day_index": intra_day,
            "date": r.date.isoformat(),
            "description": r.description,
            "amount": str(r.amount),
            "balance": str(r.balance),
        }
    )


def import_records(cfg: Config, records: list[SantanderRecord]) -> None:
    if not records:
        log.info("No Santander records to import")
        return

    overrides = load_overrides(cfg.categories_file)
    staging_rules = load_rules(cfg.santander_rules_file)
    annotations = get_split_annotations(cfg)

    intra = _intra_day_indices(records)

    tx_rows: list[tuple[Any, ...]] = []
    # Balance per date: keep the *last* record seen for each date. Files are
    # date-descending, so iterating top-to-bottom and overwriting yields the
    # chronologically earliest balance on that date — which is wrong. Take the
    # *first* (top) entry per date instead (= most recent within the day).
    balance_by_date: dict[Any, Decimal] = {}
    for r, idx in zip(records, intra, strict=True):
        tx_id = _make_id(r, idx)
        description = normalise_description(r.description).title()
        merchant, category, override = resolve(
            tx_id, r.description, staging_rules, overrides, fallback_category="unknown"
        )
        occurred_at = datetime.combine(r.date, datetime.min.time(), tzinfo=UTC)
        days = resolve_amortise_days(occurred_at, override, annotations)
        share, group_id, off_tx, off_grp = rule_to_columns(override)
        tx_rows.append(
            (
                tx_id,
                occurred_at,
                r.amount,
                merchant,
                description,
                "unknown",
                category,
                days,
                _raw_payload(r, idx),
                "santander",
                share,
                group_id,
                off_tx,
                off_grp,
            )
        )
        if r.date not in balance_by_date:
            balance_by_date[r.date] = r.balance

    balance_rows = [
        ("santander", d.isoformat(), str(bal), None) for d, bal in balance_by_date.items()
    ]

    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.executemany(SANTANDER_UPSERT_SQL, tx_rows)
        if balance_rows:
            cur.executemany(SNAPSHOT_UPSERT_SQL, balance_rows)
        conn.commit()
    log.info(
        "Upserted %d Santander transaction(s) and %d balance snapshot(s)",
        len(tx_rows),
        len(balance_rows),
    )


def _resolve_paths(paths: list[str]) -> list[Path]:
    if not paths:
        return sorted(p for p in Path.cwd().iterdir() if p.is_file() and FILENAME_RE.match(p.name))
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(q for q in p.iterdir() if q.is_file() and FILENAME_RE.match(q.name)))
        elif p.is_file():
            out.append(p)
        else:
            log.warning("Skipping %s — not a file or directory", p)
    return out


def import_paths(cfg: Config, paths: list[str]) -> None:
    log.info("--- Santander import start ---")
    files = _resolve_paths(paths)
    if not files:
        log.warning("No Santander files matched")
        return
    log.info("Parsing %d file(s)", len(files))
    all_records: list[SantanderRecord] = []
    for f in files:
        records = parse_file(f)
        log.info("  %s -> %d record(s)", f.name, len(records))
        all_records.extend(records)
    import_records(cfg, all_records)
    log.info("--- Santander import done ---")
