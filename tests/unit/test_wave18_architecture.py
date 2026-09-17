"""Cross-platform W18 architecture boundary gate."""

from __future__ import annotations

from scripts.check_wave18_architecture import check_architecture


def test_wave18_architecture_is_clean() -> None:
    assert check_architecture() == []
