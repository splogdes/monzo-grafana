"""Tests for the Santander statement parser and importer helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from monzo_grafana.rules import load_overrides
from monzo_grafana.santander.importer import _intra_day_indices, _make_id
from monzo_grafana.santander.merchant import clean_merchant, prepare_for_match
from monzo_grafana.santander.parser import parse_text
from monzo_grafana.santander.staging_rules import apply_rules, parse_rules, resolve

# U+00A0 (non-breaking space) is the label/value separator used by Santander.
NBSP = "\xa0"
SAMPLE = "\n".join(
    [
        "From:" + NBSP + "13/05/2019 to 01/11/2019",
        "",
        "Account:" + NBSP + "XXXX XXXX XXXX 8171",
        "",
        "Date:" + NBSP + "01/11/2019",
        "Description:" + NBSP + "FASTER PAYMENTS RECEIPT REF.SAMPLE",
        "Amount:" + NBSP + "20.00\t",
        "Balance:" + NBSP + "343.39",
        "",
        "Date:" + NBSP + "31/10/2019",
        "Description:" + NBSP + "GWR KEYNSHAM SST (VIA APPLE PAY), ON 29-10-2019",
        "Amount:" + NBSP + "-2.30\t",
        "Balance:" + NBSP + "323.39",
        "",
        "Date:" + NBSP + "31/10/2019",
        "Description:" + NBSP + "GWR KEYNSHAM SST (VIA APPLE PAY), ON 29-10-2019",
        "Amount:" + NBSP + "-6.90\t",
        "Balance:" + NBSP + "325.69",
        "",
    ]
)


def test_parse_basic_records() -> None:
    records = parse_text(SAMPLE, source_file="sample.txt")
    assert len(records) == 3
    assert records[0].date == date(2019, 11, 1)
    assert records[0].amount == Decimal("20.00")
    assert records[0].balance == Decimal("343.39")
    assert records[1].date == date(2019, 10, 31)
    assert records[1].amount == Decimal("-2.30")
    assert records[2].amount == Decimal("-6.90")
    assert all(r.source_file == "sample.txt" for r in records)
    assert [r.file_index for r in records] == [0, 1, 2]


def test_intra_day_indices_for_same_date() -> None:
    records = parse_text(SAMPLE, source_file="sample.txt")
    idxs = _intra_day_indices(records)
    assert idxs == [0, 0, 1]


def test_ids_are_stable_and_distinct_for_dupes() -> None:
    records = parse_text(SAMPLE, source_file="sample.txt")
    idxs = _intra_day_indices(records)
    ids = [_make_id(r, i) for r, i in zip(records, idxs, strict=True)]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("santander_") for i in ids)
    records2 = parse_text(SAMPLE, source_file="sample.txt")
    idxs2 = _intra_day_indices(records2)
    ids2 = [_make_id(r, i) for r, i in zip(records2, idxs2, strict=True)]
    assert ids == ids2


def test_clean_merchant_strips_boilerplate() -> None:
    assert clean_merchant("GWR KEYNSHAM SST (VIA APPLE PAY), ON 29-10-2019") == "Gwr Keynsham Sst"
    assert (
        clean_merchant("CARD PAYMENT TO CDKEYS.COM,1.49 GBP, RATE 1.00/GBP ON 28-10-2019")
        == "Cdkeys.Com"
    )
    assert clean_merchant("TESCO STORES") == "Tesco Stores"
    # &amp; entity is unescaped before pattern matching.
    assert clean_merchant("MARKS&AMP;SPENCER PLC") == "Marks&Spencer Plc"


def test_staging_rules_apply(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(
        "- canonical: Tesco\n"
        "  category: groceries\n"
        "  regex: '(?i)\\btesco\\b'\n",
        encoding="utf-8",
    )
    rules = parse_rules(rules_path)
    match = apply_rules(rules, prepare_for_match("TESCO STORES 1234 BATH"))
    assert match is not None
    assert match.canonical == "Tesco"
    assert match.category == "groceries"
    # A description that doesn't match returns None.
    assert apply_rules(rules, prepare_for_match("GWR KEYNSHAM SST")) is None


def _tesco_rules(tmp_path: Path) -> list:
    path = tmp_path / "rules.yaml"
    path.write_text(
        "- canonical: Tesco\n"
        "  category: groceries\n"
        "  regex: '(?i)\\btesco\\b'\n",
        encoding="utf-8",
    )
    return parse_rules(path)


def test_resolve_uses_staging_rule_when_no_override(tmp_path: Path) -> None:
    rules = _tesco_rules(tmp_path)
    merchant, category, override = resolve(
        "tx_001", "CARD PAYMENT TO TESCO STORES 1234 BATH", rules, [], "unknown"
    )
    assert merchant == "Tesco"
    assert category == "groceries"
    assert override is None


def test_resolve_falls_back_when_no_rule_matches(tmp_path: Path) -> None:
    rules = _tesco_rules(tmp_path)
    merchant, category, override = resolve(
        "tx_002", "CARD PAYMENT TO RANDOM SHOP, ON 12-03-2025", rules, [], "unknown"
    )
    # clean_merchant strips boilerplate and title-cases the rest.
    assert merchant == "Random Shop"
    assert category == "unknown"
    assert override is None


def test_resolve_categories_yaml_override_wins_over_staging(tmp_path: Path) -> None:
    """``categories.yaml`` precedence (per-tx, merchant, pattern) > staging rule."""
    rules = _tesco_rules(tmp_path)
    cat_path = tmp_path / "categories.yaml"
    cat_path.write_text(
        "overrides:\n  - transaction_id: tx_003\n    category: internal\n",
        encoding="utf-8",
    )
    overrides = load_overrides(cat_path)
    merchant, category, override = resolve(
        "tx_003", "TESCO STORES 1234 BATH", rules, overrides, "unknown"
    )
    # Staging still sets merchant; override wins category.
    assert merchant == "Tesco"
    assert category == "internal"
    assert override is not None


def test_resolve_fallback_category_used_for_unmatched(tmp_path: Path) -> None:
    """Retag passes the existing monzo_category as fallback so unmatched rows keep it."""
    rules = _tesco_rules(tmp_path)
    merchant, category, _ = resolve(
        "tx_004", "RANDOM MERCHANT", rules, [], fallback_category="eating_out"
    )
    assert merchant == "Random Merchant"
    assert category == "eating_out"
