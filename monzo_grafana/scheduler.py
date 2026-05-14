"""Daemon mode: HTTP trigger server plus the periodic poll loop."""

from __future__ import annotations

import logging
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import schedule

from .config import Config
from .db.transactions import poll, retag

log = logging.getLogger(__name__)

SCHEDULE_TICK_SECONDS = 30


def _build_trigger_handler(cfg: Config) -> type[BaseHTTPRequestHandler]:
    class _TriggerHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path == "/poll":
                threading.Thread(target=poll, args=(cfg,), daemon=True).start()
                self.send_response(202)
                self.end_headers()
                self.wfile.write(b"poll started\n")
            elif path == "/retag":
                threading.Thread(target=retag, args=(cfg,), daemon=True).start()
                self.send_response(202)
                self.end_headers()
                self.wfile.write(b"retag started\n")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt: str, *args: Any) -> None:
            log.info("trigger: %s", fmt % args)

    return _TriggerHandler


def start_trigger_server(cfg: Config) -> None:
    """Background HTTP server accepting POST ``/poll`` and POST ``/retag``.

    Used by the rule editor (especially when it runs in a separate container)
    to request immediate operations without forking subprocesses.
    """
    handler = _build_trigger_handler(cfg)
    server = HTTPServer((cfg.trigger_bind, cfg.trigger_port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info(
        "Poll trigger listening on http://%s:%d/poll and /retag",
        cfg.trigger_bind, cfg.trigger_port,
    )


def run_schedule(cfg: Config) -> None:
    start_trigger_server(cfg)
    poll(cfg)
    schedule.every(cfg.poll_interval_minutes).minutes.do(poll, cfg)
    log.info("Scheduler started — polling every %d minutes", cfg.poll_interval_minutes)
    while True:
        schedule.run_pending()
        time.sleep(SCHEDULE_TICK_SECONDS)
