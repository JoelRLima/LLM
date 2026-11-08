"""Real filesystem bounds for the discovery frecency document."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_agent.discovery.store import (
    MAX_DISCOVERY_FRECENCY_BYTES,
    FrecencyStore,
)
from llm_agent.filesystem.path_safety import WorkspacePathError


def test_frecency_load_rejects_oversized_document_without_parsing_it(tmp_path: Path) -> None:
    path = tmp_path / "frecency.json"
    path.write_bytes(b" " * (MAX_DISCOVERY_FRECENCY_BYTES + 1))

    store = FrecencyStore(path)

    assert store.snapshot() == ()
    assert store.diagnostics == ("DISCOVERY_FRECENCY_CORRUPT",)


def test_frecency_load_rejects_oversized_entry_id(tmp_path: Path) -> None:
    path = tmp_path / "frecency.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "entry_id": "x" * 193,
                        "use_count": 1,
                        "last_used_utc": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    store = FrecencyStore(path, clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert store.snapshot() == ()
    assert store.diagnostics == ("DISCOVERY_FRECENCY_CORRUPT",)


def test_frecency_record_rejects_unpersistable_entry_id(tmp_path: Path) -> None:
    store = FrecencyStore(tmp_path / "frecency.json")

    with pytest.raises(ValueError, match="entry_id is required"):
        store.record("x" * 193)

    assert not (tmp_path / "frecency.json").exists()


def test_frecency_symlink_target_is_never_resolved_or_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "outside.json"
    original = b'{"keep": "outside"}\n'
    target.write_bytes(original)
    link = tmp_path / "frecency.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    resolved_link = False
    original_resolve = Path.resolve

    def track_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        nonlocal resolved_link
        if path == link:
            resolved_link = True
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", track_resolve)
    store = FrecencyStore(link)
    assert store.diagnostics == ("DISCOVERY_FRECENCY_CORRUPT",)
    with pytest.raises(WorkspacePathError):
        store.record("cli:must-not-write")

    assert not resolved_link
    assert target.read_bytes() == original


def test_frecency_link_check_precedes_any_path_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "linked-frecency.json"
    checked: list[Path] = []
    resolved = False

    def reject_simulated_link(candidate: str | Path) -> None:
        checked.append(Path(candidate))
        raise WorkspacePathError("simulated link-like path")

    def fail_if_resolved(candidate: Path, *args: object, **kwargs: object) -> Path:
        nonlocal resolved
        resolved = True
        raise AssertionError(f"unsafe path was resolved: {candidate}")

    monkeypatch.setattr("llm_agent.discovery.store.assert_no_link_ancestors", reject_simulated_link)
    monkeypatch.setattr(Path, "resolve", fail_if_resolved)

    store = FrecencyStore(path)

    assert store.diagnostics == ("DISCOVERY_FRECENCY_CORRUPT",)
    assert checked == [path.absolute()]
    assert not resolved


def test_frecency_store_persists_and_reloads_normally(tmp_path: Path) -> None:
    path = tmp_path / "frecency.json"
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    FrecencyStore(path, clock=lambda: now).record("cli:normal", now=now)
    restored = FrecencyStore(path, clock=lambda: now)

    assert restored.snapshot() == (
        {"entry_id": "cli:normal", "use_count": 1, "last_used_utc": "2026-01-01T00:00:00Z"},
    )
