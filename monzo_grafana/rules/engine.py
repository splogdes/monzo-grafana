"""Rule matching and amortisation arithmetic."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from dateutil.relativedelta import relativedelta

SPLIT_ANNOTATION_WINDOW_HOURS = 12


def parse_share(value: Any) -> float | None:
    """Parse a ``my_share`` value: numeric, ``"0.5"``, or ``"n/d"`` fractional form."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    if "/" in s:
        num, den = s.split("/", 1)
        try:
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(s)
    except ValueError:
        return None


def find_override(
    tx_id: str,
    merchant: str,
    description: str,
    rules: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the most specific matching rule, or ``None``.

    Specificity order: ``transaction_id`` > ``merchant`` exact >
    ``merchant_pattern`` > ``description_pattern``. So a per-transaction
    override always beats a merchant rule, regardless of YAML order. List
    order only breaks ties within the same matcher class.
    """
    for rule in rules:
        if "transaction_id" in rule and tx_id == rule["transaction_id"]:
            return rule
    for rule in rules:
        if "merchant" in rule and merchant == rule["merchant"]:
            return rule
    for rule in rules:
        if "merchant_re" in rule and rule["merchant_re"].search(merchant):
            return rule
    for rule in rules:
        if "description_re" in rule and rule["description_re"].search(description):
            return rule
    return None


def merchant_name(tx: dict[str, Any]) -> str:
    merchant = tx.get("merchant") or {}
    return str(
        merchant.get("name")
        or (tx.get("counterparty") or {}).get("name")
        or tx.get("description", "")
    )


def tx_time(tx: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(tx["created"].replace("Z", "+00:00"))


def tx_description(tx: dict[str, Any]) -> str:
    return str(tx.get("notes") or tx.get("description", ""))


def amortise_total_days(spec: dict[str, Any] | None, base_ts: datetime) -> int:
    """Convert an amortise spec (``{"unit": ..., "n": ...}``) to a day-count."""
    if not spec:
        return 0
    unit = spec["unit"]
    n = int(spec["n"])
    if unit == "months":
        return ((base_ts + relativedelta(months=n)) - base_ts).days
    if unit == "weeks":
        return n * 7
    if unit == "days":
        return n
    return 0


def _find_split_factor(tx_at: datetime, annotations: list[dict[str, Any]]) -> int:
    for ann in annotations:
        delta = abs((tx_at - ann["time"]).total_seconds())
        if delta <= SPLIT_ANNOTATION_WINDOW_HOURS * 3600:
            return int(ann["split"])
    return 1


def resolve_amortise_days(
    occurred_at: datetime,
    rule: dict[str, Any] | None,
    annotations: list[dict[str, Any]],
) -> int | None:
    """Effective ``amortise_days`` for a transaction. Rule wins over annotation.

    Annotations are interpreted as a month count. Returns ``None`` when the
    row should remain a single ledger entry.
    """
    if rule and "amortise" in rule:
        days = amortise_total_days(rule["amortise"], occurred_at)
    else:
        n = _find_split_factor(occurred_at, annotations)
        days = amortise_total_days({"unit": "months", "n": n}, occurred_at) if n > 1 else 0
    return days if days > 1 else None


def rule_to_columns(
    rule: dict[str, Any] | None,
) -> tuple[float, str | None, str | None, str | None]:
    """Pull ``(my_share, group_id, offset_for_tx, offset_for_group)`` from a rule."""
    if not rule:
        return 1.0, None, None, None
    return (
        float(rule.get("my_share") or 1.0),
        rule.get("group"),
        rule.get("offset_for_tx"),
        rule.get("offset_for_group"),
    )
