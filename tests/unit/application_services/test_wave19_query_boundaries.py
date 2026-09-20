from __future__ import annotations

from pathlib import Path

from agent.application_services.queries import (
    MAX_LIST_ITEMS,
    MAX_QUERY_OUTPUT_CHARS,
    QUERY_INVALID_REQUEST,
    QUERY_PATH_INVALID,
    ReadOnlyWorkspaceQueryService,
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryStatus,
)


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


def test_unknown_keys_and_scalar_ranges_fail_as_invalid_request(tmp_path: Path) -> None:
    service = ReadOnlyWorkspaceQueryService(tmp_path)
    cancel = _NeverCancelled()
    unknown = service.execute(
        WorkspaceQueryRequest(WorkspaceQueryKind.LIST_FILES, {"path": ".", "extra": True}),
        cancel,
    )
    bad_bool = service.execute(
        WorkspaceQueryRequest(WorkspaceQueryKind.READ, {"file_path": "x.txt", "start_line": True}),
        cancel,
    )
    bad_range = service.execute(
        WorkspaceQueryRequest(WorkspaceQueryKind.READ, {"file_path": "x.txt", "start_line": 3, "end_line": 2}),
        cancel,
    )
    assert unknown.reason_code == QUERY_INVALID_REQUEST
    assert bad_bool.reason_code == QUERY_INVALID_REQUEST
    assert bad_range.reason_code == QUERY_INVALID_REQUEST


def test_successful_output_is_explicitly_bounded(tmp_path: Path) -> None:
    for index in range(MAX_LIST_ITEMS + 1):
        (tmp_path / f"{index:04d}.txt").write_text("x", encoding="utf-8")
    (tmp_path / "large.txt").write_text("x" * (MAX_QUERY_OUTPUT_CHARS + 10), encoding="utf-8")
    service = ReadOnlyWorkspaceQueryService(tmp_path)
    cancel = _NeverCancelled()
    listing = service.list_files(WorkspaceQueryRequest(WorkspaceQueryKind.LIST_FILES, {}), cancel)
    reading = service.read_file(WorkspaceQueryRequest(WorkspaceQueryKind.READ, {"file_path": "large.txt"}), cancel)
    assert listing.status is WorkspaceQueryStatus.SUCCEEDED
    assert listing.truncated is True and len(listing.data["items"]) == MAX_LIST_ITEMS
    assert reading.status is WorkspaceQueryStatus.SUCCEEDED
    assert reading.truncated is True and len(reading.data["content"]) == MAX_QUERY_OUTPUT_CHARS


def test_cancellation_returns_no_partial_success_payload(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")
    service = ReadOnlyWorkspaceQueryService(tmp_path)

    class _Cancelled:
        def is_cancelled(self) -> bool:
            return True

    result = service.list_files(WorkspaceQueryRequest(WorkspaceQueryKind.LIST_FILES, {}), _Cancelled())
    assert result.status is WorkspaceQueryStatus.CANCELLED
    assert result.data is None


def test_diff_rejects_option_like_pathspec_before_git(tmp_path: Path, monkeypatch) -> None:
    called = False

    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("git must not run for invalid pathspec")

    monkeypatch.setattr("agent.application_services.queries.run_git", fail_if_called)
    service = ReadOnlyWorkspaceQueryService(tmp_path)
    result = service.diff(WorkspaceQueryRequest(WorkspaceQueryKind.DIFF, {"paths": ("--output=x",)}), _NeverCancelled())
    assert result.status is WorkspaceQueryStatus.FAILED
    assert result.reason_code == QUERY_PATH_INVALID
    assert called is False
