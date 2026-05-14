"""Materialise YAML ``splits:`` into synthetic ledger rows."""

from __future__ import annotations

import logging

import psycopg

from ..config import Config
from ..rules import load_splits

log = logging.getLogger(__name__)

SPLIT_SUM_TOLERANCE = 0.01

SPLIT_UPSERT_SQL = """
INSERT INTO transactions
    (id, occurred_at, amount, merchant, description,
     monzo_category, category, amortise_days, raw,
     account_id, my_share, group_id, offset_for_tx, offset_for_group)
VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL,
        'ledger', 1.0, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    occurred_at      = EXCLUDED.occurred_at,
    amount           = EXCLUDED.amount,
    merchant         = EXCLUDED.merchant,
    description      = EXCLUDED.description,
    monzo_category   = EXCLUDED.monzo_category,
    category         = EXCLUDED.category,
    group_id         = EXCLUDED.group_id,
    offset_for_tx    = EXCLUDED.offset_for_tx,
    offset_for_group = EXCLUDED.offset_for_group
"""


def sync_splits(cfg: Config) -> set[str]:
    """Reconcile synthetic ledger rows with the YAML ``splits:`` section.

    For each valid split: mark the parent transaction as ``category='split'``
    (excluded from spend views) and rebuild its synthetic parts as rows on
    ``account_id='ledger'`` with deterministic ids ``{parent_id}__split_{N}``.

    Splits that fail the sum-to-parent invariant are skipped with a warning.
    Stale ledger rows are removed. Returns the set of successfully synced
    parent transaction ids so callers can skip them in the rule loop.
    """
    splits = load_splits(cfg.categories_file)
    valid_parents: set[str] = set()
    valid_ledger_ids: set[str] = set()

    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        for split in splits:
            parent_id = split["transaction_id"]
            cur.execute(
                "SELECT occurred_at, amount, merchant, description, monzo_category "
                "FROM transactions WHERE id = %s",
                (parent_id,),
            )
            row = cur.fetchone()
            if not row:
                log.warning("Split for %s: parent transaction not in DB — skipping", parent_id)
                continue
            occurred_at, parent_amount, merchant, description, monzo_category = row

            parts_sum = sum(p["amount"] for p in split["parts"])
            if abs(parts_sum - float(parent_amount)) > SPLIT_SUM_TOLERANCE:
                log.warning(
                    "Split for %s: parts sum %.2f != parent %.2f, skipping",
                    parent_id, parts_sum, float(parent_amount),
                )
                continue

            cur.execute(
                "UPDATE transactions SET category='split' WHERE id=%s "
                "AND category IS DISTINCT FROM 'split'",
                (parent_id,),
            )

            for n, part in enumerate(split["parts"], start=1):
                split_id = f"{parent_id}__split_{n}"
                valid_ledger_ids.add(split_id)
                cur.execute(SPLIT_UPSERT_SQL, (
                    split_id,
                    occurred_at,
                    part["amount"],
                    merchant,
                    part["note"] or description or "",
                    monzo_category,
                    part["category"] or "general",
                    part["group"],
                    part["offset_for_tx"],
                    part["offset_for_group"],
                ))
            valid_parents.add(parent_id)

        cur.execute("SELECT id FROM transactions WHERE account_id='ledger'")
        db_ledger = {r[0] for r in cur.fetchall()}
        stale = db_ledger - valid_ledger_ids
        if stale:
            cur.execute("DELETE FROM transactions WHERE id = ANY(%s)", (list(stale),))
            log.info("Removed %d stale ledger row(s)", len(stale))

        conn.commit()

    if splits:
        log.info("Synced %d split(s) (%d valid)", len(splits), len(valid_parents))
    return valid_parents
