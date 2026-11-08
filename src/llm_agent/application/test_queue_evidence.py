"""Canonical evidence loading shared by scheduling and inspection."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from llm_agent.application.test_queue_manifest import local_path, read_document


def read_case_evidence(
    root: Path, case_id: str, row: dict[str, Any], *, inline_limit_bytes: int | None = None,
) -> dict[str, Any]:
    """Use canonical paths and retain the durable scheduler's status."""
    if not row.get("evidence_path"):
        return dict(row)
    try:
        path = local_path(root, f"cases/{case_id}/result.json")
        if inline_limit_bytes is not None and path.stat().st_size > inline_limit_bytes:
            return dict(row)
        detail = read_document(path, max_bytes=64 * 1024 * 1024)
        return {**detail, "status": row["status"]}
    except (OSError, ValueError, RuntimeError) as exc:
        return {**row, "detail_error": f"Evidence unavailable: {type(exc).__name__}"}
