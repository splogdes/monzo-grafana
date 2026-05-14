"""YAML CRUD for ``categories.yaml``: overrides, groups, splits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

FILE_HEADER = """# Managed by the rule editor (rule_editor.py). Manual edits are kept
# but inline comments are not preserved across saves.
#
# Three things you can do here (see categories.yaml.example for full schema):
#   1. Re-categorise (fix Monzo's "general" assignments)
#   2. Mark internal transfers / reimbursements with category: internal
#      (excluded from all dashboard totals)
#   3. Amortise large purchases with amortise_months / _weeks / _days
"""


class RuleStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    # ---- file I/O ----

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"overrides": []}
        with self.path.open() as f:
            doc: dict[str, Any] = yaml.safe_load(f) or {}
        if "overrides" not in doc or doc["overrides"] is None:
            doc["overrides"] = []
        return doc

    def save(self, doc: dict[str, Any]) -> None:
        # Single-file Docker bind mounts don't support atomic rename — the
        # rename creates a new file inside the container and the host's
        # bind-mounted path keeps pointing at the old inode. Direct write is
        # fine: the file is small, writes are rare, and a partial write just
        # produces invalid YAML which the next save will overwrite.
        with self.path.open("w") as f:
            f.write(FILE_HEADER)
            yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True)

    # ---- overrides ----

    def append_rule(self, rule: dict[str, Any]) -> None:
        doc = self.load()
        doc["overrides"].append(rule)
        self.save(doc)

    def update_rule(self, index: int, rule: dict[str, Any]) -> None:
        doc = self.load()
        if 0 <= index < len(doc["overrides"]):
            doc["overrides"][index] = rule
            self.save(doc)

    def delete_rule(self, index: int) -> None:
        doc = self.load()
        if 0 <= index < len(doc["overrides"]):
            doc["overrides"].pop(index)
            self.save(doc)

    # ---- groups ----

    def yaml_groups(self) -> dict[str, dict[str, Any]]:
        return self.load().get("groups") or {}

    def save_group(self, group_id: str, body: dict[str, Any]) -> None:
        doc = self.load()
        groups = doc.get("groups") or {}
        groups[group_id] = body
        doc["groups"] = groups
        self.save(doc)

    def delete_group(self, group_id: str) -> None:
        doc = self.load()
        groups = doc.get("groups") or {}
        if group_id in groups:
            groups.pop(group_id)
            doc["groups"] = groups
            self.save(doc)

    # ---- splits ----

    def fetch_split(self, tx_id: str) -> dict[str, Any] | None:
        for entry in self.load().get("splits") or []:
            if entry.get("transaction_id") == tx_id:
                return entry  # type: ignore[no-any-return]
        return None

    def save_split(self, tx_id: str, parts: list[dict[str, Any]]) -> None:
        doc = self.load()
        splits = doc.get("splits") or []
        for i, entry in enumerate(splits):
            if entry.get("transaction_id") == tx_id:
                splits[i] = {"transaction_id": tx_id, "parts": parts}
                break
        else:
            splits.append({"transaction_id": tx_id, "parts": parts})
        doc["splits"] = splits
        self.save(doc)

    def delete_split(self, tx_id: str) -> None:
        doc = self.load()
        splits = doc.get("splits") or []
        new_splits = [e for e in splits if e.get("transaction_id") != tx_id]
        if len(new_splits) != len(splits):
            doc["splits"] = new_splits
            self.save(doc)

    def all_splits(self) -> list[dict[str, Any]]:
        return list(self.load().get("splits") or [])
