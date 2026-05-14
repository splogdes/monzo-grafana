"""Reconcile the ``groups`` table with the YAML ``groups:`` section."""

from __future__ import annotations

import logging

import psycopg

from ..config import Config
from ..rules import load_groups

log = logging.getLogger(__name__)

GROUPS_UPSERT_SQL = """
INSERT INTO groups (id, kind, name, starts_at, ends_at, budget, amortise)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    kind      = EXCLUDED.kind,
    name      = EXCLUDED.name,
    starts_at = EXCLUDED.starts_at,
    ends_at   = EXCLUDED.ends_at,
    budget    = EXCLUDED.budget,
    amortise  = EXCLUDED.amortise
"""


def sync_groups(cfg: Config) -> None:
    """Upsert every YAML group and remove DB-only entries, so a deleted YAML
    key disappears from the dashboard's ``group_costs`` view.
    """
    yaml_groups = load_groups(cfg.categories_file)
    yaml_ids = {g["id"] for g in yaml_groups}

    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        for g in yaml_groups:
            cur.execute(GROUPS_UPSERT_SQL, (
                g["id"], g["kind"], g["name"],
                g["starts_at"], g["ends_at"], g["budget"], g["amortise"],
            ))

        cur.execute("SELECT id FROM groups")
        db_ids = {r[0] for r in cur.fetchall()}
        stale = db_ids - yaml_ids
        if stale:
            cur.execute("DELETE FROM groups WHERE id = ANY(%s)", (list(stale),))
            log.info("Removed %d stale group(s): %s", len(stale), ", ".join(sorted(stale)))

        conn.commit()
    if yaml_groups:
        log.info("Synced %d group(s)", len(yaml_groups))
