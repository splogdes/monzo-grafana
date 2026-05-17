"""Typed configuration loaded from environment variables.

Single source of truth for environment-driven settings. Every module that
needs a value should pull it off a :class:`Config` instance rather than
reading ``os.environ`` directly.
"""

from __future__ import annotations

import os
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path


def resolve_pg_dsn(*, require: bool = True) -> str | None:
    """Return a Postgres DSN built from ``PG_DSN`` or component env vars.

    With ``require=False`` returns ``None`` when nothing is configured, so the
    rule editor can still serve forms without a database.
    """
    if os.environ.get("PG_DSN"):
        return os.environ["PG_DSN"]
    user = os.environ.get("POSTGRES_USER", "monzo")
    password = os.environ.get("POSTGRES_PASSWORD")
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "finance")
    if not password:
        if require:
            sys.exit("Missing PG_DSN or POSTGRES_PASSWORD")
        return None
    return f"postgresql://{user}:{urllib.parse.quote_plus(password)}@{host}:{port}/{db}"


@dataclass(frozen=True, kw_only=True)
class Config:
    client_id: str
    client_secret: str
    redirect_uri: str
    token_file: Path
    pg_dsn: str
    grafana_url: str
    grafana_token: str
    poll_interval_minutes: int
    lookback_days: int
    categories_file: Path
    santander_rules_file: Path
    revolut_rules_file: Path
    trigger_bind: str
    trigger_port: int

    @classmethod
    def from_env(cls) -> Config:
        required = ["MONZO_CLIENT_ID", "MONZO_CLIENT_SECRET"]
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            sys.exit(f"Missing required env vars: {', '.join(missing)}")
        dsn = resolve_pg_dsn(require=True)
        assert dsn is not None
        return cls(
            client_id=os.environ["MONZO_CLIENT_ID"],
            client_secret=os.environ["MONZO_CLIENT_SECRET"],
            redirect_uri=os.environ.get(
                "MONZO_REDIRECT_URI", "http://localhost:7923/callback"
            ),
            token_file=Path(os.environ.get("TOKEN_FILE", "tokens.json")),
            pg_dsn=dsn,
            grafana_url=os.environ.get("GRAFANA_URL", "http://localhost:3000"),
            grafana_token=os.environ.get("GRAFANA_TOKEN", ""),
            poll_interval_minutes=int(os.environ.get("POLL_INTERVAL_MINUTES", "30")),
            lookback_days=int(os.environ.get("LOOKBACK_DAYS", "90")),
            categories_file=Path(os.environ.get("CATEGORIES_FILE", "categories.yaml")),
            santander_rules_file=Path(
                os.environ.get("SANTANDER_RULES_FILE", "data/santander_rules.yaml")
            ),
            revolut_rules_file=Path(
                os.environ.get("REVOLUT_RULES_FILE", "data/revolut_rules.yaml")
            ),
            trigger_bind=os.environ.get("POLLER_TRIGGER_BIND", "127.0.0.1"),
            trigger_port=int(os.environ.get("POLLER_TRIGGER_PORT", "7925")),
        )
