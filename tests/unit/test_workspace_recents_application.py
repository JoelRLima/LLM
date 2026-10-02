"""Application contract tests for the recent-workspace document."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import llm_agent.application.workspace_recents as recents
from llm_agent.agent.runtime.instance_lock import InstanceLockError
from llm_agent.application.context import AppPaths
from llm_agent.storage.json_persistence import AtomicWriteError, JsonObjectReadError


def _app_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.discover(app_home=tmp_path / "app-home")


def _record(path: Path, timestamp: str = "2026-01-01T00:00:00Z") -> dict[str, str]:
    return {"path": str(path), "last_opened_utc": timestamp}


def _write_document(app_paths: AppPaths, payload: object) -> None:
    app_paths.global_dir.mkdir(parents=True, exist_ok=True)
    app_paths.recent_workspaces_file.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_document_returns_empty_tuple(tmp_path: Path) -> None:
    assert recents.list_recent_workspaces(_app_paths(tmp_path)) == ()


@pytest.mark.parametrize("contents", ["{", "[]", "null", "\"text\""])
def test_corrupt_or_non_object_document_returns_empty_tuple(
    tmp_path: Path, contents: str
) -> None:
    app_paths = _app_paths(tmp_path)
    app_paths.global_dir.mkdir(parents=True, exist_ok=True)
    app_paths.recent_workspaces_file.write_text(contents, encoding="utf-8")

    assert recents.list_recent_workspaces(app_paths) == ()


@pytest.mark.parametrize("version", [True, 1.0, "1", None, 2])
def test_invalid_schema_version_returns_empty_tuple(
    tmp_path: Path, version: object
) -> None:
    app_paths = _app_paths(tmp_path)
    payload = {"schema_version": version, "items": []}
    _write_document(app_paths, payload)

    assert recents.list_recent_workspaces(app_paths) == ()


def test_missing_schema_version_returns_empty_tuple(tmp_path: Path) -> None:
    app_paths = _app_paths(tmp_path)
    _write_document(app_paths, {"items": []})

    assert recents.list_recent_workspaces(app_paths) == ()


def test_invalid_records_and_timestamps_are_ignored(tmp_path: Path) -> None:
    app_paths = _app_paths(tmp_path)
    good = tmp_path / "good"
    good.mkdir()
    oversized = "x" * (recents._MAX_WORKSPACE_PATH_BYTES + 1)
    payload = {
        "schema_version": 1,
        "items": [
            None,
            {"path": str(good), "last_opened_utc": "not-a-time"},
            {"path": str(good), "last_opened_utc": "2026-01-01T01:00:00+01:00"},
            {"path": oversized, "last_opened_utc": "2026-01-01T00:00:00Z"},
            {"path": str(tmp_path / "missing"), "last_opened_utc": "2026-01-01T00:00:00Z"},
            _record(good),
        ],
    }
    _write_document(app_paths, payload)

    assert recents.list_recent_workspaces(app_paths) == (good.resolve(),)


def test_symlink_workspace_is_ignored(tmp_path: Path) -> None:
    app_paths = _app_paths(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "workspace-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    _write_document(app_paths, {"schema_version": 1, "items": [_record(link)]})

    assert recents.list_recent_workspaces(app_paths) == ()


def test_duplicate_canonical_path_uses_newest_and_orders_deterministically(
    tmp_path: Path,
) -> None:
    app_paths = _app_paths(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    payload = {
        "schema_version": 1,
        "items": [
            _record(first, "2026-01-01T00:00:02Z"),
            _record(second, "2026-01-01T00:00:02Z"),
            _record(first / "." , "2026-01-01T00:00:01Z"),
            _record(first, "2026-01-01T00:00:03Z"),
        ],
    }
    _write_document(app_paths, payload)

    assert recents.list_recent_workspaces(app_paths) == (first.resolve(), second.resolve())


def test_limit_is_respected_and_capped_at_eight(tmp_path: Path) -> None:
    app_paths = _app_paths(tmp_path)
    workspaces = [tmp_path / f"workspace-{index}" for index in range(10)]
    for path in workspaces:
        path.mkdir()
    items = [
        _record(path, f"2026-01-01T00:00:{20 - index:02d}Z")
        for index, path in enumerate(workspaces)
    ]
    _write_document(app_paths, {"schema_version": 1, "items": items})

    assert len(recents.list_recent_workspaces(app_paths, limit=20)) == 8
    assert len(recents.list_recent_workspaces(app_paths, limit=3)) == 3
    assert recents.list_recent_workspaces(app_paths, limit=0) == ()


@pytest.mark.parametrize("limit", [-1, True, 1.5, "2", None])
def test_invalid_limit_raises_value_error(tmp_path: Path, limit: object) -> None:
    with pytest.raises(ValueError, match="limit"):
        recents.list_recent_workspaces(_app_paths(tmp_path), limit=limit)  # type: ignore[arg-type]


def test_list_treats_typed_read_failure_as_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_paths = _app_paths(tmp_path)

    def fail_read(*_args: object, **_kwargs: object) -> None:
        raise JsonObjectReadError(app_paths.recent_workspaces_file, "corrupt")

    monkeypatch.setattr(recents, "read_json_object", fail_read)

    assert recents.list_recent_workspaces(app_paths) == ()


def test_remember_persists_canonical_record_with_exact_lock_and_atomic_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_paths = _app_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    observed: dict[str, object] = {}
    original_writer = recents.write_text_atomic

    class RecordingLock:
        def __init__(self, path: Path, *, create_parent: bool) -> None:
            observed["lock_path"] = path
            observed["lock_create_parent"] = create_parent

        def __enter__(self) -> RecordingLock:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def recording_writer(
        path: str | Path,
        content: str,
        *,
        create_parent: bool = True,
    ) -> bool:
        observed["write_path"] = Path(path)
        observed["write_create_parent"] = create_parent
        observed["content"] = content
        return original_writer(path, content, create_parent=create_parent)

    monkeypatch.setattr(recents, "InstanceLock", type("LockFactory", (), {
        "create": staticmethod(lambda path, *, create_parent: RecordingLock(path, create_parent=create_parent))
    }))
    monkeypatch.setattr(recents, "write_text_atomic", recording_writer)

    recents.remember_recent_workspace(app_paths, workspace)

    assert observed["lock_path"] == app_paths.recent_workspaces_file.with_name(
        f"{app_paths.recent_workspaces_file.name}.lock"
    )
    assert observed["lock_create_parent"] is False
    assert observed["write_path"] == app_paths.recent_workspaces_file
    assert observed["write_create_parent"] is False
    document = json.loads(str(observed["content"]))
    assert document["schema_version"] == 1
    assert document["items"][0]["path"] == str(workspace.resolve())
    stamp = datetime.fromisoformat(document["items"][0]["last_opened_utc"].replace("Z", "+00:00"))
    assert stamp.utcoffset() == timedelta(0)
    assert app_paths.recent_workspaces_file.is_file()


@pytest.mark.parametrize(
    "failure",
    ["read", "write", "lock", "read_oserror", "write_oserror", "lock_oserror"],
)
def test_remember_known_storage_and_lock_failures_are_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    app_paths = _app_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    if failure in {"read", "read_oserror"}:
        def fail_read(*_args: object, **_kwargs: object) -> None:
            if failure == "read_oserror":
                raise OSError("read failed")
            raise JsonObjectReadError(app_paths.recent_workspaces_file, "corrupt")

        monkeypatch.setattr(recents, "read_json_object", fail_read)
    elif failure in {"write", "write_oserror"}:
        def fail_write(*_args: object, **_kwargs: object) -> None:
            if failure == "write_oserror":
                raise OSError("write failed")
            raise AtomicWriteError(app_paths.recent_workspaces_file, OSError("read-only"))

        monkeypatch.setattr(recents, "write_text_atomic", fail_write)
    else:
        class FailingLock:
            @staticmethod
            def create(*_args: object, **_kwargs: object) -> None:
                if failure == "lock_oserror":
                    raise OSError("lock failed")
                raise InstanceLockError("busy")

        monkeypatch.setattr(recents, "InstanceLock", FailingLock)

    assert recents.remember_recent_workspace(app_paths, workspace) is None


def test_remember_canonicalizes_and_retains_only_eight(tmp_path: Path) -> None:
    app_paths = _app_paths(tmp_path)
    app_paths.global_dir.mkdir(parents=True, exist_ok=True)
    workspaces = [tmp_path / f"workspace-{index}" for index in range(10)]
    for path in workspaces:
        path.mkdir()
    _write_document(
        app_paths,
        {"schema_version": 1, "items": [_record(path) for path in workspaces]},
    )

    recents.remember_recent_workspace(app_paths, workspaces[-1] / ".")

    payload = json.loads(app_paths.recent_workspaces_file.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert len(payload["items"]) == 8
    assert payload["items"][0]["path"] == str(workspaces[-1].resolve())
    assert sum(item["path"] == str(workspaces[-1].resolve()) for item in payload["items"]) == 1
