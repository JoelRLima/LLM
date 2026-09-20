from __future__ import annotations

import time
from pathlib import Path

from agent.application_services.queries import (
    ReadOnlyWorkspaceQueryService,
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryStatus,
)
from agent.interfaces.cli.query_executor import BoundedQueryExecutor, CliQueryCompletion, CliQuerySubmission
from agent.runtime.workspace_context import WorkspaceContext


def test_cli_executor_wraps_canonical_truth_and_keeps_live_marker_at_adapter_boundary(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")
    workspace = WorkspaceContext.create(tmp_path)
    executor = BoundedQueryExecutor(workspace_id=workspace.workspace_id)
    request = WorkspaceQueryRequest(WorkspaceQueryKind.READ, {"file_path": "readme.txt"})
    submission = executor.submit(request, task_active=True, execute=ReadOnlyWorkspaceQueryService(workspace).execute)
    assert isinstance(submission, CliQuerySubmission)
    completion = None
    deadline = time.monotonic() + 2
    while completion is None and time.monotonic() < deadline:
        completion = executor.poll()
        if completion is None:
            time.sleep(0.01)
    assert isinstance(completion, CliQueryCompletion)
    assert completion.result is not None
    assert completion.result.status is WorkspaceQueryStatus.SUCCEEDED
    assert completion.live_marker is not None and "LIVE SNAPSHOT" in completion.live_marker
    assert not hasattr(completion.result, "live_marker")
