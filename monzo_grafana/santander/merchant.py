"""Boilerplate stripping for Santander descriptions.

Santander statement exports prefix transactions with ``CARD PAYMENT TO``,
append currency-conversion tails, splice in ``(VIA …)`` markers, and so on.
:func:`prepare_for_match` returns the cleaned, title-cased form against which
the rules in ``data/santander_rules.yaml`` are tested at import / retag time.

:func:`clean_merchant` is the fallback when no YAML rule matches — it just
returns the prepared description as-is.
"""

from __future__ import annotations

import re

_AMP_ENTITY = re.compile(r"&amp;", re.IGNORECASE)
_TRAILING_ON_DATE = re.compile(r",?\s*ON\s+\d{2}-\d{2}-\d{4}\s*$", re.IGNORECASE)
_VIA_TAG = re.compile(r"\s*\(VIA [^)]+\)\s*", re.IGNORECASE)
_LEADING_CARD_PAYMENT = re.compile(r"^CARD PAYMENT TO\s+", re.IGNORECASE)
_LEADING_FASTER_PAYMENT = re.compile(
    r"^FASTER PAYMENTS? (RECEIPT|PAYMENT) REF\.?\s*", re.IGNORECASE
)
# `Tran` / `Trans` at the end accounts for Santander truncating "Transaction".
_CURRENCY_TAIL = re.compile(
    r"\s*,?\s*[\d.]+\s*[A-Z]{3},?\s*RATE\s+[\d./A-Z]+"
    r"(?:\s*ON\s+\d{2}-\d{2}-\d{4})?(?:\s*Non-Sterling(?:\s+\w+)?)?\s*$",
    re.IGNORECASE,
)
_WS = re.compile(r"\s+")


def normalise_description(description: str) -> str:
    """Replace HTML entities Santander leaves in descriptions (``&amp;`` → ``&``)."""
    return _AMP_ENTITY.sub("&", description)


def prepare_for_match(description: str) -> str:
    """Title-cased, boilerplate-stripped form that YAML rules are tested against.

    Shared by :func:`clean_merchant` and the retag / dry-run paths so all three
    see identical input.
    """
    s = normalise_description(description).strip()
    s = _TRAILING_ON_DATE.sub("", s)
    s = _CURRENCY_TAIL.sub("", s)
    s = _VIA_TAG.sub(" ", s)
    s = _LEADING_CARD_PAYMENT.sub("", s)
    s = _LEADING_FASTER_PAYMENT.sub("", s)
    s = _WS.sub(" ", s).strip(" ,")
    return s.title()


def clean_merchant(description: str) -> str:
    """Fallback merchant name when no YAML rule matches — just the prepared form."""
    return prepare_for_match(description)
