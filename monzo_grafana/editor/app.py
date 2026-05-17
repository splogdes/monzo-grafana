"""HTTP server entry point for the rule editor.

Builds a small dispatch table for ``GET`` and ``POST`` routes and delegates
each request to a handler function. Replaces the legacy ``Handler.do_GET``
monkey-patch in the original ``rule_editor.py``.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from jinja2 import Environment

from ..logging_setup import configure_logging
from . import handlers, queries
from .forms import build_group_from_form, build_rule_from_form, parse_split_parts
from .render import build_environment
from .settings import EditorSettings
from .store import RuleStore
from .triggers import trigger

log = logging.getLogger(__name__)

SPLIT_SUM_TOLERANCE = 0.01

Response = tuple[int, bytes, dict[str, str]]  # status, body, extra headers


class _Editor:
    def __init__(self, settings: EditorSettings) -> None:
        self.settings = settings
        self.store = RuleStore(settings.categories_file)
        self.env: Environment = build_environment()

    # --- triggers ----------------------------------------------------------

    def _spawn(self, action: str) -> None:
        trigger(action, self.settings.trigger_url)

    # --- responses ---------------------------------------------------------

    @staticmethod
    def _html(status: int, body: bytes) -> Response:
        return status, body, {"Content-Type": "text/html; charset=utf-8"}

    @staticmethod
    def _json(status: int, payload: Any) -> Response:
        body = json.dumps(payload, default=str).encode("utf-8")
        return status, body, {"Content-Type": "application/json; charset=utf-8"}

    @staticmethod
    def _redirect(location: str) -> Response:
        return 303, b"", {"Location": location}

    # --- GET handlers ------------------------------------------------------

    def get_root(self, params: dict[str, str], _form: dict[str, str], _raw: dict[str, list[str]]) -> Response:
        return self._html(200, handlers.render_rules_list(
            self.env, self.settings, self.store, message=params.get("msg", ""),
        ))

    def get_new(self, params: dict[str, str], _form: dict[str, str], _raw: dict[str, list[str]]) -> Response:
        return self._html(200, handlers.render_rule_form(
            self.env, self.settings, self.store, mode="new", params=params,
        ))

    def get_edit(self, params: dict[str, str], _form: dict[str, str], _raw: dict[str, list[str]]) -> Response:
        try:
            idx = int(params.get("idx", "-1"))
        except ValueError:
            idx = -1
        rules = self.store.load().get("overrides", [])
        if not (0 <= idx < len(rules)):
            return self._html(404, handlers.render_message(
                self.env, "Rule not found",
                "<p>That rule index doesn't exist. <a href='/'>Back to rules</a></p>",
            ))
        return self._html(200, handlers.render_rule_form(
            self.env, self.settings, self.store, mode="edit", index=idx, existing=rules[idx],
        ))

    def get_groups(self, params: dict[str, str], _form: dict[str, str], _raw: dict[str, list[str]]) -> Response:
        return self._html(200, handlers.render_groups_page(
            self.env, self.settings, self.store, message=params.get("msg", ""),
        ))

    def get_splits(self, params: dict[str, str], _form: dict[str, str], _raw: dict[str, list[str]]) -> Response:
        return self._html(200, handlers.render_splits_index(
            self.env, self.settings, self.store, message=params.get("msg", ""),
        ))

    def get_api_offset_tx_search(self, params: dict[str, str], _form: dict[str, str], _raw: dict[str, list[str]]) -> Response:
        try:
            limit = int(params.get("limit", "20"))
        except ValueError:
            limit = 20
        limit = max(1, min(50, limit))
        rows = queries.search_outgoings(self.settings.pg_dsn, params.get("q", ""), limit)
        return self._json(200, [
            {"id": r["id"], "date": str(r["date"]),
             "merchant": r["merchant"], "amount": r["amount"]}
            for r in rows
        ])

    def get_split(self, params: dict[str, str], _form: dict[str, str], _raw: dict[str, list[str]]) -> Response:
        tx_id = params.get("tx_id", "").strip()
        if not tx_id:
            return self._html(400, handlers.render_message(
                self.env, "Missing tx_id",
                "<p>Open a split via the <a href='/splits'>Splits index</a> "
                "or from a transaction's <em>Add rule</em> page.</p>",
            ))
        return self._html(200, handlers.render_split_form(
            self.env, self.settings, self.store, tx_id, message=params.get("msg", ""),
        ))

    # --- POST handlers -----------------------------------------------------

    def post_add(self, _params: dict[str, str], form: dict[str, str], _raw: dict[str, list[str]]) -> Response:
        rule = build_rule_from_form(form)
        if rule is None:
            return self._html(400, handlers.render_message(
                self.env, "Invalid rule",
                "<p>Need at least a matcher and one action (category or amortise). "
                "<a href='javascript:history.back()'>Go back</a></p>",
            ))
        self.store.append_rule(rule)
        self._spawn("retag")
        msg = "Rule saved — re-applying rules in the background"
        if form.get("auto_close") == "1":
            return self._html(200, handlers.render_done(self.env, msg))
        return self._redirect(f"/?msg={urllib.parse.quote(msg)}")

    def post_delete(self, _params: dict[str, str], form: dict[str, str], _raw: dict[str, list[str]]) -> Response:
        try:
            idx = int(form.get("idx", "-1"))
        except ValueError:
            idx = -1
        self.store.delete_rule(idx)
        self._spawn("retag")
        return self._redirect("/?msg=rule+deleted+%E2%80%94+re-applying")

    def post_update(self, _params: dict[str, str], form: dict[str, str], _raw: dict[str, list[str]]) -> Response:
        try:
            idx = int(form.get("idx", "-1"))
        except ValueError:
            idx = -1
        rule = build_rule_from_form(form)
        if rule is None or idx < 0:
            return self._html(400, handlers.render_message(
                self.env, "Invalid rule",
                "<p>Need a matcher and at least one action. "
                "<a href='javascript:history.back()'>Go back</a></p>",
            ))
        self.store.update_rule(idx, rule)
        self._spawn("retag")
        return self._redirect(f"/?msg=rule+%23{idx}+updated+%E2%80%94+re-applying")

    def post_apply(self, *_: Any) -> Response:
        self._spawn("retag")
        return self._redirect("/?msg=re-applying+rules+to+all+data")

    def post_poll(self, *_: Any) -> Response:
        self._spawn("poll")
        return self._redirect("/?msg=fetching+from+Monzo")

    def post_groups_save(self, _params: dict[str, str], form: dict[str, str], _raw: dict[str, list[str]]) -> Response:
        parsed = build_group_from_form(form)
        if parsed is None:
            return self._html(400, handlers.render_message(
                self.env, "Invalid group",
                "<p>An ID is required. <a href='/groups'>Go back</a></p>",
            ))
        gid, body = parsed
        self.store.save_group(gid, body)
        self._spawn("retag")
        return self._redirect(f"/groups?msg=group+%27{urllib.parse.quote(gid)}%27+saved")

    def post_groups_delete(self, _params: dict[str, str], form: dict[str, str], _raw: dict[str, list[str]]) -> Response:
        gid = form.get("id", "").strip()
        if gid:
            self.store.delete_group(gid)
            self._spawn("retag")
        return self._redirect("/groups?msg=group+deleted")

    def post_split_save(self, _params: dict[str, str], form: dict[str, str], raw_qs: dict[str, list[str]]) -> Response:
        tx_id = form.get("transaction_id", "").strip()
        try:
            parent_amount = float(form.get("parent_amount", "0") or 0)
        except ValueError:
            parent_amount = 0.0
        if not tx_id:
            return self._html(400, handlers.render_message(
                self.env, "Missing tx_id",
                "<p>The form didn't include a transaction id. <a href='/splits'>Back</a></p>",
            ))

        parts, err = parse_split_parts(raw_qs)
        if err:
            return self._html(400, handlers.render_message(
                self.env, "Invalid split",
                f"<p>{err}. <a href='javascript:history.back()'>Go back</a></p>",
            ))
        if not parts:
            return self._html(400, handlers.render_message(
                self.env, "Empty split",
                "<p>A split needs at least one part with a numeric amount. "
                "<a href='javascript:history.back()'>Go back</a></p>",
            ))

        parts_sum = round(sum(p["amount"] for p in parts), 2)
        if abs(parts_sum - round(parent_amount, 2)) > SPLIT_SUM_TOLERANCE:
            return self._html(400, handlers.render_message(
                self.env, "Sum mismatch",
                f"<p>Parts sum to £{parts_sum:.2f} but parent is £{parent_amount:.2f} "
                f"(Δ £{parts_sum - parent_amount:.2f}). They must match within 1p. "
                "<a href='javascript:history.back()'>Go back</a></p>",
            ))

        self.store.save_split(tx_id, parts)
        self._spawn("retag")
        return self._redirect(
            f"/split?tx_id={urllib.parse.quote(tx_id)}&msg=split+saved+%E2%80%94+re-applying"
        )

    def post_split_delete(self, _params: dict[str, str], form: dict[str, str], _raw: dict[str, list[str]]) -> Response:
        tx_id = form.get("transaction_id", "").strip()
        if tx_id:
            self.store.delete_split(tx_id)
            self._spawn("retag")
        return self._redirect("/splits?msg=split+deleted+%E2%80%94+re-applying")


Route = Callable[[dict[str, str], dict[str, str], dict[str, list[str]]], Response]


def _build_routes(editor: _Editor) -> tuple[dict[str, Route], dict[str, Route]]:
    gets: dict[str, Route] = {
        "/": editor.get_root,
        "/new": editor.get_new,
        "/edit": editor.get_edit,
        "/groups": editor.get_groups,
        "/splits": editor.get_splits,
        "/split": editor.get_split,
        "/api/offset_tx_search": editor.get_api_offset_tx_search,
    }
    posts: dict[str, Route] = {
        "/add": editor.post_add,
        "/delete": editor.post_delete,
        "/update": editor.post_update,
        "/apply": editor.post_apply,
        "/poll": editor.post_poll,
        "/groups/save": editor.post_groups_save,
        "/groups/delete": editor.post_groups_delete,
        "/split/save": editor.post_split_save,
        "/split/delete": editor.post_split_delete,
    }
    return gets, posts


def _build_handler(editor: _Editor) -> type[BaseHTTPRequestHandler]:
    gets, posts = _build_routes(editor)

    def _dispatch(handler: BaseHTTPRequestHandler, table: dict[str, Route]) -> None:
        url = urllib.parse.urlparse(handler.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        length = int(handler.headers.get("Content-Length", "0"))
        raw = handler.rfile.read(length).decode("utf-8") if length else ""
        raw_qs = urllib.parse.parse_qs(raw, keep_blank_values=True)
        form = {k: v[0] for k, v in raw_qs.items()}

        route = table.get(url.path)
        if route is None:
            status, body, headers = 404, b"Not found", {"Content-Type": "text/plain"}
        else:
            status, body, headers = route(params, form, raw_qs)

        handler.send_response(status)
        for k, v in headers.items():
            handler.send_header(k, v)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if body:
            handler.wfile.write(body)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            _dispatch(self, gets)

        def do_POST(self) -> None:
            _dispatch(self, posts)

        def log_message(self, fmt: str, *args: Any) -> None:
            log.info("%s - %s", self.address_string(), fmt % args)

    return Handler


def main() -> None:
    configure_logging()
    settings = EditorSettings.from_env()
    log.info("Rule editor on http://%s:%d", settings.bind, settings.port)
    log.info("Editing %s", settings.categories_file.resolve())
    editor = _Editor(settings)
    HTTPServer((settings.bind, settings.port), _build_handler(editor)).serve_forever()


if __name__ == "__main__":
    main()
