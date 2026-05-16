"""Parse Santander downloadable statement `.txt` exports.

Each file has header lines (``From:``/``Account:``/``Date:``-of-export)
followed by repeating 4-line record blocks separated by blank lines:

    Date: dd/mm/yyyy
    Description: <free text>
    Amount: <signed decimal>      (negative = spend)
    Balance: <decimal>            (running balance after the tx)

Label/value separator is U+00A0 (non-breaking space). Records appear in
date-descending order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class SantanderRecord:
    date: Date
    description: str
    amount: Decimal
    balance: Decimal
    source_file: str
    file_index: int


_FIELD_RE = re.compile(r"^(Date|Description|Amount|Balance)\s*:\s*(.*)$")


def _normalise(line: str) -> str:
    return line.replace("\xa0", " ").replace("\t", " ").rstrip("\r\n").strip()


def parse_text(text: str, source_file: str) -> list[SantanderRecord]:
    records: list[SantanderRecord] = []
    current: dict[str, str] = {}
    index = 0
    for raw_line in text.splitlines():
        line = _normalise(raw_line)
        if not line:
            if current:
                rec = _finalise(current, source_file, index)
                if rec is not None:
                    records.append(rec)
                    index += 1
                current = {}
            continue
        m = _FIELD_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key == "Date" and current:
            rec = _finalise(current, source_file, index)
            if rec is not None:
                records.append(rec)
                index += 1
            current = {}
        current[key] = value
    if current:
        rec = _finalise(current, source_file, index)
        if rec is not None:
            records.append(rec)
    return records


def _finalise(block: dict[str, str], source_file: str, file_index: int) -> SantanderRecord | None:
    required = ("Date", "Description", "Amount", "Balance")
    if not all(k in block for k in required):
        return None
    try:
        d = datetime.strptime(block["Date"], "%d/%m/%Y").date()
    except ValueError:
        return None
    try:
        amount = Decimal(block["Amount"].replace(",", ""))
        balance = Decimal(block["Balance"].replace(",", ""))
    except (ValueError, ArithmeticError):
        return None
    return SantanderRecord(
        date=d,
        description=block["Description"],
        amount=amount,
        balance=balance,
        source_file=source_file,
        file_index=file_index,
    )


def parse_file(path: Path) -> list[SantanderRecord]:
    # Santander exports are latin-1 (single byte 0xA0 = NBSP as the label/value
    # separator). Reading as UTF-8 corrupts those bytes into the replacement
    # character, which breaks field detection.
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return parse_text(text, source_file=path.name)
