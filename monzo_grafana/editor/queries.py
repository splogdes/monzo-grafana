"""Read-only Postgres queries powering the editor's autocomplete dropdowns.

Every query is best-effort: a failed DB call yields an empty result rather
than a 500, so the editor stays usable while Postgres is down.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import psycopg

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 2


def _run[T](dsn: str | None, fn: Callable[[psycopg.Cursor[Any]], T], empty: T) -> T:
    if not dsn:
        return empty
    try:
        with psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn, conn.cursor() as cur:
            return fn(cur)
    except psycopg.Error as exc:
        log.warning("DB query failed: %s", exc)
        return empty


def fetch_db_categories(dsn: str | None) -> list[str]:
    def q(cur: psycopg.Cursor[Any]) -> list[str]:
        cur.execute("SELECT DISTINCT category FROM transactions ORDER BY 1")
        return [r[0] for r in cur.fetchall() if r[0]]

    return _run(dsn, q, [])


def fetch_db_groups(dsn: str | None) -> list[dict[str, Any]]:
    def q(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
        cur.execute(
            "SELECT id, kind, name, starts_at, ends_at, budget, amortise "
            "FROM groups ORDER BY id"
        )
        return [
            {
                "id": r[0], "kind": r[1], "name": r[2],
                "starts_at": r[3], "ends_at": r[4],
                "budget": r[5], "amortise": r[6],
            }
            for r in cur.fetchall()
        ]

    return _run(dsn, q, [])


def fetch_merchants(dsn: str | None, limit: int = 1000) -> list[str]:
    def q(cur: psycopg.Cursor[Any]) -> list[str]:
        cur.execute(
            "SELECT DISTINCT merchant FROM transactions "
            "WHERE merchant IS NOT NULL AND merchant <> '' "
            "ORDER BY merchant LIMIT %s",
            (limit,),
        )
        return [r[0] for r in cur.fetchall()]

    return _run(dsn, q, [])


def fetch_recent_transactions(dsn: str | None, limit: int = 200) -> list[dict[str, Any]]:
    def q(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
        cur.execute(
            "SELECT id, occurred_at::date, merchant, amount "
            "FROM transactions "
            "ORDER BY occurred_at DESC LIMIT %s",
            (limit,),
        )
        return [
            {"id": r[0], "date": r[1], "merchant": r[2], "amount": float(r[3])}
            for r in cur.fetchall()
        ]

    return _run(dsn, q, [])


def fetch_recent_outgoings(dsn: str | None, limit: int = 200) -> list[dict[str, Any]]:
    def q(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
        cur.execute(
            "SELECT id, occurred_at::date, merchant, amount "
            "FROM transactions WHERE amount < 0 "
            "ORDER BY occurred_at DESC LIMIT %s",
            (limit,),
        )
        return [
            {"id": r[0], "date": r[1], "merchant": r[2], "amount": float(r[3])}
            for r in cur.fetchall()
        ]

    return _run(dsn, q, [])


def search_outgoings(
    dsn: str | None, query: str, limit: int = 20
) -> list[dict[str, Any]]:
    """Filtered outgoings for live autocomplete.

    Empty ``query`` falls back to the most-recent slice so the dropdown is
    useful on first focus.
    """
    if not (query or "").strip():
        return fetch_recent_outgoings(dsn, limit)

    like = f"%{query.strip()}%"

    def q(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
        cur.execute(
            "SELECT id, occurred_at::date, merchant, amount "
            "FROM transactions "
            "WHERE amount < 0 AND ("
            "  id ILIKE %s OR merchant ILIKE %s "
            "  OR description ILIKE %s OR occurred_at::text ILIKE %s"
            ") "
            "ORDER BY occurred_at DESC LIMIT %s",
            (like, like, like, like, limit),
        )
        return [
            {"id": r[0], "date": r[1], "merchant": r[2], "amount": float(r[3])}
            for r in cur.fetchall()
        ]

    return _run(dsn, q, [])


def fetch_transactions_by_ids(
    dsn: str | None, tx_ids: list[str]
) -> dict[str, dict[str, Any]]:
    if not tx_ids:
        return {}

    def q(cur: psycopg.Cursor[Any]) -> dict[str, dict[str, Any]]:
        cur.execute(
            "SELECT id, occurred_at::date, merchant, amount, description, category "
            "FROM transactions WHERE id = ANY(%s)",
            (tx_ids,),
        )
        return {
            r[0]: {"date": r[1], "merchant": r[2], "amount": float(r[3]),
                   "description": r[4], "category": r[5]}
            for r in cur.fetchall()
        }

    return _run(dsn, q, {})
