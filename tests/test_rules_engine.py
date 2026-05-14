"""Tests for the rule-matching engine and amortisation arithmetic."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest

from monzo_grafana.rules.engine import (
    amortise_total_days,
    find_override,
    parse_share,
    resolve_amortise_days,
    rule_to_columns,
)

Rules = list[dict[str, Any]]


class TestParseShare:
    def test_decimal(self) -> None:
        assert parse_share("0.5") == 0.5

    def test_fraction(self) -> None:
        assert parse_share("1/3") == pytest.approx(1 / 3)

    def test_numeric(self) -> None:
        assert parse_share(0.25) == 0.25
        assert parse_share(1) == 1.0

    def test_none_and_empty(self) -> None:
        assert parse_share(None) is None
        assert parse_share("") is None
        assert parse_share("   ") is None

    def test_garbage(self) -> None:
        assert parse_share("abc") is None
        assert parse_share("1/0") is None
        assert parse_share("a/b") is None


class TestFindOverride:
    def test_transaction_id_beats_merchant_regardless_of_order(self) -> None:
        rules: Rules = [
            {"merchant": "Tesco", "category": "groceries"},
            {"transaction_id": "tx_001", "category": "internal"},
        ]
        match = find_override("tx_001", "Tesco", "", rules)
        assert match is not None and match["category"] == "internal"

    def test_merchant_exact_beats_pattern(self) -> None:
        rules: Rules = [
            {"merchant_re": re.compile(r"(?i)tesco"), "category": "from_pattern"},
            {"merchant": "Tesco", "category": "from_exact"},
        ]
        match = find_override("tx_x", "Tesco", "", rules)
        assert match is not None and match["category"] == "from_exact"

    def test_merchant_pattern_beats_description_pattern(self) -> None:
        rules: Rules = [
            {"description_re": re.compile(r"groceries"), "category": "from_desc"},
            {"merchant_re": re.compile(r"(?i)tesco"), "category": "from_merchant_re"},
        ]
        match = find_override("tx_x", "Tesco", "groceries", rules)
        assert match is not None and match["category"] == "from_merchant_re"

    def test_list_order_breaks_ties_within_same_matcher(self) -> None:
        rules: Rules = [
            {"merchant": "Tesco", "category": "first"},
            {"merchant": "Tesco", "category": "second"},
        ]
        match = find_override("tx_x", "Tesco", "", rules)
        assert match is not None and match["category"] == "first"

    def test_merchant_exact_case_sensitive(self) -> None:
        rules: Rules = [{"merchant": "Tesco", "category": "groceries"}]
        assert find_override("tx_x", "TESCO", "", rules) is None

    def test_merchant_regex(self) -> None:
        rules: Rules = [{"merchant_re": re.compile(r"(?i)tesco"), "category": "groceries"}]
        match = find_override("tx_x", "Tesco Express", "", rules)
        assert match is not None

    def test_description_regex(self) -> None:
        rules: Rules = [{"description_re": re.compile(r"^salary"), "category": "income"}]
        assert find_override("tx_x", "Acme Co", "salary March", rules) is not None
        assert find_override("tx_x", "Acme Co", "March salary", rules) is None

    def test_no_match(self) -> None:
        rules: Rules = [{"merchant": "Tesco"}]
        assert find_override("tx_x", "Other", "", rules) is None


class TestAmortiseTotalDays:
    def test_none(self) -> None:
        assert amortise_total_days(None, datetime(2025, 1, 1, tzinfo=UTC)) == 0

    def test_days(self) -> None:
        base = datetime(2025, 1, 1, tzinfo=UTC)
        assert amortise_total_days({"unit": "days", "n": 7}, base) == 7

    def test_weeks(self) -> None:
        base = datetime(2025, 1, 1, tzinfo=UTC)
        assert amortise_total_days({"unit": "weeks", "n": 3}, base) == 21

    def test_months(self) -> None:
        # Jan 1 → Apr 1 = 90 days (Jan 31 + Feb 28 + Mar 31)
        base = datetime(2025, 1, 1, tzinfo=UTC)
        assert amortise_total_days({"unit": "months", "n": 3}, base) == 90

    def test_unknown_unit(self) -> None:
        base = datetime(2025, 1, 1, tzinfo=UTC)
        assert amortise_total_days({"unit": "fortnights", "n": 2}, base) == 0


class TestResolveAmortiseDays:
    def test_rule_wins_over_annotation(self) -> None:
        base = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
        rule = {"amortise": {"unit": "days", "n": 30}}
        annotations = [{"time": base, "split": 6}]
        assert resolve_amortise_days(base, rule, annotations) == 30

    def test_annotation_used_when_no_rule(self) -> None:
        base = datetime(2025, 1, 1, tzinfo=UTC)
        annotations = [{"time": base, "split": 3}]
        # 3 months from 1 Jan = 90 days
        assert resolve_amortise_days(base, None, annotations) == 90

    def test_returns_none_when_single_day(self) -> None:
        base = datetime(2025, 6, 15, tzinfo=UTC)
        # amortise_days=1 collapses to None
        assert resolve_amortise_days(base, {"amortise": {"unit": "days", "n": 1}}, []) is None

    def test_returns_none_with_no_amortisation(self) -> None:
        assert resolve_amortise_days(datetime(2025, 1, 1, tzinfo=UTC), None, []) is None


class TestRuleToColumns:
    def test_no_rule(self) -> None:
        assert rule_to_columns(None) == (1.0, None, None, None)

    def test_partial_rule(self) -> None:
        rule = {"my_share": 0.5, "group": "iceland_2025"}
        assert rule_to_columns(rule) == (0.5, "iceland_2025", None, None)

    def test_offsets(self) -> None:
        rule = {"offset_for_tx": "tx_001", "offset_for_group": "rent_2025"}
        share, group, off_tx, off_grp = rule_to_columns(rule)
        assert share == 1.0
        assert group is None
        assert off_tx == "tx_001"
        assert off_grp == "rent_2025"
