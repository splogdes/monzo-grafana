"""Load and apply Revolut merchant staging rules at import / retag time.

Same YAML schema as santander/staging_rules.py::

    - canonical: <Title Case Name>
      category:  <category>
      regex:     <python regex>

Unlike Santander, Revolut descriptions are already clean, so rules are
matched against the raw description without any boilerplate stripping.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..rules import find_override
from ..santander.staging_rules import Rule, apply_rules, parse_rules

log = logging.getLogger(__name__)


def load_rules(path: Path) -> list[Rule]:
    """Parse the rules file, or return [] if it doesn't exist."""
    if not path.is_file():
        log.warning("Revolut rules file %s not found — no staging rules applied", path)
        return []
    rules = parse_rules(path)
    log.info("Loaded %d Revolut rule(s) from %s", len(rules), path)
    return rules


def resolve(
    tx_id: str,
    description: str,
    staging_rules: list[Rule],
    overrides: list[dict[str, Any]],
    fallback_category: str,
) -> tuple[str, str, dict[str, Any] | None]:
    """Compute ``(merchant, category, override_rule)`` for a Revolut row."""
    staging_match = apply_rules(staging_rules, description)
    merchant = staging_match.canonical if staging_match else description.strip()
    override = find_override(tx_id, merchant, description, overrides)
    category = (
        (override and override.get("category"))
        or (staging_match.category if staging_match else None)
        or fallback_category
    )
    return merchant, category, override
