"""Tests for the Vanguard ISA importer helpers."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from monzo_grafana.vanguard.importer import (
    _build_balance_rows,
    _build_tx_rows,
    _make_investment_id,
    import_csv,
)
from monzo_grafana.vanguard.parser import CashRow, InvestmentRow


def _cash(
    dt: date,
    details: str,
    amount: float = 0.0,
    balance: float = 0.0,
    contributions: float = 0.0,
) -> CashRow:
    return CashRow(date=dt, details=details, amount=amount, balance=balance,
                   contributions_to_date=contributions)


def _inv(
    dt: date,
    name: str = "S&P 500 UCITS ETF",
    details: str = "Bought 44 VUAG",
    quantity: float = 44.0,
    price: float = 90.35,
    cost: float = 3975.40,
) -> InvestmentRow:
    return InvestmentRow(date=dt, investment_name=name, transaction_details=details,
                         quantity=quantity, price=price, cost=cost)


@pytest.fixture
def mock_conn() -> MagicMock:
    cur = MagicMock()
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    conn._cur = cur
    return conn


_MINIMAL_CSV = """\
ISA,,,,,

Cash Transactions,,,,,

Date,Details,Amount,Balance,,
03/08/2025,Deposit for Investment Purchases,"4,000.00","4,000.00",,
04/08/2025,Bought 44 S&P 500 UCITS ETF,"-3,975.40",24.60,,
Balance,24.60,,,,



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


# ---------------------------------------------------------------------------
# _make_investment_id
# ---------------------------------------------------------------------------


def test_make_id_stable_and_formatted() -> None:
    r = _inv(date(2025, 8, 6), name="VUAG", cost=3975.40)
    tx_id = _make_investment_id(r)
    assert tx_id == _make_investment_id(r)                     # deterministic
    assert tx_id.startswith("vanguard_isa_20250806_")
    suffix = tx_id.removeprefix("vanguard_isa_20250806_")
    assert len(suffix) == 12 and all(c in "0123456789abcdef" for c in suffix)


def test_make_id_differs_on_name_and_cost() -> None:
    base = _inv(date(2025, 8, 6))
    assert _make_investment_id(base) != _make_investment_id(_inv(date(2025, 8, 6), name="Fund B"))
    assert _make_investment_id(base) != _make_investment_id(_inv(date(2025, 8, 6), cost=2000.0))


# ---------------------------------------------------------------------------
# _build_balance_rows
# ---------------------------------------------------------------------------


def test_balance_rows_deposits_only_sorted() -> None:
    rows = [
        _cash(date(2025, 9, 29), "Deposit via direct credit", contributions=9000.0),
        _cash(date(2025, 8, 3),  "Deposit for Investment Purchases", contributions=4000.0),
        _cash(date(2025, 8, 4),  "Bought 44 VUAG", contributions=4000.0),
        _cash(date(2025, 9, 1),  "Cash Account Interest", contributions=4000.0),
        _cash(date(2025, 11, 3), "Account Fee for the period", contributions=9000.0),
    ]
    result = _build_balance_rows(rows)
    assert [r[0] for r in result] == ["2025-08-03", "2025-09-29"]
    assert result[0][2] == pytest.approx(4000.0)
    assert result[1][2] == pytest.approx(9000.0)


def test_balance_rows_tuple_structure() -> None:
    result = _build_balance_rows(
        [_cash(date(2025, 8, 3), "Deposit for Investment Purchases", contributions=4000.0)]
    )
    observed_at, balance_placeholder, contributions = result[0]
    assert observed_at == "2025-08-03"
    assert balance_placeholder == pytest.approx(4000.0)
    assert contributions == pytest.approx(4000.0)


def test_balance_rows_same_date_last_wins() -> None:
    rows = [
        _cash(date(2026, 1, 1), "Deposit for Investment Purchases", contributions=1000.0),
        _cash(date(2026, 1, 1), "Deposit via direct credit", contributions=1500.0),
    ]
    result = _build_balance_rows(rows)
    assert len(result) == 1
    assert result[0][2] == pytest.approx(1500.0)


def test_balance_rows_case_insensitive_and_empty() -> None:
    assert len(_build_balance_rows(
        [_cash(date(2026, 1, 1), "DEPOSIT VIA DIRECT CREDIT", contributions=1000.0)]
    )) == 1
    assert _build_balance_rows([]) == []


# ---------------------------------------------------------------------------
# _build_tx_rows
# ---------------------------------------------------------------------------


def test_tx_row_structure() -> None:
    row = _build_tx_rows([_inv(date(2025, 8, 6), name="VUAG", details="Bought 44",
                               quantity=44.0, price=90.35, cost=3975.40)])[0]
    assert row[0].startswith("vanguard_isa_20250806_")
    assert row[1] == datetime(2025, 8, 6, 0, 0, 0, tzinfo=UTC)
    assert row[2] == pytest.approx(-3975.40)
    assert row[3] == "VUAG"
    assert row[4] == "Bought 44"
    assert row[5] == "investment"
    assert row[6] == "internal"
    assert row[7] is None   # amortise_days
    raw = json.loads(row[8])
    assert raw["source"] == "vanguard_isa"
    assert raw["investment_name"] == "VUAG"
    assert raw["quantity"] == pytest.approx(44.0)
    assert raw["price"] == pytest.approx(90.35)
    assert raw["cost"] == pytest.approx(3975.40)
    assert row[9] == "vanguard_isa"
    assert row[10] == pytest.approx(1.0)
    assert row[11] is None  # group_id
    assert row[12] is None  # offset_for_tx
    assert row[13] is None  # offset_for_group


def test_tx_rows_count_and_empty() -> None:
    inv = [_inv(date(2025, 8, 6)), _inv(date(2025, 10, 3), name="ESG Fund", cost=5000.0)]
    assert len(_build_tx_rows(inv)) == 2
    assert _build_tx_rows([]) == []


# ---------------------------------------------------------------------------
# import_csv — smoke test with mocked DB
# ---------------------------------------------------------------------------


def test_import_csv_calls_executemany_and_commits(mock_conn: MagicMock, csv_path: Path) -> None:
    mock_cfg = MagicMock()
    mock_cfg.pg_dsn = "postgresql://fake"

    with patch("monzo_grafana.vanguard.importer.psycopg.connect", return_value=mock_conn):
        import_csv(mock_cfg, csv_path)

    cur = mock_conn._cur
    assert cur.executemany.call_count == 2
    mock_conn.commit.assert_called_once()

    calls: list[Any] = cur.executemany.call_args_list
    balance_sql, balance_data = calls[0].args
    tx_sql, tx_data = calls[1].args

    assert "account_balances" in balance_sql
    assert "transactions" in tx_sql
    assert len(balance_data) == 1 and balance_data[0][0] == "2025-08-03"
    assert len(tx_data) == 1 and tx_data[0][2] == pytest.approx(-3975.40)
