from __future__ import annotations

from pathlib import Path

import pytest

from agent.runtime import filesystem_primitives


def test_write_bytes_atomic_publishes_exact_bytes_and_syncs_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "payload.bin"
    payload = b"\x00exact\xff\nbytes"
    calls: list[object] = []

    real_fsync = filesystem_primitives.os.fsync

    def observe_fsync(descriptor: int) -> None:
        calls.append("content-fsync")
        real_fsync(descriptor)

    def observe_parent_sync(path: str | Path) -> None:
        calls.append(("parent-sync", Path(path)))

    monkeypatch.setattr(filesystem_primitives.os, "fsync", observe_fsync)
    monkeypatch.setattr(filesystem_primitives, "sync_parent_directory", observe_parent_sync)

    filesystem_primitives.write_bytes_atomic(destination, payload)

    assert destination.read_bytes() == payload
    assert calls == ["content-fsync", ("parent-sync", destination)]
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []


def test_write_bytes_atomic_replaces_only_after_complete_same_directory_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "payload.bin"
    destination.write_bytes(b"previous")
    payload = b"replacement"
    observed: dict[str, object] = {}
    real_replace = filesystem_primitives.os.replace

    def observe_replace(source: str | Path, target: str | Path) -> None:
        temporary = Path(source)
        selected = Path(target)
        observed["directory"] = temporary.parent
        observed["temporary_bytes"] = temporary.read_bytes()
        observed["destination_before"] = selected.read_bytes()
        real_replace(source, target)

    monkeypatch.setattr(filesystem_primitives.os, "replace", observe_replace)

    filesystem_primitives.write_bytes_atomic(destination, payload)

    assert observed == {
        "directory": tmp_path,
        "temporary_bytes": payload,
        "destination_before": b"previous",
    }
    assert destination.read_bytes() == payload


def test_write_bytes_atomic_cleans_temp_and_does_not_publish_on_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "payload.bin"
    destination.write_bytes(b"previous")

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(filesystem_primitives.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="fsync failed"):
        filesystem_primitives.write_bytes_atomic(destination, b"never-published")

    assert destination.read_bytes() == b"previous"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []


def test_write_bytes_atomic_cleans_temp_and_leaves_absent_destination_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "payload.bin"

    def fail_replace(_source: str | Path, _target: str | Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(filesystem_primitives.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        filesystem_primitives.write_bytes_atomic(destination, b"never-published")

    assert not destination.exists()
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []
