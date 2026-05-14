"""Tell the poller to run a poll/retag after a YAML change.

Prefers HTTP (works across docker services); falls back to a subprocess
spawn for local development.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import urllib.request

log = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 3


def trigger(action: str, base_url: str | None) -> None:
    """Trigger ``action`` (``"poll"`` or ``"retag"``) on the poller."""
    if base_url:
        # POLLER_TRIGGER_URL historically included the action suffix; normalise.
        base = (
            base_url.rsplit("/", 1)[0]
            if base_url.endswith(("/poll", "/retag"))
            else base_url.rstrip("/")
        )
        url = f"{base}/{action}"
        try:
            req = urllib.request.Request(url, method="POST", data=b"")
            urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS).read()
            log.info("Triggered %s via %s", action, url)
            return
        except OSError as exc:
            log.warning("Trigger %s failed (%s); falling back to subprocess", url, exc)

    _spawn_subprocess(action)


def _spawn_subprocess(action: str) -> None:
    try:
        subprocess.Popen(
            [sys.executable, "-m", "monzo_grafana.cli", action],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.warning("Failed to spawn poller %s: %s", action, exc)
