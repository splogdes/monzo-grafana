"""Tests for YAML schema validation in the rules loader."""

from __future__ import annotations

from pathlib import Path

from monzo_grafana.rules.loader import load_groups, load_overrides, load_splits


def write(path: Path, body: str) -> None:
    path.write_text(body)


def test_load_overrides_valid(tmp_categories: Path) -> None:
    write(tmp_categories, """
overrides:
  - merchant: Tesco
    category: groceries
  - transaction_id: tx_001
    category: internal
""")
    rules = load_overrides(tmp_categories)
    assert len(rules) == 2
    assert rules[0]["merchant"] == "Tesco"
    assert rules[0]["category"] == "groceries"
    assert rules[1]["transaction_id"] == "tx_001"


def test_load_overrides_compiles_regex(tmp_categories: Path) -> None:
    write(tmp_categories, """
overrides:
  - merchant_pattern: '(?i)tesco'
    category: groceries
""")
    rules = load_overrides(tmp_categories)
    assert len(rules) == 1
    assert rules[0]["merchant_re"].search("Tesco")


def test_load_overrides_skips_no_matcher(tmp_categories: Path) -> None:
    write(tmp_categories, """
overrides:
  - category: groceries
""")
    assert load_overrides(tmp_categories) == []


def test_load_overrides_skips_no_action(tmp_categories: Path) -> None:
    write(tmp_categories, """
overrides:
  - merchant: Tesco
""")
    assert load_overrides(tmp_categories) == []


def test_load_overrides_missing_file(tmp_path: Path) -> None:
    assert load_overrides(tmp_path / "nope.yaml") == []


def test_load_overrides_amortise(tmp_categories: Path) -> None:
    write(tmp_categories, """
overrides:
  - merchant: Claude
    amortise_months: 1
""")
    rules = load_overrides(tmp_categories)
    assert rules[0]["amortise"] == {"unit": "months", "n": 1}


def test_load_overrides_rejects_bad_share(tmp_categories: Path) -> None:
    write(tmp_categories, """
overrides:
  - merchant: Foo
    category: bar
    my_share: garbage
""")
    assert load_overrides(tmp_categories) == []


def test_load_splits_valid(tmp_categories: Path) -> None:
    write(tmp_categories, """
splits:
  - transaction_id: tx_001
    parts:
      - amount: -10.00
        category: groceries
      - amount: -5.00
        category: transport
""")
    splits = load_splits(tmp_categories)
    assert len(splits) == 1
    assert len(splits[0]["parts"]) == 2
    assert splits[0]["parts"][0]["amount"] == -10.0


def test_load_splits_skips_no_parts(tmp_categories: Path) -> None:
    write(tmp_categories, """
splits:
  - transaction_id: tx_001
    parts: []
""")
    assert load_splits(tmp_categories) == []


def test_load_splits_skips_bad_amount(tmp_categories: Path) -> None:
    write(tmp_categories, """
splits:
  - transaction_id: tx_001
    parts:
      - amount: not_a_number
""")
    assert load_splits(tmp_categories) == []


def test_load_groups(tmp_categories: Path) -> None:
    write(tmp_categories, """
groups:
  iceland_2025:
    kind: holiday
    name: Iceland 2025
    starts_at: 2025-06-01
    ends_at: 2025-06-15
    budget: 1500
""")
    groups = load_groups(tmp_categories)
    assert len(groups) == 1
    g = groups[0]
    assert g["id"] == "iceland_2025"
    assert g["kind"] == "holiday"
    assert g["budget"] == 1500


def test_load_groups_defaults(tmp_categories: Path) -> None:
    write(tmp_categories, """
groups:
  bare: {}
""")
    g = load_groups(tmp_categories)[0]
    assert g["kind"] == "project"
    assert g["name"] == "bare"
    assert g["amortise"] is False
