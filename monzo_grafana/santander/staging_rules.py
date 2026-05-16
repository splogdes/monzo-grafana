"""Load and apply Santander merchant rules at import / retag time.

The rules file is a plain YAML list. Each entry has three keys::

    - canonical: <Title Case Name>
      category:  <category>
      regex:     <python regex>

List order is significant — first-match-wins priority. Regexes are tested
against the output of :func:`prepare_for_match` so all rules see the same
title-cased, boilerplate-stripped form.

Default location: ``data/santander_rules.yaml`` (override via the
``SANTANDER_RULES_FILE`` env var).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..rules import find_override
from .merchant import clean_merchant, prepare_for_match

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    canonical: str
    category: str
    regex: str
    compiled: re.Pattern[str]


def parse_rules(path: Path) -> list[Rule]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{path}: top-level YAML must be a list of rules")
    out: list[Rule] = []
    for i, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: rule #{i} is not a mapping")
        missing = {k for k in ("canonical", "category", "regex") if k not in entry}
        if missing:
            raise ValueError(f"{path}: rule #{i} missing keys: {sorted(missing)}")
        try:
            compiled = re.compile(entry["regex"])
        except re.error as exc:
            raise ValueError(
                f"{path}: rule #{i} ({entry['canonical']!r}) has invalid regex "
                f"{entry['regex']!r}: {exc}"
            ) from exc
        out.append(
            Rule(
                canonical=entry["canonical"],
                category=entry["category"],
                regex=entry["regex"],
                compiled=compiled,
            )
        )
    return out


def load_rules(path: Path) -> list[Rule]:
    """Parse the rules file, or return [] if it doesn't exist."""
    if not path.is_file():
        log.warning("Santander rules file %s not found — no staging rules applied", path)
        return []
    rules = parse_rules(path)
    log.info("Loaded %d Santander rule(s) from %s", len(rules), path)
    return rules


def apply_rules(rules: list[Rule], prepared_description: str) -> Rule | None:
    """Return the first matching rule, or ``None``."""
    for rule in rules:
        if rule.compiled.search(prepared_description):
            return rule
    return None


def resolve(
    tx_id: str,
    description: str,
    staging_rules: list[Rule],
    overrides: list[dict[str, Any]],
    fallback_category: str,
) -> tuple[str, str, dict[str, Any] | None]:
    """Compute ``(merchant, category, override_rule)`` for a Santander row.

    Precedence:
    1. ``categories.yaml`` override (per-tx, merchant, or pattern) — sets category.
    2. Staging rule canonical — sets merchant.
    3. Staging rule category — used when no override category matched.
    4. ``fallback_category`` (the row's existing category, e.g. ``monzo_category``
       on retag or ``"unknown"`` on first import).
    """
    staging_match = apply_rules(staging_rules, prepare_for_match(description))
    merchant = staging_match.canonical if staging_match else clean_merchant(description)
    override = find_override(tx_id, merchant, description, overrides)
    category = (
        (override and override.get("category"))
        or (staging_match.category if staging_match else None)
        or fallback_category
    )
    return merchant, category, override
