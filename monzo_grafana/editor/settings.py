"""Lightweight env-driven settings for the rule editor.

The editor doesn't need Monzo credentials, so it has its own settings
loader independent of :class:`monzo_grafana.config.Config`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..config import resolve_pg_dsn


@dataclass(frozen=True, kw_only=True)
class EditorSettings:
    categories_file: Path
    bind: str
    port: int
    pg_dsn: str | None
    trigger_url: str | None

    @classmethod
    def from_env(cls) -> EditorSettings:
        return cls(
            categories_file=Path(os.environ.get("CATEGORIES_FILE", "categories.yaml")),
            bind=os.environ.get("EDITOR_BIND", "localhost"),
            port=int(os.environ.get("EDITOR_PORT", "7924")),
            pg_dsn=resolve_pg_dsn(require=False),
            trigger_url=os.environ.get("POLLER_TRIGGER_URL"),
        )
