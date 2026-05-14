"""Grafana annotation lookup for the legacy ``split:N`` amortisation hints."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import requests

from .config import Config

log = logging.getLogger(__name__)

_SPLIT_RE = re.compile(r"split\s*:\s*(\d+)", re.IGNORECASE)
ANNOTATION_LIMIT = 1000
HTTP_TIMEOUT_SECONDS = 10


def get_split_annotations(cfg: Config) -> list[dict[str, Any]]:
    if not cfg.grafana_token:
        return []
    try:
        resp = requests.get(
            f"{cfg.grafana_url}/api/annotations",
            headers={"Authorization": f"Bearer {cfg.grafana_token}"},
            params={"limit": str(ANNOTATION_LIMIT), "type": "annotation"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Could not fetch Grafana annotations: %s", exc)
        return []

    result: list[dict[str, Any]] = []
    for ann in resp.json():
        m = _SPLIT_RE.search(ann.get("text", ""))
        if m:
            ts = datetime.fromtimestamp(ann["time"] / 1000, tz=UTC)
            result.append({"time": ts, "split": int(m.group(1))})
    log.info("Found %d amortisation annotation(s)", len(result))
    return result
