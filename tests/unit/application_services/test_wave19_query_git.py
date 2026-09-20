from __future__ import annotations

from pathlib import Path

from agent.application_services import query_git
from agent.application_services.queries import (
    QUERY_CANCELLED,
    QUERY_GIT_FAILED,
    QUERY_GIT_UNAVAILABLE,
    QUERY_TIMEOUT,
    ReadOnlyWorkspaceQueryService,
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryStatus,
)
from agent.application_services.query_git import GitObservation


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


def test_git_status_and_diff_use_bounded_read_only_adapter(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run_git(_workspace: Path, arguments: list[str], _cancel: object, **_kwargs: object) -> GitObservation:
        calls.append(arguments)
        if arguments[0] == "status":
            return GitObservation(True, 0, "## main\n", "")
        return GitObservation(True, 0, "1\t2\ta.py\n-\t-\tb.bin\n", "")

    monkeypatch.setattr("agent.application_services.queries.run_git", fake_run_git)
    service = ReadOnlyWorkspaceQueryService(tmp_path)
    cancel = _NeverCancelled()
    status = service.git_status(WorkspaceQueryRequest(WorkspaceQueryKind.GIT_STATUS, {}), cancel)
    diff = service.diff(WorkspaceQueryRequest(WorkspaceQueryKind.DIFF, {"paths": ()}), cancel)

    assert status.status is WorkspaceQueryStatus.SUCCEEDED and status.data == "## main\n"
    assert diff.data == {
        "file_count": 2,
        "files": [
            {"file": "a.py", "added": 1, "deleted": 2},
            {"file": "b.bin", "added": None, "deleted": None},
        ],
        "truncated": False,
    }
    assert calls[0][:2] == ["status", "--short"]
    assert "--numstat" in calls[1]


def test_git_observation_maps_unavailable_timeout_and_nonzero_exit(tmp_path: Path, monkeypatch) -> None:
    service = ReadOnlyWorkspaceQueryService(tmp_path)
    request = WorkspaceQueryRequest(WorkspaceQueryKind.GIT_STATUS, {})
    cancel = _NeverCancelled()
    monkeypatch.setattr(query_git.shutil, "which", lambda _name: None)
    unavailable = service.git_status(request, cancel)
    assert unavailable.reason_code == QUERY_GIT_UNAVAILABLE

    monkeypatch.setattr(query_git.shutil, "which", lambda _name: "git")
    monkeypatch.setattr(
        "agent.application_services.queries.run_git",
        lambda *_args, **_kwargs: GitObservation(True, 1, "", "not a repository"),
    )
    failed = service.git_status(request, cancel)
    assert failed.reason_code == QUERY_GIT_FAILED

    monkeypatch.setattr(
        "agent.application_services.queries.run_git",
        lambda *_args, **_kwargs: GitObservation(True, None, "", "timeout", timed_out=True),
    )
    timed_out = service.git_status(request, cancel)
    assert timed_out.reason_code == QUERY_TIMEOUT

    monkeypatch.setattr(
        "agent.application_services.queries.run_git",
        lambda *_args, **_kwargs: GitObservation(True, None, "", "", cancelled=True),
    )
    cancelled = service.git_status(request, cancel)
    assert cancelled.reason_code == QUERY_CANCELLED
