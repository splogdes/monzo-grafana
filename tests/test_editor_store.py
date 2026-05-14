"""Tests for the editor's YAML store (round-trips on a tmp file)."""

from __future__ import annotations

from pathlib import Path

import yaml

from monzo_grafana.editor.store import RuleStore


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    store = RuleStore(tmp_path / "categories.yaml")
    assert store.load() == {"overrides": []}


def test_append_and_load(tmp_categories: Path) -> None:
    store = RuleStore(tmp_categories)
    store.append_rule({"merchant": "Tesco", "category": "groceries"})
    store.append_rule({"transaction_id": "tx_001", "category": "internal"})
    doc = store.load()
    assert len(doc["overrides"]) == 2
    assert doc["overrides"][0]["merchant"] == "Tesco"


def test_update_and_delete(tmp_categories: Path) -> None:
    store = RuleStore(tmp_categories)
    store.append_rule({"merchant": "A", "category": "x"})
    store.append_rule({"merchant": "B", "category": "y"})
    store.update_rule(0, {"merchant": "A2", "category": "x2"})
    assert store.load()["overrides"][0]["merchant"] == "A2"
    store.delete_rule(0)
    assert [r["merchant"] for r in store.load()["overrides"]] == ["B"]


def test_groups_crud(tmp_categories: Path) -> None:
    store = RuleStore(tmp_categories)
    store.save_group("iceland_2025", {"kind": "holiday", "name": "Iceland"})
    assert "iceland_2025" in store.yaml_groups()
    store.delete_group("iceland_2025")
    assert "iceland_2025" not in store.yaml_groups()


def test_splits_crud(tmp_categories: Path) -> None:
    store = RuleStore(tmp_categories)
    parts = [{"amount": -10.0, "category": "groceries"}]
    store.save_split("tx_001", parts)
    fetched = store.fetch_split("tx_001")
    assert fetched is not None and fetched["parts"] == parts

    parts2 = [{"amount": -10.0, "category": "transport"}]
    store.save_split("tx_001", parts2)
    fetched2 = store.fetch_split("tx_001")
    assert fetched2 is not None and fetched2["parts"][0]["category"] == "transport"

    assert len(store.all_splits()) == 1
    store.delete_split("tx_001")
    assert store.fetch_split("tx_001") is None


def test_save_preserves_yaml_structure(tmp_categories: Path) -> None:
    store = RuleStore(tmp_categories)
    store.append_rule({"merchant": "Foo", "category": "bar"})
    raw = tmp_categories.read_text()
    assert raw.startswith("# Managed by the rule editor")
    body = yaml.safe_load(raw)
    assert body["overrides"][0]["merchant"] == "Foo"
