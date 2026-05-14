"""Tests for env-driven config and DSN assembly."""

from __future__ import annotations

import pytest

from monzo_grafana.config import resolve_pg_dsn


def test_pg_dsn_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DSN", "postgresql://override")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    assert resolve_pg_dsn() == "postgresql://override"


def test_component_assembly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_DSN", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "monzo")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/word")
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_DB", "finance")
    # urlencoded password
    assert resolve_pg_dsn() == "postgresql://monzo:p%40ss%2Fword@db:5433/finance"


def test_missing_password_with_require_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_DSN", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    with pytest.raises(SystemExit):
        resolve_pg_dsn(require=True)


def test_missing_password_without_require_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_DSN", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    assert resolve_pg_dsn(require=False) is None
