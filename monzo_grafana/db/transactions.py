"""Transaction upsert and the rule-only retag pass."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import requests

from ..config import Config
from ..grafana_api import get_split_annotations
from ..monzo.auth import get_valid_token
from ..monzo.client import MonzoClient
from ..rules import (
    find_override,
    load_overrides,
    merchant_name,
    resolve_amortise_days,
    rule_to_columns,
    tx_description,
    tx_time,
)
from .groups import sync_groups
from .splits import sync_splits

log = logging.getLogger(__name__)

PENCE_PER_POUND = 100.0

UPSERT_SQL = """
INSERT INTO transactions
    (id, occurred_at, amount, merchant, description,
     monzo_category, category, amortise_days, raw,
     my_share, group_id, offset_for_tx, offset_for_group)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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

RETAG_UPDATE_SQL = """
UPDATE transactions SET
    category = %s, amortise_days = %s,
    my_share = %s, group_id = %s,
    offset_for_tx = %s, offset_for_group = %s
WHERE id = %s
  AND (category, amortise_days, my_share, group_id, offset_for_tx, offset_for_group)
      IS DISTINCT FROM (%s, %s, %s, %s, %s, %s)
"""


def upsert_transactions(
    cfg: Config,
    transactions: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
    split_parents: set[str] | None = None,
) -> None:
    split_parents = split_parents or set()
    rows: list[tuple[Any, ...]] = []
    for tx in transactions:
        if tx.get("decline_reason"):
            continue
        if tx.get("amount") == 0:
            continue
        merchant = merchant_name(tx)
        description = tx_description(tx)
        if tx["id"] in split_parents:
            rule = None
            monzo_category = tx.get("category") or "unknown"
            category = "split"
            if find_override(tx["id"], merchant, description, overrides):
                log.warning("override on %s ignored — split takes precedence", tx["id"])
        else:
            rule = find_override(tx["id"], merchant, description, overrides)
            monzo_category = tx.get("category") or "unknown"
            category = (rule and rule.get("category")) or monzo_category
        days = resolve_amortise_days(tx_time(tx), rule, annotations)
        share, group_id, off_tx, off_grp = rule_to_columns(rule)
        rows.append((
            tx["id"],
            tx_time(tx),
            tx["amount"] / PENCE_PER_POUND,
            merchant,
            description,
            monzo_category,
            category,
            days,
            json.dumps(tx),
            share, group_id, off_tx, off_grp,
        ))

    if not rows:
        log.info("No eligible transactions to upsert")
        return

    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
        conn.commit()
    log.info("Upserted %d transaction(s)", len(rows))


def poll(cfg: Config) -> None:
    log.info("--- Poll start ---")
    try:
        sync_groups(cfg)
        split_parents = sync_splits(cfg)
        access_token = get_valid_token(cfg)
        client = MonzoClient(access_token)
        account_id = client.get_account_id()

        now = datetime.now(UTC)
        since = now - timedelta(days=cfg.lookback_days)

        transactions = client.fetch_transactions(account_id, since, now)
        log.info("Fetched %d transaction(s) from Monzo", len(transactions))

        annotations = get_split_annotations(cfg)
        overrides = load_overrides(cfg.categories_file)
        upsert_transactions(cfg, transactions, overrides, annotations, split_parents)
    except requests.HTTPError as exc:
        log.error("Monzo HTTP error: %s", exc)
    except Exception:
        log.exception("Unexpected error during poll")
    log.info("--- Poll done ---")


def retag(cfg: Config) -> None:
    """Re-apply ``categories.yaml`` rules to every transaction already in Postgres.

    No Monzo round-trip, no DELETE. Each row gets an UPDATE only if its
    derived columns differ from current — so a second retag with no rule
    changes touches zero rows.
    """
    log.info("--- Retag start ---")
    try:
        sync_groups(cfg)
        split_parents = sync_splits(cfg)
        overrides = load_overrides(cfg.categories_file)
        annotations = get_split_annotations(cfg)

        with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
            # Skip ledger rows (sync_splits owns those) and split parents
            # (sync_splits has already set their category to 'split').
            cur.execute(
                "SELECT id, merchant, description, monzo_category, occurred_at "
                "FROM transactions WHERE account_id <> 'ledger'"
            )
            updates = 0
            for tx_id, merchant, description, monzo_cat, occurred_at in cur.fetchall():
                if tx_id in split_parents:
                    continue
                rule = find_override(tx_id, merchant, description or "", overrides)
                new_category = (rule and rule.get("category")) or monzo_cat
                new_days = resolve_amortise_days(occurred_at, rule, annotations)
                share, group_id, off_tx, off_grp = rule_to_columns(rule)
                cur.execute(
                    RETAG_UPDATE_SQL,
                    (
                        new_category, new_days, share, group_id, off_tx, off_grp,
                        tx_id,
                        new_category, new_days, share, group_id, off_tx, off_grp,
                    ),
                )
                updates += cur.rowcount
            conn.commit()
            log.info("Retag updated %d row(s)", updates)
    except Exception:
        log.exception("Unexpected error during retag")
    log.info("--- Retag done ---")
