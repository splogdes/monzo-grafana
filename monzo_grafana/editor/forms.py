"""Parse and validate form submissions from the editor's HTML forms."""

from __future__ import annotations

import contextlib
from typing import Any

ACTION_KEYS = (
    "category", "amortise_months", "amortise_weeks", "amortise_days",
    "my_share", "group", "offset_for_tx", "offset_for_group",
)
MATCH_KEYS = ("transaction_id", "merchant", "merchant_pattern", "description_pattern")


def build_rule_from_form(form: dict[str, str]) -> dict[str, Any] | None:
    rule: dict[str, Any] = {}

    match_by = form.get("match_by", "merchant")
    if match_by not in MATCH_KEYS:
        return None

    # The /add form may carry the matcher in tx_id / merchant rather than the
    # generic match_value used by /edit.
    match_value = form.get("match_value", "").strip()
    if not match_value:
        if match_by == "transaction_id":
            match_value = form.get("tx_id", "").strip()
        elif match_by == "merchant":
            match_value = form.get("merchant", "").strip()
    if not match_value:
        return None

    rule[match_by] = match_value

    category = form.get("category", "").strip()
    if category:
        rule["category"] = category

    unit = form.get("amortise_unit", "")
    n_raw = form.get("amortise_n", "").strip()
    if unit and n_raw:
        try:
            n = int(n_raw)
            if n >= 1:
                rule[f"amortise_{unit}"] = n
        except ValueError:
            pass

    my_share = form.get("my_share", "").strip()
    if my_share:
        rule["my_share"] = my_share

    for k in ("group", "offset_for_tx", "offset_for_group"):
        v = form.get(k, "").strip()
        if v:
            rule[k] = v

    if not any(k in rule for k in ACTION_KEYS):
        return None

    return rule


def build_group_from_form(form: dict[str, str]) -> tuple[str, dict[str, Any]] | None:
    gid = form.get("id", "").strip()
    if not gid:
        return None
    body: dict[str, Any] = {
        "kind": form.get("kind", "").strip() or "project",
        "name": form.get("name", "").strip() or gid,
    }
    for k in ("starts_at", "ends_at"):
        v = form.get(k, "").strip()
        if v:
            body[k] = v
    budget = form.get("budget", "").strip()
    if budget:
        with contextlib.suppress(ValueError):
            body["budget"] = float(budget)
    if form.get("amortise") in ("1", "on", "true"):
        body["amortise"] = True
    return gid, body


def parse_split_parts(
    raw_qs: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], str | None]:
    """Pull part rows out of the multi-valued split form.

    Returns ``(parts, error_message)`` — exactly one is empty.
    """
    amounts = raw_qs.get("amount", [])
    categories = raw_qs.get("category", [])
    groups = raw_qs.get("group", [])
    offsets_tx = raw_qs.get("offset_for_tx", [])
    offsets_grp = raw_qs.get("offset_for_group", [])
    notes = raw_qs.get("note", [])

    def at(lst: list[str], i: int) -> str:
        return lst[i].strip() if i < len(lst) and lst[i] else ""

    parts: list[dict[str, Any]] = []
    for i, raw_amount in enumerate(amounts):
        a_raw = (raw_amount or "").strip()
        if not a_raw:
            continue
        try:
            amt = float(a_raw)
        except ValueError:
            return [], f"part {i + 1}: amount {a_raw!r} is not a number"
        part: dict[str, Any] = {"amount": round(amt, 2)}
        for key, lst in (
            ("category", categories),
            ("group", groups),
            ("offset_for_tx", offsets_tx),
            ("offset_for_group", offsets_grp),
            ("note", notes),
        ):
            v = at(lst, i)
            if v:
                part[key] = v
        parts.append(part)

    return parts, None
