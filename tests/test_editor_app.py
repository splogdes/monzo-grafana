"""Route-level tests for the editor's HTTP layer.

Exercises the offset-tx search endpoint without spinning up the HTTP server:
the route handlers are plain methods on ``_Editor``, so we can call them
directly with stub form/query dicts.
"""

from __future__ import annotations

import json
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


def _decode(resp: tuple[int, bytes, dict[str, str]]) -> tuple[int, Any, dict[str, str]]:
    status, body, headers = resp
    return status, json.loads(body.decode("utf-8")), headers


def test_offset_tx_search_returns_json_shape(
    monkeypatch: pytest.MonkeyPatch, editor: app_module._Editor
) -> None:
    captured: dict[str, Any] = {}

    def fake_search(dsn: str | None, q: str, limit: int) -> list[dict[str, Any]]:
        captured["dsn"] = dsn
        captured["q"] = q
        captured["limit"] = limit
        return [
            {"id": "tx_1", "date": date(2025, 8, 15), "merchant": "Tesco", "amount": -5.2},
            {"id": "tx_2", "date": date(2025, 8, 14), "merchant": "Co-op", "amount": -1.0},
        ]

    monkeypatch.setattr(queries, "search_outgoings", fake_search)

    status, payload, headers = _decode(
        editor.get_api_offset_tx_search({"q": "tesco"}, {}, {})
    )
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert captured == {"dsn": None, "q": "tesco", "limit": 20}
    assert payload == [
        {"id": "tx_1", "date": "2025-08-15", "merchant": "Tesco", "amount": -5.2},
        {"id": "tx_2", "date": "2025-08-14", "merchant": "Co-op", "amount": -1.0},
    ]


def test_offset_tx_search_clamps_limit(
    monkeypatch: pytest.MonkeyPatch, editor: app_module._Editor
) -> None:
    seen: list[int] = []

    def fake_search(_dsn: str | None, _q: str, limit: int) -> list[dict[str, Any]]:
        seen.append(limit)
        return []

    monkeypatch.setattr(queries, "search_outgoings", fake_search)

    editor.get_api_offset_tx_search({"limit": "999"}, {}, {})
    editor.get_api_offset_tx_search({"limit": "0"}, {}, {})
    editor.get_api_offset_tx_search({"limit": "garbage"}, {}, {})
    editor.get_api_offset_tx_search({}, {}, {})

    assert seen == [50, 1, 20, 20]


def test_offset_tx_search_empty_q_passes_through(
    monkeypatch: pytest.MonkeyPatch, editor: app_module._Editor
) -> None:
    captured: dict[str, str] = {}

    def fake_search(_dsn: str | None, q: str, _limit: int) -> list[dict[str, Any]]:
        captured["q"] = q
        return []

    monkeypatch.setattr(queries, "search_outgoings", fake_search)
    editor.get_api_offset_tx_search({}, {}, {})
    assert captured == {"q": ""}


def test_search_outgoings_with_no_dsn_returns_empty() -> None:
    assert queries.search_outgoings(None, "anything") == []
    assert queries.search_outgoings(None, "") == []
