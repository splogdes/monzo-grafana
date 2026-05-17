"""Per-route handler functions producing HTML response bodies."""

from __future__ import annotations

from typing import Any

from jinja2 import Environment

from . import queries, render
from .settings import EditorSettings
from .store import RuleStore


def _common_options(settings: EditorSettings, store: RuleStore) -> dict[str, Any]:
    """Datalist contents shared by the rule and split forms."""
    doc = store.load()
    db_cats = queries.fetch_db_categories(settings.pg_dsn)
    yaml_cats = render.yaml_categories_from_doc(doc)
    db_groups = queries.fetch_db_groups(settings.pg_dsn)
    return {
        "category_options": render.category_suggestions(db_cats, yaml_cats),
        "group_options": render.group_id_suggestions(db_groups, store.yaml_groups()),
    }


def render_rules_list(
    env: Environment, settings: EditorSettings, store: RuleStore, *, message: str = "",
) -> bytes:
    rules = store.load().get("overrides", [])
    tx_ids = [r["transaction_id"] for r in rules if r.get("transaction_id")]
    tx_details = queries.fetch_transactions_by_ids(settings.pg_dsn, tx_ids)
    annotated = [render.annotate_rule_for_list(r) for r in rules]
    return env.get_template("rules_list.html").render(
        title="Override rules",
        message=message,
        rules=annotated,
        tx_details=tx_details,
        wide=False,
    ).encode("utf-8")


def render_rule_form(
    env: Environment,
    settings: EditorSettings,
    store: RuleStore,
    *,
    mode: str,
    params: dict[str, str] | None = None,
    index: int | None = None,
    existing: dict[str, Any] | None = None,
) -> bytes:
    params = params or {}
    recent_txs = queries.fetch_recent_transactions(settings.pg_dsn)
    merchants = queries.fetch_merchants(settings.pg_dsn)

    if mode == "new":
        tx_id = params.get("tx_id", "")
        merchant = params.get("merchant", "")
        description = params.get("description", "")
        amount = params.get("amount", "")
        category = ""
        my_share = ""
        group = ""
        offset_for_tx = ""
        offset_for_group = ""
        amortise_unit = ""
        amortise_n = ""

        if tx_id:
            match_by, match_value = "transaction_id", tx_id
        elif merchant:
            match_by, match_value = "merchant", merchant
        else:
            match_by, match_value = "transaction_id", ""

        context_tx: dict[str, Any] | None = None
        if tx_id or merchant:
            context_tx = {
                "merchant": merchant,
                "amount_label": amount,
                "description": description,
                "date": None,
                "category": params.get("category", ""),
            }

        offset_for_tx_label = ""
        action = "/add"
        cancel_href = "javascript:window.close()" if tx_id else "/"
        auto_close = "1" if tx_id else ""
    else:
        rule = existing or {}
        tx_id = rule.get("transaction_id", "")
        merchant = rule.get("merchant", "")
        merchant_pattern = rule.get("merchant_pattern", "")
        description_pattern = rule.get("description_pattern", "")
        category = rule.get("category", "")
        my_share = str(rule.get("my_share", "") or "")
        group = rule.get("group", "")
        offset_for_tx = rule.get("offset_for_tx", "")
        offset_for_group = rule.get("offset_for_group", "")

        if tx_id:
            match_by, match_value = "transaction_id", tx_id
        elif merchant:
            match_by, match_value = "merchant", merchant
        elif merchant_pattern:
            match_by, match_value = "merchant_pattern", merchant_pattern
        elif description_pattern:
            match_by, match_value = "description_pattern", description_pattern
        else:
            match_by, match_value = "merchant", ""

        amortise_unit = ""
        amortise_n = ""
        for unit in ("months", "weeks", "days"):
            key = f"amortise_{unit}"
            if key in rule:
                amortise_unit = unit
                amortise_n = str(rule[key])
                break

        context_tx = None
        lookup_ids = [i for i in {tx_id, offset_for_tx} if i]
        details = queries.fetch_transactions_by_ids(settings.pg_dsn, lookup_ids)
        if tx_id and (d := details.get(tx_id)):
            context_tx = {
                "merchant": d["merchant"],
                "amount_label": f"£{d['amount']:.2f}",
                "description": d["description"],
                "date": d["date"],
                "category": d["category"],
            }
        if offset_for_tx and (d := details.get(offset_for_tx)):
            offset_for_tx_label = f"{d['date']} · {d['merchant'] or '?'} · £{abs(d['amount']):.2f}"
        else:
            offset_for_tx_label = ""

        action = "/update"
        cancel_href = "/"
        auto_close = ""

    ctx = {
        "title": "Add rule" if mode == "new" else f"Edit rule #{index}",
        "mode": mode,
        "index": index,
        "action": action,
        "tx_id": tx_id,
        "context_tx": context_tx,
        "match_by": match_by,
        "match_value": match_value,
        "category": category,
        "amortise_unit": amortise_unit,
        "amortise_n": amortise_n,
        "my_share": my_share,
        "group": group,
        "offset_for_tx": offset_for_tx,
        "offset_for_tx_label": offset_for_tx_label,
        "offset_for_group": offset_for_group,
        "recent_txs": recent_txs,
        "merchants": merchants,
        "tx_to_merchant_json": render.tx_to_merchant_json(recent_txs),
        "cancel_href": cancel_href,
        "auto_close": auto_close,
        "wide": False,
        **_common_options(settings, store),
    }
    return env.get_template("rule_form.html").render(**ctx).encode("utf-8")


def render_done(env: Environment, summary: str) -> bytes:
    return env.get_template("done.html").render(
        title="Saved", message="", summary=summary, wide=False
    ).encode("utf-8")


def render_message(env: Environment, title: str, body_html: str) -> bytes:
    return env.get_template("message.html").render(
        title=title, message="", body_html=body_html, wide=False
    ).encode("utf-8")


def render_groups_page(
    env: Environment, settings: EditorSettings, store: RuleStore, *, message: str = "",
) -> bytes:
    groups = [
        {"id": gid, "summary": render.summarise_group(body)}
        for gid, body in sorted(store.yaml_groups().items())
    ]
    return env.get_template("groups.html").render(
        title="Groups", message=message, groups=groups, wide=False,
    ).encode("utf-8")


def render_splits_index(
    env: Environment, settings: EditorSettings, store: RuleStore, *, message: str = "",
) -> bytes:
    splits = store.all_splits()
    tx_ids: list[str] = [s["transaction_id"] for s in splits if s.get("transaction_id")]
    tx_details = queries.fetch_transactions_by_ids(settings.pg_dsn, tx_ids)
    enriched = [
        {
            "transaction_id": s.get("transaction_id", ""),
            "detail": tx_details.get(s.get("transaction_id", "")),
            "part_lines": render.split_part_lines(s.get("parts") or []),
        }
        for s in splits
    ]
    return env.get_template("splits_index.html").render(
        title="Splits", message=message, splits=enriched, wide=False,
    ).encode("utf-8")


def render_split_form(
    env: Environment,
    settings: EditorSettings,
    store: RuleStore,
    tx_id: str,
    *,
    message: str = "",
) -> bytes:
    parent = queries.fetch_transactions_by_ids(settings.pg_dsn, [tx_id]).get(tx_id)
    if not parent:
        return env.get_template("split_missing.html").render(
            title="Split — unknown transaction", message="", tx_id=tx_id, wide=False,
        ).encode("utf-8")

    existing = store.fetch_split(tx_id)
    if existing and existing.get("parts"):
        parts = existing["parts"]
    else:
        parts = [
            {"amount": parent["amount"], "category": "", "group": "",
             "offset_for_tx": "", "offset_for_group": "", "note": ""},
            {"amount": 0.0, "category": "", "group": "",
             "offset_for_tx": "", "offset_for_group": "", "note": ""},
        ]

    recent_outgoings = queries.fetch_recent_outgoings(settings.pg_dsn)
    ctx = {
        "title": "Split transaction",
        "message": message,
        "tx_id": tx_id,
        "parent": parent,
        "parts": parts,
        "recent_outgoings": recent_outgoings,
        "offset_tx_ids": {t["id"] for t in recent_outgoings},
        "offset_tx_base_html": render.offset_tx_base_html_for_js(recent_outgoings),
        "existing": existing is not None,
        "wide": True,
        **_common_options(settings, store),
    }
    return env.get_template("split_form.html").render(**ctx).encode("utf-8")
