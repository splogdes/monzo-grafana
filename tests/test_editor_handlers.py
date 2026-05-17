"""Tests for render_rule_form's context_tx DB lookup (new and edit modes)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from monzo_grafana.editor import app as app_module
from monzo_grafana.editor import queries
from monzo_grafana.editor.settings import EditorSettings


@pytest.fixture
def editor(tmp_path: Path) -> app_module._Editor:
    settings = EditorSettings(
        categories_file=tmp_path / "categories.yaml",
        bind="localhost",
        port=0,
        pg_dsn=None,
        trigger_url=None,
    )
    return app_module._Editor(settings)


def _stub_queries(monkeypatch: pytest.MonkeyPatch, tx_map: dict[str, Any]) -> None:
    monkeypatch.setattr(queries, "fetch_transactions_by_ids", lambda _dsn, _ids: tx_map)
    monkeypatch.setattr(queries, "fetch_recent_transactions", lambda _dsn, **_kw: [])
    monkeypatch.setattr(queries, "fetch_merchants", lambda _dsn, **_kw: [])
    monkeypatch.setattr(queries, "fetch_db_categories", lambda _dsn: [])
    monkeypatch.setattr(queries, "fetch_db_groups", lambda _dsn: [])


FAKE_TX: dict[str, Any] = {
    "date": date(2025, 3, 10),
    "merchant": "Tesco",
    "amount": -42.50,
    "description": "TESCO STORES 1234",
    "category": "groceries",
    "group_id": "iceland_2025",
    "offset_for_tx": None,
    "offset_for_group": None,
}


# --- new mode ---

def test_new_form_with_tx_id_shows_db_merchant(
    monkeypatch: pytest.MonkeyPatch, editor: app_module._Editor
) -> None:
    _stub_queries(monkeypatch, {"tx_abc": FAKE_TX})
    status, body, _ = editor.get_new({"tx_id": "tx_abc"}, {}, {})
    assert status == 200
    assert b"Tesco" in body


def test_new_form_with_tx_id_shows_group_badge(
    monkeypatch: pytest.MonkeyPatch, editor: app_module._Editor
) -> None:
    _stub_queries(monkeypatch, {"tx_abc": FAKE_TX})
    _, body, _ = editor.get_new({"tx_id": "tx_abc"}, {}, {})
    assert b"already in group" in body
    assert b"iceland_2025" in body


def test_new_form_prefills_group_field_from_db(
    monkeypatch: pytest.MonkeyPatch, editor: app_module._Editor
) -> None:
    _stub_queries(monkeypatch, {"tx_abc": FAKE_TX})
    _, body, _ = editor.get_new({"tx_id": "tx_abc"}, {}, {})
    assert b"iceland_2025" in body


def test_new_form_shows_offset_for_tx_badge(
    monkeypatch: pytest.MonkeyPatch, editor: app_module._Editor
) -> None:
    tx = {**FAKE_TX, "group_id": None, "offset_for_tx": "tx_original"}
    _stub_queries(monkeypatch, {"tx_abc": tx})
    _, body, _ = editor.get_new({"tx_id": "tx_abc"}, {}, {})
    assert b"offsets tx" in body
    assert b"tx_original" in body


def test_new_form_shows_offset_for_group_badge(
    monkeypatch: pytest.MonkeyPatch, editor: app_module._Editor
) -> None:
    tx = {**FAKE_TX, "group_id": None, "offset_for_group": "iceland_2025"}
    _stub_queries(monkeypatch, {"tx_abc": tx})
    _, body, _ = editor.get_new({"tx_id": "tx_abc"}, {}, {})
    assert b"offsets group" in body
    assert b"iceland_2025" in body


def test_new_form_tx_not_in_db_falls_back_to_params(
    monkeypatch: pytest.MonkeyPatch, editor: app_module._Editor
) -> None:
    _stub_queries(monkeypatch, {})
    _, body, _ = editor.get_new(
        {"tx_id": "tx_missing", "merchant": "Fallback Shop"}, {}, {}
    )
    assert b"Fallback Shop" in body
    assert b"already in group" not in body


def test_new_form_without_tx_id_has_no_context_card(
    monkeypatch: pytest.MonkeyPatch, editor: app_module._Editor
) -> None:
    _stub_queries(monkeypatch, {})
    status, body, _ = editor.get_new({}, {}, {})
    assert status == 200
    assert b"already in group" not in body
    assert b"offsets tx" not in body


# --- edit mode ---

def test_edit_form_shows_group_badge_from_db(
    monkeypatch: pytest.MonkeyPatch, editor: app_module._Editor, tmp_path: Path
) -> None:
    import yaml
    rules_file = tmp_path / "categories.yaml"
    rules_file.write_text(
        yaml.safe_dump({"overrides": [{"transaction_id": "tx_abc", "category": "groceries"}]})
    )
    editor.store.path = rules_file

    _stub_queries(monkeypatch, {"tx_abc": FAKE_TX})
    status, body, _ = editor.get_edit({"idx": "0"}, {}, {})
    assert status == 200
    assert b"already in group" in body
    assert b"iceland_2025" in body


# --- queries edge cases ---

def test_fetch_transactions_by_ids_no_dsn_returns_empty() -> None:
    assert queries.fetch_transactions_by_ids(None, ["tx1"]) == {}


def test_fetch_transactions_by_ids_empty_list_returns_empty() -> None:
    assert queries.fetch_transactions_by_ids(None, []) == {}
