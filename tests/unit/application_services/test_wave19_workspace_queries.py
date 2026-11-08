from __future__ import annotations

from pathlib import Path

import pytest

import llm_agent.application.services.queries as queries
import llm_agent.application.services.query_find as query_find
from llm_agent.application.services.queries import (
    QUERY_FILE_NOT_UTF8,
    QUERY_FILE_TYPE_UNSUPPORTED,
    QUERY_FIND_PATTERN_EMPTY,
    QUERY_FIND_PATTERN_INVALID,
    QUERY_PATH_INVALID,
    QUERY_PATH_NOT_FOUND,
    QUERY_PERMISSION_DENIED,
    ReadOnlyWorkspaceQueryService,
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryStatus,
)
from llm_agent.workspace.context import WorkspaceContext


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


def _query(kind: WorkspaceQueryKind, arguments: dict[str, object]) -> WorkspaceQueryRequest:
    return WorkspaceQueryRequest(kind, arguments)


def test_all_five_queries_are_available_without_cli_objects(tmp_path: Path) -> None:
    (tmp_path / "README").write_text("needle\nsecond\n", encoding="utf-8")
    (tmp_path / "module.py").write_text("needle in python\n", encoding="utf-8")
    service = ReadOnlyWorkspaceQueryService(WorkspaceContext.create(tmp_path))
    cancellation = _NeverCancelled()

    listing = service.execute(_query(WorkspaceQueryKind.LIST_FILES, {}), cancellation)
    reading = service.execute(_query(WorkspaceQueryKind.READ, {"file_path": "README"}), cancellation)
    finding = service.execute(_query(WorkspaceQueryKind.FIND, {"pattern": "needle"}), cancellation)
    status = service.execute(_query(WorkspaceQueryKind.GIT_STATUS, {}), cancellation)
    diff = service.execute(_query(WorkspaceQueryKind.DIFF, {}), cancellation)

    assert listing.status is WorkspaceQueryStatus.SUCCEEDED
    assert reading.status is WorkspaceQueryStatus.SUCCEEDED
    assert finding.status is WorkspaceQueryStatus.SUCCEEDED
    assert status.kind is WorkspaceQueryKind.GIT_STATUS
    assert diff.kind is WorkspaceQueryKind.DIFF
    assert reading.data["path"] == "README"
    assert reading.data["content"].replace("\r\n", "\n") == "needle\nsecond\n"
    assert reading.data["truncated"] is False
    assert finding.data["matches"][0]["file"] in {"README", "module.py"}


def test_query_failures_have_stable_reason_codes(tmp_path: Path) -> None:
    (tmp_path / "binary.txt").write_bytes(b"\xff\xfe")
    (tmp_path / "image.bin").write_bytes(b"not text")
    service = ReadOnlyWorkspaceQueryService(tmp_path)
    cancellation = _NeverCancelled()

    outside = service.execute(_query(WorkspaceQueryKind.READ, {"file_path": "../escape.txt"}), cancellation)
    missing = service.execute(_query(WorkspaceQueryKind.READ, {"file_path": "missing.txt"}), cancellation)
    unsupported = service.execute(_query(WorkspaceQueryKind.READ, {"file_path": "image.bin"}), cancellation)
    non_utf8 = service.execute(_query(WorkspaceQueryKind.READ, {"file_path": "binary.txt"}), cancellation)
    empty = service.execute(_query(WorkspaceQueryKind.FIND, {"pattern": ""}), cancellation)
    invalid = service.execute(_query(WorkspaceQueryKind.FIND, {"pattern": "a.*"}), cancellation)

    assert outside.reason_code == QUERY_PATH_INVALID
    assert missing.reason_code == QUERY_PATH_NOT_FOUND
    assert unsupported.reason_code == QUERY_FILE_TYPE_UNSUPPORTED
    assert non_utf8.reason_code == QUERY_FILE_NOT_UTF8
    assert empty.reason_code == QUERY_FIND_PATTERN_EMPTY
    assert invalid.reason_code == QUERY_FIND_PATTERN_INVALID


@pytest.mark.parametrize(("content", "max_bytes"), [("xéneedle", 2), ("x😀needle", 3)])
def test_read_handles_utf8_sequence_split_by_byte_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str, max_bytes: int
) -> None:
    (tmp_path / "unicode.txt").write_text(content, encoding="utf-8")
    monkeypatch.setattr(queries, "MAX_READ_BYTES", max_bytes)
    result = ReadOnlyWorkspaceQueryService(tmp_path).read_file(
        _query(WorkspaceQueryKind.READ, {"file_path": "unicode.txt"}), _NeverCancelled()
    )
    assert result.status is WorkspaceQueryStatus.SUCCEEDED
    assert result.data == {"path": "unicode.txt", "content": "x", "truncated": True}


def test_read_reports_genuinely_invalid_utf8(tmp_path: Path) -> None:
    (tmp_path / "invalid.txt").write_bytes(b"needle\xff")
    result = ReadOnlyWorkspaceQueryService(tmp_path).read_file(
        _query(WorkspaceQueryKind.READ, {"file_path": "invalid.txt"}), _NeverCancelled()
    )
    assert result.status is WorkspaceQueryStatus.FAILED
    assert result.reason_code == QUERY_FILE_NOT_UTF8


def test_list_missing_and_wrong_type_are_distinguished(tmp_path: Path) -> None:
    service = ReadOnlyWorkspaceQueryService(tmp_path)
    cancellation = _NeverCancelled()
    missing = service.list_files(_query(WorkspaceQueryKind.LIST_FILES, {"path": "missing"}), cancellation)
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    wrong_type = service.list_files(_query(WorkspaceQueryKind.LIST_FILES, {"path": "file.txt"}), cancellation)
    assert missing.reason_code == QUERY_PATH_NOT_FOUND
    assert wrong_type.reason_code is not None


def test_find_reports_empty_success_when_no_file_matches(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("ordinary text", encoding="utf-8")
    result = ReadOnlyWorkspaceQueryService(tmp_path).find(
        _query(WorkspaceQueryKind.FIND, {"pattern": "absent"}), _NeverCancelled()
    )
    assert result.status is WorkspaceQueryStatus.SUCCEEDED
    assert result.data == {"matches": [], "truncated": False}


def test_find_fails_clearly_when_selected_root_cannot_be_scanned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_scandir = query_find.os.scandir

    def denied_root(path: object) -> object:
        if Path(path) == tmp_path:
            raise PermissionError("root denied")
        return original_scandir(path)

    monkeypatch.setattr(query_find.os, "scandir", denied_root)
    result = ReadOnlyWorkspaceQueryService(tmp_path).find(
        _query(WorkspaceQueryKind.FIND, {"pattern": "needle"}), _NeverCancelled()
    )
    assert result.status is WorkspaceQueryStatus.FAILED
    assert result.reason_code == QUERY_PERMISSION_DENIED


def test_find_preserves_matches_and_marks_unreadable_subdirectory_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "readable.txt").write_text("needle", encoding="utf-8")
    denied = tmp_path / "denied"
    denied.mkdir()
    original_scandir = query_find.os.scandir

    def denied_subdirectory(path: object) -> object:
        if Path(path) == denied:
            raise PermissionError("subdirectory denied")
        return original_scandir(path)

    monkeypatch.setattr(query_find.os, "scandir", denied_subdirectory)
    result = ReadOnlyWorkspaceQueryService(tmp_path).find(
        _query(WorkspaceQueryKind.FIND, {"pattern": "needle"}), _NeverCancelled()
    )
    assert result.status is WorkspaceQueryStatus.SUCCEEDED
    assert result.data["matches"][0]["file"] == "readable.txt"
    assert result.data["truncated"] is True


def test_find_preserves_matches_and_marks_unreadable_file_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readable = tmp_path / "readable.txt"
    unreadable = tmp_path / "unreadable.txt"
    readable.write_text("needle", encoding="utf-8")
    unreadable.write_text("needle", encoding="utf-8")
    original_open = Path.open

    def denied_file(path: Path, *args: object, **kwargs: object) -> object:
        if path == unreadable:
            raise PermissionError("file denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_file)
    result = ReadOnlyWorkspaceQueryService(tmp_path).find(
        _query(WorkspaceQueryKind.FIND, {"pattern": "needle"}), _NeverCancelled()
    )
    assert result.status is WorkspaceQueryStatus.SUCCEEDED
    assert result.data["matches"][0]["file"] == "readable.txt"
    assert result.data["truncated"] is True


@pytest.mark.parametrize(("content", "max_bytes"), [("éneedle", 1), ("😀needle", 2)])
def test_find_handles_utf8_sequence_split_by_byte_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str, max_bytes: int
) -> None:
    (tmp_path / "unicode.txt").write_text(content, encoding="utf-8")
    monkeypatch.setattr(query_find, "MAX_FIND_FILE_BYTES", max_bytes)
    result = ReadOnlyWorkspaceQueryService(tmp_path).find(
        _query(WorkspaceQueryKind.FIND, {"pattern": "needle"}), _NeverCancelled()
    )
    assert result.status is WorkspaceQueryStatus.SUCCEEDED
    assert result.data == {"matches": [], "truncated": True}


def test_find_keeps_valid_matches_when_another_file_has_invalid_utf8(tmp_path: Path) -> None:
    (tmp_path / "invalid.txt").write_bytes(b"needle\xff")
    (tmp_path / "valid.txt").write_text("needle", encoding="utf-8")
    result = ReadOnlyWorkspaceQueryService(tmp_path).find(
        _query(WorkspaceQueryKind.FIND, {"pattern": "needle"}), _NeverCancelled()
    )
    assert result.status is WorkspaceQueryStatus.SUCCEEDED
    assert [item["file"] for item in result.data["matches"]] == ["valid.txt"]
    assert result.data["truncated"] is True


def test_find_reports_invalid_utf8_when_selected_file_is_invalid(tmp_path: Path) -> None:
    (tmp_path / "invalid.txt").write_bytes(b"needle\xff")
    result = ReadOnlyWorkspaceQueryService(tmp_path).find(
        _query(WorkspaceQueryKind.FIND, {"pattern": "needle", "path": "invalid.txt"}),
        _NeverCancelled(),
    )
    assert result.status is WorkspaceQueryStatus.FAILED
    assert result.reason_code == QUERY_FILE_NOT_UTF8
