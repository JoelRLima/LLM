"""Single CLI projection boundary for successful workspace query output."""

from __future__ import annotations

from typing import Any

from agent.application_services.queries import (
    WorkspaceQueryKind,
    WorkspaceQueryResult,
    WorkspaceQueryStatus,
)
from agent.outputs.models import (
    OutputContentPolicy,
    OutputKind,
    OutputPublication,
    OutputPublishRequest,
    OutputSource,
)
from agent.outputs.service import OutputService

_KIND_BY_QUERY = {
    WorkspaceQueryKind.LIST_FILES: OutputKind.TABLE,
    WorkspaceQueryKind.READ: OutputKind.FILE,
    WorkspaceQueryKind.FIND: OutputKind.SEARCH,
    WorkspaceQueryKind.GIT_STATUS: OutputKind.TABLE,
    WorkspaceQueryKind.DIFF: OutputKind.DIFF,
}


def format_workspace_query_result(result: WorkspaceQueryResult) -> str:
    """Return exactly the text previously emitted by the CLI query renderer."""

    if result.status is not WorkspaceQueryStatus.SUCCEEDED:
        raise ValueError("only successful query results have a display projection")
    data = result.data
    if isinstance(data, dict) and "items" in data:
        rows = data.get("items", [])
        text = "\n".join(
            f"{row.get('type', '?')} {row.get('relative', row.get('name', ''))}"
            for row in rows
        )
        return text + ("\n[output truncated]" if data.get("truncated") else "") or "(empty workspace)"
    if isinstance(data, dict) and "content" in data:
        text = str(data.get("content", ""))
        return text + ("\n[output truncated]" if data.get("truncated") else "")
    if isinstance(data, dict) and "matches" in data:
        text = "\n".join(
            f"{row.get('file')}:{row.get('line')}: {row.get('content')}"
            for row in data.get("matches", [])
        )
        return text + ("\n[output truncated]" if data.get("truncated") else "") or "(no matches)"
    if isinstance(data, dict) and "files" in data:
        rows = data.get("files", [])
        text = "\n".join(
            f"{row.get('file')}: +{row.get('added', '?')} -{row.get('deleted', '?')}"
            for row in rows
        )
        return f"files={data.get('file_count', len(rows))}" + ("\n" + text if text else "")
    return str(data or "(no output)") + ("\n[output truncated]" if result.truncated else "")


def publish_workspace_query_result(
    service: OutputService,
    result: WorkspaceQueryResult,
    *,
    action_id: str,
) -> OutputPublication | None:
    """Publish only successful canonical query results, without changing them."""

    if result.status is not WorkspaceQueryStatus.SUCCEEDED:
        return None
    text = format_workspace_query_result(result)
    kind = _KIND_BY_QUERY[result.kind]
    metadata: dict[str, object] = {
        "query_kind": result.kind.value,
        "result_status": result.status.value,
    }
    if result.kind is WorkspaceQueryKind.READ and isinstance(result.data, dict):
        metadata["file_path"] = result.data.get("path", "")
    return service.publish(
        OutputPublishRequest(
            kind=kind,
            source=OutputSource.WORKSPACE_QUERY,
            title=f"Workspace query: {result.kind.value}",
            text=text,
            content_policy=OutputContentPolicy.PRESERVE_USER_CONTENT,
            source_truncated=result.truncated,
            action_id=action_id,
            metadata=metadata,
        )
    )


def render_publication(publication: OutputPublication, emit: Any) -> None:
    """Render a publication through an injected literal emitter."""

    if publication.artifact is None or publication.reference is None:
        emit(publication.inline_text)
        return
    emit(publication.inline_text)
    reference = publication.reference
    suffix = " (source truncated)" if reference.source_truncated else ""
    emit(
        f"[output artifact] {reference.output_id} "
        f"({reference.char_count} chars, {reference.line_count} lines){suffix}; "
        f"use /inspect output {reference.output_id}"
    )


__all__ = [
    "format_workspace_query_result",
    "publish_workspace_query_result",
    "render_publication",
]
