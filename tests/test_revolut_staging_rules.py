"""Tests for monzo_grafana.revolut.staging_rules.resolve()."""

from __future__ import annotations

import re

from monzo_grafana.revolut.staging_rules import resolve
from monzo_grafana.santander.staging_rules import Rule


def _rule(canonical: str, category: str, pattern: str) -> Rule:
    return Rule(canonical=canonical, category=category, regex=pattern, compiled=re.compile(pattern))


TESCO = _rule("Tesco", "groceries", r"(?i)tesco")
NETFLIX = _rule("Netflix", "subscriptions", r"(?i)netflix")


def test_no_match_no_override_uses_stripped_description_and_fallback() -> None:
    merchant, category, rule = resolve("tx1", "  UNKNOWN SHOP  ", [], [], "general")
    assert merchant == "UNKNOWN SHOP"
    assert category == "general"
    assert rule is None


def test_staging_match_sets_canonical_and_category() -> None:
    merchant, category, rule = resolve("tx1", "TESCO SUPERSTORE", [TESCO], [], "general")
    assert merchant == "Tesco"
    assert category == "groceries"
    assert rule is None


def test_override_category_wins_over_staging() -> None:
    overrides = [{"merchant": "Tesco", "category": "shopping"}]
    merchant, category, rule = resolve("tx1", "TESCO SUPERSTORE", [TESCO], overrides, "general")
    assert merchant == "Tesco"
    assert category == "shopping"
    assert rule == overrides[0]


def test_override_by_transaction_id_wins() -> None:
    overrides = [{"transaction_id": "tx99", "category": "internal"}]
    _, category, rule = resolve("tx99", "TESCO SUPERSTORE", [TESCO], overrides, "general")
    assert category == "internal"
    assert rule == overrides[0]


def test_no_staging_match_with_override_by_merchant() -> None:
    overrides = [{"merchant": "UNKNOWN SHOP", "category": "shopping"}]
    merchant, category, _ = resolve("tx1", "UNKNOWN SHOP", [], overrides, "general")
    assert merchant == "UNKNOWN SHOP"
    assert category == "shopping"


def test_override_without_category_falls_through_to_staging() -> None:
    overrides = [{"merchant": "Tesco", "group": "weekly_shop"}]
    merchant, category, _ = resolve("tx1", "TESCO METRO", [TESCO], overrides, "general")
    assert merchant == "Tesco"
    assert category == "groceries"  # staging category, not fallback


def test_first_matching_rule_wins() -> None:
    later_rule = _rule("Tesco Express", "eating_out", r"(?i)tesco")
    merchant, category, _ = resolve("tx1", "TESCO METRO", [TESCO, later_rule], [], "general")
    assert merchant == "Tesco"
    assert category == "groceries"


def test_multiple_rules_picks_correct_match() -> None:
    merchant, category, _ = resolve("tx1", "Netflix monthly", [TESCO, NETFLIX], [], "general")
    assert merchant == "Netflix"
    assert category == "subscriptions"
