"""Manual balance snapshots for external accounts (ISAs, investments)."""

from __future__ import annotations

import logging

import psycopg

from ..config import Config

log = logging.getLogger(__name__)

SNAPSHOT_UPSERT_SQL = """
INSERT INTO account_balances (account_id, observed_at, balance, contributions_to_date)
VALUES (%s, %s, %s, %s)
ON CONFLICT (account_id, observed_at) DO UPDATE SET
    balance               = EXCLUDED.balance,
    contributions_to_date = EXCLUDED.contributions_to_date
"""


def record_snapshot(
    cfg: Config,
    account_id: str,
    observed_at: str,
    balance: float,
    contributions: float | None,
) -> None:
    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(SNAPSHOT_UPSERT_SQL, (account_id, observed_at, balance, contributions))
        conn.commit()
    contrib_suffix = f" (contrib £{contributions:.2f})" if contributions is not None else ""
    log.info("Snapshot recorded: %s @ %s = £%.2f%s",
             account_id, observed_at, balance, contrib_suffix)
