"""Parse Vanguard ISA CSV exports.

The CSV has two logical sections separated by blank rows:
  - Cash Transactions  (Date, Details, Amount, Balance)
  - Investment Transactions  (Date, InvestmentName, TransactionDetails, Quantity, Price, Cost)

Numbers use comma thousands-separators and may be quoted.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class CashRow:
    date: date
    details: str
    amount: float
    balance: float
    contributions_to_date: float  # running sum of deposit rows up to this point


@dataclass
class InvestmentRow:
    date: date
    investment_name: str
    transaction_details: str
    quantity: float
    price: float
    cost: float  # positive; store as -cost in transactions


def _to_float(s: str) -> float:
    return float(s.replace(",", "").strip())


def _parse_date(s: str) -> date:
    day, month, year = s.strip().split("/")
    return date(int(year), int(month), int(day))


def parse_vanguard_csv(path: Path) -> tuple[list[CashRow], list[InvestmentRow]]:
    cash: list[CashRow] = []
    investments: list[InvestmentRow] = []

    rows: list[list[str]] = []
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with path.open(newline="", encoding=enc) as fh:
                rows = list(csv.reader(fh))
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Could not decode {path} with utf-8, cp1252, or latin-1")

    section = None
    skip_next = False  # True after the section header — skip the column-name row
    running_contributions = 0.0

    for row in rows:
        if not any(cell.strip() for cell in row):
            continue

        first = row[0].strip()

        if first == "Cash Transactions":
            section = "cash"
            skip_next = True
            continue

        if first == "Investment Transactions":
            section = "investment"
            skip_next = True
            continue

        if skip_next:
            skip_next = False
            continue

        # Summary/footer rows that end each section
        if first in ("Balance", "Cost"):
            section = None
            continue

        if section == "cash":
            try:
                tx_date = _parse_date(first)
            except (ValueError, IndexError):
                continue
            details = row[1].strip()
            amount = _to_float(row[2])
            balance = _to_float(row[3])
            if "deposit" in details.lower():
                running_contributions += amount
            cash.append(CashRow(
                date=tx_date,
                details=details,
                amount=amount,
                balance=balance,
                contributions_to_date=running_contributions,
            ))

        elif section == "investment":
            try:
                tx_date = _parse_date(first)
            except (ValueError, IndexError):
                continue
            investments.append(InvestmentRow(
                date=tx_date,
                investment_name=row[1].strip(),
                transaction_details=row[2].strip(),
                quantity=_to_float(row[3]),
                price=_to_float(row[4]),
                cost=_to_float(row[5]),
            ))

    return cash, investments
