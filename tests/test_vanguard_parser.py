"""Tests for the Vanguard ISA CSV parser."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from monzo_grafana.vanguard.parser import (
    _parse_date,
    _to_float,
    parse_vanguard_csv,
)

_MINIMAL_CSV = """\
ISA,,,,,

Cash Transactions,,,,,

Date,Details,Amount,Balance,,
03/08/2025,Deposit for Investment Purchases,"4,000.00","4,000.00",,
04/08/2025,Bought 44 S&P 500 UCITS ETF,"-3,975.40",24.60,,
01/09/2025,Cash Account Interest,0.49,25.09,,
29/09/2025,Deposit via direct credit,"5,000.00","5,025.09",,
03/11/2025,Account Fee for the period,-11.87,"5,013.22",,
Balance,"5,013.22",,,,



Investment Transactions,,,,,

Date,InvestmentName,TransactionDetails,Quantity,Price,Cost
06/08/2025,S&P 500 UCITS ETF - Accumulating (VUAG),Bought 44 VUAG,44.00,90.3500,"3,975.40"
Cost,"3,975.40",,,,
"""


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    p = tmp_path / "vanguard.csv"
    p.write_text(_MINIMAL_CSV, encoding="utf-8")
    return p


def _write_csv(tmp_path: Path, cash_lines: str = "", inv_lines: str = "") -> Path:
    p = tmp_path / "v.csv"
    p.write_text(
        f"ISA,,,,,\n\nCash Transactions,,,,,\n\nDate,Details,Amount,Balance,,\n"
        f"{cash_lines}"
        f"Balance,0.00,,,,\n\n\nInvestment Transactions,,,,,\n\n"
        f"Date,InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
        f"{inv_lines}"
        f"Cost,0.00,,,,\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("s,expected", [
    ("4,000.00", 4000.0),
    ("18,252.48", 18252.48),
    ("90.35", 90.35),
    ("-3,975.40", -3975.40),
    ("  1,234.56  ", 1234.56),
])
def test_to_float(s: str, expected: float) -> None:
    assert _to_float(s) == pytest.approx(expected)


@pytest.mark.parametrize("s,expected", [
    ("03/08/2025", date(2025, 8, 3)),
    ("31/12/2024", date(2024, 12, 31)),
    ("  01/01/2026  ", date(2026, 1, 1)),
])
def test_parse_date(s: str, expected: date) -> None:
    assert _parse_date(s) == expected


# ---------------------------------------------------------------------------
# Full parse
# ---------------------------------------------------------------------------


def test_parse_returns_both_sections(csv_path: Path) -> None:
    cash, investments = parse_vanguard_csv(csv_path)
    assert len(cash) == 5
    assert len(investments) == 1


def test_cash_row_fields(csv_path: Path) -> None:
    cash, _ = parse_vanguard_csv(csv_path)
    assert cash[0].date == date(2025, 8, 3)
    assert cash[0].details == "Deposit for Investment Purchases"
    assert cash[0].amount == pytest.approx(4000.0)
    assert cash[0].balance == pytest.approx(4000.0)
    assert cash[1].amount == pytest.approx(-3975.40)     # buy — negative
    assert cash[3].date == date(2025, 9, 29)
    assert cash[3].balance == pytest.approx(5025.09)
    assert cash[4].amount == pytest.approx(-11.87)        # fee — negative


def test_investment_row_fields(csv_path: Path) -> None:
    _, investments = parse_vanguard_csv(csv_path)
    inv = investments[0]
    assert inv.date == date(2025, 8, 6)
    assert inv.investment_name == "S&P 500 UCITS ETF - Accumulating (VUAG)"
    assert inv.transaction_details == "Bought 44 VUAG"
    assert inv.quantity == pytest.approx(44.0)
    assert inv.price == pytest.approx(90.35)
    assert inv.cost == pytest.approx(3975.40)


def test_summary_and_header_rows_excluded(csv_path: Path) -> None:
    cash, investments = parse_vanguard_csv(csv_path)
    # Balance and Cost footer rows must not appear; every row must have a real date
    assert all(isinstance(r.date, date) for r in cash)
    assert all(isinstance(r.date, date) for r in investments)


def test_empty_sections(tmp_path: Path) -> None:
    cash, investments = parse_vanguard_csv(_write_csv(tmp_path))
    assert cash == []
    assert investments == []


# ---------------------------------------------------------------------------
# contributions_to_date
# ---------------------------------------------------------------------------


def test_contributions_accumulate_on_deposits(csv_path: Path) -> None:
    cash, _ = parse_vanguard_csv(csv_path)
    assert cash[0].contributions_to_date == pytest.approx(4000.0)   # first deposit
    assert cash[1].contributions_to_date == pytest.approx(4000.0)   # buy — no change
    assert cash[2].contributions_to_date == pytest.approx(4000.0)   # interest — no change
    assert cash[3].contributions_to_date == pytest.approx(9000.0)   # second deposit
    assert cash[4].contributions_to_date == pytest.approx(9000.0)   # fee — no change


def test_deposit_detection_case_insensitive(tmp_path: Path) -> None:
    cash, _ = parse_vanguard_csv(
        _write_csv(tmp_path, cash_lines="01/01/2026,DEPOSIT VIA DIRECT CREDIT,1000.00,1000.00,,\n")
    )
    assert len(cash) == 1
    assert cash[0].contributions_to_date == pytest.approx(1000.0)


def test_two_deposits_same_date(tmp_path: Path) -> None:
    cash, _ = parse_vanguard_csv(_write_csv(tmp_path, cash_lines=(
        "01/01/2026,Deposit for Investment Purchases,1000.00,1000.00,,\n"
        "01/01/2026,Deposit via direct credit,500.00,1500.00,,\n"
    )))
    assert cash[0].contributions_to_date == pytest.approx(1000.0)
    assert cash[1].contributions_to_date == pytest.approx(1500.0)


# ---------------------------------------------------------------------------
# Investment cost with comma thousands-separator
# ---------------------------------------------------------------------------


def test_investment_cost_with_comma(tmp_path: Path) -> None:
    _, investments = parse_vanguard_csv(_write_csv(
        tmp_path,
        inv_lines='01/01/2026,Global Small-Cap Fund,Bought 4.74,4.74,490.55,"2,325.21"\n',
    ))
    assert len(investments) == 1
    assert investments[0].cost == pytest.approx(2325.21)


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def test_cp1252_encoded_file(tmp_path: Path) -> None:
    # cp1252 byte 0x96 maps to U+2013 (en-dash), which is not valid UTF-8.
    header = (
        b"ISA,,,,,\n\nCash Transactions,,,,,\n\nDate,Details,Amount,Balance,,\n"
        b"Balance,0.00,,,,\n\n\nInvestment Transactions,,,,,\n\n"
        b"Date,InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
    )
    fund_line = b"01/01/2026,FTSE North America\x96upcoming change,Bought 16,16.00,124.72,1995.45\n"
    p = tmp_path / "v.csv"
    p.write_bytes(header + fund_line + b"Cost,1995.45,,,,\n")
    _, investments = parse_vanguard_csv(p)
    assert len(investments) == 1
    assert "–" in investments[0].investment_name  # noqa: RUF001 — intentional en-dash
