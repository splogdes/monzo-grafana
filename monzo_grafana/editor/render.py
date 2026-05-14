"""Jinja2 setup and the small data-shaping helpers feeding each template."""

from __future__ import annotations

import html
import json
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

COMMON_CATEGORIES = (
    "groceries", "eating_out", "transport", "bills", "subscriptions",
    "entertainment", "shopping", "holidays", "personal_care",
    "savings", "transfers", "general", "internal",
)


def build_environment() -> Environment:
    return Environment(
        loader=PackageLoader("monzo_grafana.editor", "templates"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=False,
        lstrip_blocks=False,
    )


def category_suggestions(db_cats: list[str], yaml_cats: list[str]) -> list[str]:
    return sorted(set(COMMON_CATEGORIES) | set(db_cats) | set(yaml_cats))


def yaml_categories_from_doc(doc: dict[str, Any]) -> list[str]:
    return [
        rule["category"]
        for rule in doc.get("overrides") or []
        if rule.get("category")
    ]


def group_id_suggestions(db_groups: list[dict[str, Any]], yaml_groups: dict[str, Any]) -> list[str]:
    db_ids = [g["id"] for g in db_groups]
    yaml_ids = list(yaml_groups.keys())
    return sorted(set(db_ids) | set(yaml_ids))


def annotate_rule_for_list(rule: dict[str, Any]) -> dict[str, Any]:
    """Add ``matcher_key`` and ``action_summary`` fields for the rules-list template."""
    matcher_key: str | None = None
    if "transaction_id" not in rule:
        for k in ("merchant", "merchant_pattern", "description_pattern"):
            if k in rule:
                matcher_key = k
                break
    actions: list[str] = []
    if "category" in rule:
        actions.append(f"category={rule['category']}")
    for k in ("amortise_months", "amortise_weeks", "amortise_days",
              "my_share", "group", "offset_for_tx", "offset_for_group"):
        if k in rule:
            actions.append(f"{k}={rule[k]}")
    return {**rule, "matcher_key": matcher_key, "action_summary": ", ".join(actions)}


def summarise_group(body: dict[str, Any] | None) -> str:
    body = body or {}
    attrs: list[str] = []
    for k in ("kind", "name", "starts_at", "ends_at", "budget"):
        if body.get(k) not in (None, ""):
            attrs.append(f"{k}={body[k]}")
    if body.get("amortise"):
        attrs.append("amortise=true")
    return ", ".join(attrs)


def split_part_lines(parts: list[dict[str, Any]]) -> list[str]:
    """Pre-render the textual summary lines shown for each part on the splits index.

    HTML-escaped here (rather than autoescaped in the template) because the
    template renders the list with the ``safe`` filter to keep each part on
    its own line via ``<br>``.
    """
    out: list[str] = []
    for p in parts:
        bits = [f"£{float(p.get('amount', 0)):.2f}"]
        for k in ("category", "group", "offset_for_tx", "offset_for_group"):
            if p.get(k):
                bits.append(f"{k}={p[k]}")
        if p.get("note"):
            bits.append(f"note={p['note']}")
        out.append(html.escape(" · ".join(bits)))
    return out


def offset_tx_base_html_for_js(recent_outgoings: list[dict[str, Any]]) -> str:
    """Pre-render the HTML option list injected into the split form's JS."""
    return "".join(
        f'<option value="{html.escape(t["id"])}">'
        f'{html.escape(str(t["date"]))} · {html.escape(t["merchant"])} · '
        f'£{t["amount"]:.2f}</option>'
        for t in recent_outgoings
    )


def tx_to_merchant_json(recent_txs: list[dict[str, Any]]) -> str:
    return json.dumps({t["id"]: t["merchant"] for t in recent_txs})
