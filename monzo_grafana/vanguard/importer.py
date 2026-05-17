"""Import Vanguard ISA CSV data into account_balances (contributions_to_date)
and transactions (fund purchases, category='internal')."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from ..config import Config
from .parser import CashRow, InvestmentRow, parse_vanguard_csv

log = logging.getLogger(__name__)

_BALANCE_UPSERT_SQL = """
INSERT INTO account_balances (account_id, observed_at, balance, contributions_to_date)
VALUES ('vanguard_isa', %s, %s, %s)
ON CONFLICT (account_id, observed_at) DO UPDATE SET
    contributions_to_date = EXCLUDED.contributions_to_date
"""

_TX_UPSERT_SQL = """
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
    raw              = EXCLUDED.raw
"""


def _make_investment_id(r: InvestmentRow) -> str:
    digest = f"{r.investment_name}|{r.transaction_details}|{r.cost}"
    h = hashlib.sha1(digest.encode("utf-8")).hexdigest()[:12]
    return f"vanguard_isa_{r.date.strftime('%Y%m%d')}_{h}"


def _build_balance_rows(cash_rows: list[CashRow]) -> list[tuple[str, float, float]]:
    # ON CONFLICT only updates contributions_to_date; manual balance entries are preserved.
    deposit_dates: dict[str, float] = {}
    for r in cash_rows:
        if "deposit" in r.details.lower():
            deposit_dates[r.date.isoformat()] = r.contributions_to_date
    return [(d, contrib, contrib) for d, contrib in sorted(deposit_dates.items())]


def _build_tx_rows(inv_rows: list[InvestmentRow]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for r in inv_rows:
        tx_id = _make_investment_id(r)
        occurred_at = datetime.combine(r.date, datetime.min.time(), tzinfo=UTC)
        raw = json.dumps({
            "source": "vanguard_isa",
            "investment_name": r.investment_name,
            "transaction_details": r.transaction_details,
            "quantity": r.quantity,
            "price": r.price,
            "cost": r.cost,
        })
        rows.append((
            tx_id, occurred_at, -r.cost,
            r.investment_name, r.transaction_details,
            "investment", "internal", None, raw,
            "vanguard_isa", 1.0, None, None, None,
        ))
    return rows


def import_csv(cfg: Config, path: Path) -> None:
    log.info("--- Vanguard import start: %s ---", path)
    cash_rows, inv_rows = parse_vanguard_csv(path)
    log.info("Parsed %d cash row(s), %d investment row(s)", len(cash_rows), len(inv_rows))

    balance_rows = _build_balance_rows(cash_rows)
    tx_rows = _build_tx_rows(inv_rows)

    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.executemany(_BALANCE_UPSERT_SQL, balance_rows)
        cur.executemany(_TX_UPSERT_SQL, tx_rows)
        conn.commit()

    log.info("Upserted %d balance snapshot(s), %d investment transaction(s)",
             len(balance_rows), len(tx_rows))
