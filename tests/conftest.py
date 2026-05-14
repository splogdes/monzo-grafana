"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_categories(tmp_path: Path) -> Path:
    """A scratch ``categories.yaml`` path inside a per-test tmp directory."""
    return tmp_path / "categories.yaml"
