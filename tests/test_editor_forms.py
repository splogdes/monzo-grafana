"""Tests for the editor's form parsers."""

from __future__ import annotations

from monzo_grafana.editor.forms import (
    build_group_from_form,
    build_rule_from_form,
    parse_split_parts,
)


class TestBuildRuleFromForm:
    def test_minimal_merchant_rule(self) -> None:
        rule = build_rule_from_form({
            "match_by": "merchant",
            "match_value": "Tesco",
            "category": "groceries",
        })
        assert rule == {"merchant": "Tesco", "category": "groceries"}

    def test_transaction_id_rule(self) -> None:
        rule = build_rule_from_form({
            "match_by": "transaction_id",
            "match_value": "tx_001",
            "category": "internal",
        })
        assert rule == {"transaction_id": "tx_001", "category": "internal"}

    def test_amortise(self) -> None:
        rule = build_rule_from_form({
            "match_by": "merchant",
            "match_value": "Claude",
            "amortise_unit": "months",
            "amortise_n": "1",
        })
        assert rule == {"merchant": "Claude", "amortise_months": 1}

    def test_no_matcher(self) -> None:
        assert build_rule_from_form({
            "match_by": "merchant",
            "match_value": "",
            "category": "groceries",
        }) is None

    def test_no_action(self) -> None:
        assert build_rule_from_form({
            "match_by": "merchant",
            "match_value": "Tesco",
        }) is None

    def test_bad_match_by(self) -> None:
        assert build_rule_from_form({
            "match_by": "totally_invalid",
            "match_value": "Tesco",
            "category": "x",
        }) is None

    def test_falls_back_to_tx_id_field(self) -> None:
        # /add posts can carry the matcher in tx_id rather than match_value
        rule = build_rule_from_form({
            "match_by": "transaction_id",
            "tx_id": "tx_legacy",
            "category": "internal",
        })
        assert rule == {"transaction_id": "tx_legacy", "category": "internal"}


class TestBuildGroupFromForm:
    def test_basic(self) -> None:
        parsed = build_group_from_form({
            "id": "iceland_2025",
            "kind": "holiday",
            "name": "Iceland 2025",
            "starts_at": "2025-06-01",
            "ends_at": "2025-06-15",
            "budget": "1500.50",
            "amortise": "1",
        })
        assert parsed is not None
        gid, body = parsed
        assert gid == "iceland_2025"
        assert body["kind"] == "holiday"
        assert body["budget"] == 1500.50
        assert body["amortise"] is True

    def test_defaults(self) -> None:
        parsed = build_group_from_form({"id": "x"})
        assert parsed is not None
        _, body = parsed
        assert body["kind"] == "project"
        assert body["name"] == "x"

    def test_missing_id(self) -> None:
        assert build_group_from_form({"name": "no id"}) is None

    def test_bad_budget_ignored(self) -> None:
        parsed = build_group_from_form({"id": "x", "budget": "garbage"})
        assert parsed is not None
        _, body = parsed
        assert "budget" not in body


class TestParseSplitParts:
    def test_basic(self) -> None:
        raw = {
            "amount": ["-10.00", "-5.00"],
            "category": ["groceries", "transport"],
            "group": ["", ""],
            "offset_for_tx": ["", ""],
            "offset_for_group": ["", ""],
            "note": ["", ""],
        }
        parts, err = parse_split_parts(raw)
        assert err is None
        assert parts == [
            {"amount": -10.00, "category": "groceries"},
            {"amount": -5.00, "category": "transport"},
        ]

    def test_skips_blank_amount(self) -> None:
        raw = {"amount": ["-10", "", "-5"], "category": ["a", "b", "c"]}
        parts, err = parse_split_parts(raw)
        assert err is None
        assert [p["amount"] for p in parts] == [-10.0, -5.0]

    def test_bad_amount(self) -> None:
        raw = {"amount": ["-10", "abc"], "category": ["a", "b"]}
        parts, err = parse_split_parts(raw)
        assert err is not None
        assert "abc" in err
        assert parts == []
