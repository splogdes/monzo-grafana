"""List the top merchants in each account by transaction count and total spend.

Useful for sanity-checking imports and for picking out the long-tail merchants
that would benefit most from a categories.yaml rule.
"""

from __future__ import annotations

from decimal import Decimal

import psycopg

from .config import Config


def _fetch(
    cfg: Config, account_id: str, limit: int, min_count: int
) -> list[tuple[str, int, Decimal, Decimal]]:
    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT merchant,
                   COUNT(*)                              AS n,
                   COALESCE(SUM(amount) FILTER (WHERE amount < 0), 0) AS spend,
                   COALESCE(SUM(amount) FILTER (WHERE amount > 0), 0) AS income
            FROM transactions
            WHERE account_id = %s
            GROUP BY merchant
            HAVING COUNT(*) >= %s
            ORDER BY n DESC, spend ASC
            LIMIT %s
            """,
            (account_id, min_count, limit),
        )
        return cur.fetchall()


def _print_table(account_id: str, rows: list[tuple[str, int, Decimal, Decimal]]) -> None:
    print(f"\n# Top merchants — {account_id}")
    if not rows:
        print("  (no rows)")
        return
    merchant_w = max(8, min(40, max(len(r[0]) for r in rows)))
    print(f"  {'merchant'.ljust(merchant_w)}  {'count':>6}  {'spend':>12}  {'income':>12}")
    print(f"  {'-' * merchant_w}  {'-' * 6}  {'-' * 12}  {'-' * 12}")
    for merchant, n, spend, income in rows:
        name = (merchant or "")[:merchant_w].ljust(merchant_w)
        print(f"  {name}  {n:>6}  {spend:>12.2f}  {income:>12.2f}")


def top_merchants(cfg: Config, limit: int, min_count: int) -> None:
    for account_id in ("monzo", "santander"):
        rows = _fetch(cfg, account_id, limit, min_count)
        _print_table(account_id, rows)
