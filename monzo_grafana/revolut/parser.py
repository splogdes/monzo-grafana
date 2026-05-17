"""Parse Revolut CSV exports into RevolutRecord dataclasses.

Expected CSV columns (as exported from the Revolut app):
    Type, Product, Started Date, Completed Date, Description,
    Amount, Fee, Currency, State, Balance
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

log = logging.getLogger(__name__)

_DATE_FMT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class RevolutRecord:
    type: str
    product: str
    started_at: datetime
    completed_at: datetime | None
    description: str
    amount: Decimal
    fee: Decimal
    currency: str
    state: str
    balance: Decimal | None
    source_file: str
    file_index: int


def _parse_dt(s: str) -> datetime | None:
    s = s.strip()
    if not s:
        return None
    return datetime.strptime(s, _DATE_FMT)


def _parse_decimal(s: str) -> Decimal | None:
    s = s.strip()
    if not s:
        return None
    return Decimal(s)


def parse_file(path: Path) -> list[RevolutRecord]:
    """Parse a Revolut CSV export, skipping REVERTED and non-GBP rows."""
    records: list[RevolutRecord] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if row["State"].strip() == "REVERTED":
                continue
            if row["Currency"].strip() != "GBP":
                continue
            started = _parse_dt(row["Started Date"])
            if started is None:
                log.warning("Row %d in %s has no Started Date — skipping", i, path.name)
                continue
            records.append(
                RevolutRecord(
                    type=row["Type"].strip(),
                    product=row["Product"].strip(),
                    started_at=started,
                    completed_at=_parse_dt(row["Completed Date"]),
                    description=row["Description"].strip(),
                    amount=Decimal(row["Amount"].strip()),
                    fee=_parse_decimal(row["Fee"]) or Decimal("0"),
                    currency=row["Currency"].strip(),
                    state=row["State"].strip(),
                    balance=_parse_decimal(row["Balance"]),
                    source_file=str(path),
                    file_index=i,
                )
            )
    return records


def parse_paths(paths: list[str]) -> list[RevolutRecord]:
    """Resolve a list of file/directory paths and parse all CSVs found."""
    resolved: list[Path] = []
    if not paths:
        resolved = sorted(p for p in Path.cwd().iterdir() if p.suffix.lower() == ".csv")
    else:
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                resolved.extend(sorted(q for q in p.iterdir() if q.suffix.lower() == ".csv"))
            elif p.is_file():
                resolved.append(p)
            else:
                log.warning("Skipping %s — not a file or directory", p)
    records: list[RevolutRecord] = []
    for f in resolved:
        batch = parse_file(f)
        log.info("  %s → %d record(s)", f.name, len(batch))
        records.extend(batch)
    return records
