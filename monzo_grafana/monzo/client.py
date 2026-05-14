"""HTTP client for the Monzo transactions API."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import requests

log = logging.getLogger(__name__)

MONZO_API_BASE = "https://api.monzo.com"
HTTP_TIMEOUT_SECONDS = 20

# Monzo rejects single-request windows wider than ~90 days.
CHUNK_DAYS = 89
PAGE_SIZE = 100


class MonzoClient:
    def __init__(self, access_token: str) -> None:
        self._headers = {"Authorization": f"Bearer {access_token}"}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = requests.get(
            f"{MONZO_API_BASE}{path}",
            headers=self._headers,
            params=params or {},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def get_account_id(self) -> str:
        data = self._get("/accounts")
        for account in data.get("accounts", []):
            if account.get("type") == "uk_retail" and not account.get("closed"):
                return str(account["id"])
        raise RuntimeError("No active UK retail account found")

    def fetch_transactions(
        self,
        account_id: str,
        since: datetime,
        before: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch all transactions in ``[since, before)``, chunking the window."""
        out: list[dict[str, Any]] = []
        chunk_start = since
        while chunk_start < before:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), before)
            log.info("Fetching %s → %s", chunk_start.date(), chunk_end.date())
            try:
                out.extend(self._fetch_chunk(account_id, chunk_start, chunk_end))
            except requests.HTTPError as exc:
                log.warning(
                    "Chunk %s → %s failed: %s",
                    chunk_start.date(), chunk_end.date(), exc,
                )
            chunk_start = chunk_end
        return out

    def _fetch_chunk(
        self,
        account_id: str,
        since: datetime,
        before: datetime,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor = since
        while True:
            data = self._get(
                "/transactions",
                params={
                    "account_id": account_id,
                    "since": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "before": before.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "expand[]": "merchant",
                    "limit": PAGE_SIZE,
                },
            )
            batch = data.get("transactions", [])
            out.extend(batch)
            if len(batch) < PAGE_SIZE:
                return out
            last_ts = datetime.fromisoformat(batch[-1]["created"].replace("Z", "+00:00"))
            cursor = last_ts + timedelta(seconds=1)
