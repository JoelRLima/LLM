from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from agent.application_services.queries import (
    ReadOnlyWorkspaceQueryService,
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryStatus,
)
from agent.interfaces.cli.action_parser import parse_action
from agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY
from agent.interfaces.cli.interactive_rendering import query_arguments
from agent.interfaces.cli.output_projection import (
    format_workspace_query_result,
    publish_workspace_query_result,
)
from agent.interfaces.cli.output_viewer import render_output_viewer
from agent.interfaces.cli.query_executor import BoundedQueryExecutor, CliQueryCompletion
from agent.outputs.models import (
    OutputContentPolicy,
    OutputDisposition,
    OutputKind,
    OutputPublishRequest,
    OutputSource,
)
from agent.outputs.service import OutputService
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext


class _Shell:
    def __init__(self) -> None:
        self.values: list[object] = []

    def print_background(self, value: object) -> None:
        self.values.append(value)


def _completion(executor: BoundedQueryExecutor) -> CliQueryCompletion:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        completion = executor.poll()
        if completion is not None:
            return completion
        time.sleep(0.01)
    raise AssertionError("bounded query did not settle")


def _query_request(text: str, action_id: str) -> WorkspaceQueryRequest:
    arguments = query_arguments(text, action_id)
    assert arguments is not None
    return WorkspaceQueryRequest(WorkspaceQueryKind.READ, arguments)


def _preserved_request(text: str, *, force_artifact: bool = False) -> OutputPublishRequest:
    return OutputPublishRequest(
        kind=OutputKind.FILE,
        source=OutputSource.WORKSPACE_QUERY,
        title="query result",
        text=text,
        content_policy=OutputContentPolicy.PRESERVE_USER_CONTENT,
        force_artifact=force_artifact,
        action_id="query.read",
        metadata={"result_status": "succeeded", "file_path": "large.txt"},
    )


def test_actions_queries_and_outputs_share_identity_and_strict_publication_boundaries(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "large.txt").write_text("x" * 8_001, encoding="utf-8")
    (workspace_root / "truncated.txt").write_text("y" * 30_000, encoding="utf-8")
    workspace = WorkspaceContext.create(workspace_root)
    query_service = ReadOnlyWorkspaceQueryService(workspace)
    executor = BoundedQueryExecutor(workspace_id=workspace.workspace_id)

    match = parse_action("/read large.txt")
    assert match is not None and match.action_id == "query.read"
    assert query_arguments("/read large.txt", match.action_id) == {"file_path": "large.txt"}
    assert parse_action("/git-status").action_id == "query.git_status"
    assert parse_action("/diff").action_id == "query.git_diff"
    assert parse_action("/inspect").action_id == "inspection.open"
    assert parse_action("/output") is None
    assert "/output" not in DEFAULT_CLI_ACTION_REGISTRY.preferred_commands()

    submission = executor.submit(
        _query_request("/read large.txt", match.action_id),
        task_active=False,
        execute=query_service.execute,
    )
    assert submission.__class__.__name__ == "CliQuerySubmission"
    completion = _completion(executor)
    assert completion.result is not None
    assert completion.result.status is WorkspaceQueryStatus.SUCCEEDED
    canonical = completion.result
    before = canonical
    output_service = OutputService(AppPaths.discover(tmp_path / "home", env={}).for_workspace(workspace.workspace_id))
    publication = publish_workspace_query_result(output_service, canonical, action_id=match.action_id)
    assert canonical == before
    assert publication is not None and publication.artifact is not None
    assert output_service.store.read(publication.artifact.output_id) == format_workspace_query_result(canonical)
    assert publication.artifact.source_truncated is False

    truncated_request = WorkspaceQueryRequest(WorkspaceQueryKind.READ, {"file_path": "truncated.txt"})
    truncated_submission = executor.submit(
        truncated_request,
        task_active=False,
        execute=query_service.execute,
    )
    assert truncated_submission.__class__.__name__ == "CliQuerySubmission"
    truncated_completion = _completion(executor)
    assert truncated_completion.result is not None
    assert truncated_completion.result.truncated is True
    truncated_publication = publish_workspace_query_result(
        output_service, truncated_completion.result, action_id="query.read"
    )
    assert truncated_publication is not None and truncated_publication.artifact is not None
    assert truncated_publication.artifact.source_truncated is True

    inline = output_service.publish(_preserved_request("x" * 8_000))
    assert inline.disposition is OutputDisposition.INLINE_ONLY
    line_inline = output_service.publish(_preserved_request("x\n" * 119 + "x"))
    assert line_inline.disposition is OutputDisposition.INLINE_ONLY
    strict_artifact = output_service.publish(_preserved_request("x" * 8_001))
    assert strict_artifact.disposition is OutputDisposition.ARTIFACT
    assert strict_artifact.artifact is not None

    shell = _Shell()
    context = SimpleNamespace(
        shell=shell,
        application=SimpleNamespace(output_service=lambda: output_service),
    )
    assert render_output_viewer(f"/inspect output {strict_artifact.artifact.output_id}", context)
    assert any(strict_artifact.artifact.output_id in str(value) for value in shell.values)
    assert render_output_viewer("/inspect", context) is False

    ordinary_service = OutputService(
        AppPaths.discover(tmp_path / "ordinary-home", env={}).for_workspace("ordinary")
    )
    assert ordinary_service.list() == ()
