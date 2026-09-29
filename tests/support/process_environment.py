"""Environment helpers for subprocess-backed tests in the src-layout tree."""

from __future__ import annotations

import os
from pathlib import Path


def source_child_environment() -> dict[str, str]:
    """Make the current checkout importable by a fresh Python child."""

    repository_root = Path(__file__).resolve().parents[2]
    source_root = str(repository_root / "src")
    environment = os.environ.copy()
    inherited = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (source_root, inherited) if item
    )
    return environment
