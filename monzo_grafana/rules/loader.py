"""Load override rules, groups, and splits from ``categories.yaml``."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from .engine import parse_share

log = logging.getLogger(__name__)

ACTION_KEYS = ("category", "amortise", "my_share", "group", "offset_for_tx", "offset_for_group")
MATCHER_KEYS = ("transaction_id", "merchant", "merchant_re", "description_re")
AMORTISE_KEYS = ("amortise_months", "amortise_weeks", "amortise_days")


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open() as f:
            doc: dict[str, Any] = yaml.safe_load(f) or {}
        return doc
    except FileNotFoundError:
        return {}


def load_overrides(path: Path) -> list[dict[str, Any]]:
    """Load and pre-compile override rules.

    Matchers (in priority order): ``transaction_id``, ``merchant``,
    ``merchant_pattern``, ``description_pattern``.

    Actions: ``category``, ``amortise_months/_weeks/_days``, ``my_share``,
    ``group``, ``offset_for_tx``, ``offset_for_group``.
    """
    doc = _read_yaml(path)
    if not doc:
        log.info("No overrides file at %s — skipping", path)
        return []

    rules: list[dict[str, Any]] = []
    for raw in doc.get("overrides") or []:
        rule = _compile_rule(raw)
        if rule is not None:
            rules.append(rule)

    if rules:
        log.info("Loaded %d override rule(s)", len(rules))
    return rules


def _compile_rule(raw: dict[str, Any]) -> dict[str, Any] | None:
    rule: dict[str, Any] = {}
    for k in ("transaction_id", "merchant"):
        if k in raw:
            rule[k] = raw[k]
    if "merchant_pattern" in raw:
        rule["merchant_re"] = re.compile(raw["merchant_pattern"])
    if "description_pattern" in raw:
        rule["description_re"] = re.compile(raw["description_pattern"])
    if "category" in raw:
        rule["category"] = raw["category"]

    amortise_keys = [k for k in AMORTISE_KEYS if k in raw]
    if len(amortise_keys) > 1:
        log.warning("Multiple amortise_* keys in %r — using %s", raw, amortise_keys[0])
    if amortise_keys:
        key = amortise_keys[0]
        n = int(raw[key])
        if n < 1:
            log.warning("%s must be >= 1, got %s — skipping rule", key, n)
            return None
        rule["amortise"] = {"unit": key.removeprefix("amortise_"), "n": n}

    if "my_share" in raw:
        share = parse_share(raw["my_share"])
        if share is None or share <= 0:
            log.warning("Invalid my_share %r — skipping rule", raw.get("my_share"))
            return None
        rule["my_share"] = share

    for k in ("group", "offset_for_tx", "offset_for_group"):
        if raw.get(k):
            rule[k] = str(raw[k])

    if not any(k in rule for k in MATCHER_KEYS):
        log.warning("Skipping override without a matcher: %r", raw)
        return None
    if not any(k in rule for k in ACTION_KEYS):
        log.warning("Skipping override without an action: %r", raw)
        return None
    return rule


def load_splits(path: Path) -> list[dict[str, Any]]:
    """Top-level ``splits:`` section — list of ``{transaction_id, parts: [...]}``.

    Each part requires a numeric ``amount`` plus optional ``category``,
    ``group``, ``offset_for_tx``, ``offset_for_group``, ``note``. The
    sum-to-parent invariant is checked later in
    :func:`monzo_grafana.db.splits.sync_splits`; here we only validate
    structural well-formedness.
    """
    doc = _read_yaml(path)
    out: list[dict[str, Any]] = []
    for raw in doc.get("splits") or []:
        entry = _compile_split(raw)
        if entry is not None:
            out.append(entry)
    if out:
        log.info("Loaded %d split(s)", len(out))
    return out


def _compile_split(raw: dict[str, Any]) -> dict[str, Any] | None:
    tx_id = raw.get("transaction_id")
    if not tx_id:
        log.warning("Skipping split without transaction_id: %r", raw)
        return None
    parts_raw = raw.get("parts") or []
    if not parts_raw:
        log.warning("Skipping split for %s: no parts", tx_id)
        return None

    parts: list[dict[str, Any]] = []
    for p in parts_raw:
        if not isinstance(p, dict) or "amount" not in p:
            log.warning("Skipping split for %s: part missing amount: %r", tx_id, p)
            return None
        try:
            amount = float(p["amount"])
        except (TypeError, ValueError):
            log.warning("Skipping split for %s: bad amount %r", tx_id, p.get("amount"))
            return None
        parts.append({
            "amount": amount,
            "category": (p.get("category") or "").strip() or None,
            "group": (p.get("group") or "").strip() or None,
            "offset_for_tx": (p.get("offset_for_tx") or "").strip() or None,
            "offset_for_group": (p.get("offset_for_group") or "").strip() or None,
            "note": (p.get("note") or "").strip() or None,
        })
    return {"transaction_id": tx_id, "parts": parts}


def load_groups(path: Path) -> list[dict[str, Any]]:
    """Top-level ``groups:`` section — list of group dicts."""
    doc = _read_yaml(path)
    raw_groups = doc.get("groups") or {}
    return [
        {
            "id": gid,
            "kind": (body or {}).get("kind") or "project",
            "name": (body or {}).get("name") or gid,
            "starts_at": (body or {}).get("starts_at"),
            "ends_at": (body or {}).get("ends_at"),
            "budget": (body or {}).get("budget"),
            "amortise": bool((body or {}).get("amortise", False)),
        }
        for gid, body in raw_groups.items()
    ]
