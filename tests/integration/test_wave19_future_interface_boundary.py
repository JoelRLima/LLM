from __future__ import annotations

import json
from pathlib import Path

from agent.application_services.queries import (
    ReadOnlyWorkspaceQueryService,
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryStatus,
)


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


def test_synthetic_non_cli_adapter_uses_typed_capability_and_bounded_serialization(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("adapter", encoding="utf-8")
    result = ReadOnlyWorkspaceQueryService(tmp_path).execute(
        WorkspaceQueryRequest(WorkspaceQueryKind.READ, {"file_path": "note.md"}),
        _NeverCancelled(),
    )
    document = {"kind": result.kind.value, "status": result.status.value, "data": result.data, "truncated": result.truncated}
    assert json.loads(json.dumps(document))["status"] == WorkspaceQueryStatus.SUCCEEDED.value
