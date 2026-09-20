from __future__ import annotations

from pathlib import Path

from agent.application_services.queries import (
    QUERY_FILE_NOT_UTF8,
    QUERY_FILE_TYPE_UNSUPPORTED,
    QUERY_FIND_PATTERN_EMPTY,
    QUERY_FIND_PATTERN_INVALID,
    QUERY_PATH_INVALID,
    QUERY_PATH_NOT_FOUND,
    ReadOnlyWorkspaceQueryService,
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryStatus,
)
from agent.runtime.workspace_context import WorkspaceContext


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


def test_list_missing_and_wrong_type_are_distinguished(tmp_path: Path) -> None:
    service = ReadOnlyWorkspaceQueryService(tmp_path)
    cancellation = _NeverCancelled()
    missing = service.list_files(_query(WorkspaceQueryKind.LIST_FILES, {"path": "missing"}), cancellation)
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    wrong_type = service.list_files(_query(WorkspaceQueryKind.LIST_FILES, {"path": "file.txt"}), cancellation)
    assert missing.reason_code == QUERY_PATH_NOT_FOUND
    assert wrong_type.reason_code is not None
