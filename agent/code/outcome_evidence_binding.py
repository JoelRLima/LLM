"""Runtime binding of selected bytes and prepared operation facts."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.code.change_models import FileChange
from agent.llm.context_projection import (
    REQUIRED_EVIDENCE,
    UNTRUSTED_WORKSPACE,
    ContextSourceRecord,
)
from agent.runtime.path_safety import assert_no_link_ancestors, resolve_workspace_path

from .outcome_evidence import (
    MAX_EVIDENCE_RECORDS,
    MAX_FILE_CONTENT_RECORDS,
    MAX_RECORD_TEXT_CHARS,
    MAX_TOTAL_TEXT_CHARS,
    CodeEvidenceManifest,
    CodeEvidenceRecord,
)


def _new_evidence_id(kind: str) -> str:
    return f"code-evidence:{kind.casefold()}:{uuid4().hex}"


def bind_selected_file_evidence(
    root: str | Path,
    observed_files: Sequence[Any],
    *,
    user_objective: str | None = None,
) -> CodeEvidenceManifest:
    """Bind runtime-owned selected-file bytes into a bounded manifest."""

    records: list[CodeEvidenceRecord] = []
    total_text = 0
    complete = True
    omitted = 0
    workspace = Path(root).expanduser().resolve()
    for item in observed_files:
        if len(records) >= MAX_EVIDENCE_RECORDS or sum(
            record.kind == "FILE_CONTENT" for record in records
        ) >= MAX_FILE_CONTENT_RECORDS:
            omitted += 1
            complete = False
            continue
        path = str(getattr(item, "path", ""))
        observed_text = str(getattr(item, "observed_text", ""))
        observed_hash = getattr(item, "content_hash", None)
        truncated = bool(getattr(item, "truncated", False))
        lossless = True
        current_hash = observed_hash if isinstance(observed_hash, str) else None
        try:
            current = resolve_workspace_path(workspace, path, require_file=True)
            assert_no_link_ancestors(current)
            raw = current.read_bytes()
            current_hash = hashlib.sha256(raw).hexdigest()
            lossless = raw.decode("utf-8").encode("utf-8") == raw
            if isinstance(observed_hash, str) and observed_hash != current_hash:
                complete = False
            if not truncated and raw.decode("utf-8") != observed_text:
                complete = False
                lossless = False
        except (OSError, UnicodeDecodeError, RuntimeError, ValueError):
            lossless = False
            complete = False
        record_complete = not truncated and lossless and len(observed_text) <= MAX_RECORD_TEXT_CHARS
        if total_text + len(observed_text) > MAX_TOTAL_TEXT_CHARS:
            record_complete = False
            truncated = True
            complete = False
            remaining = max(0, MAX_TOTAL_TEXT_CHARS - total_text)
            content = observed_text[:remaining]
        else:
            content = observed_text
        total_text += len(content)
        records.append(
            CodeEvidenceRecord(
                evidence_id=_new_evidence_id("file_content"),
                kind="FILE_CONTENT",
                path=path,
                sha256=current_hash,
                content=content,
                complete=record_complete,
                truncated=truncated,
                text_lossless=lossless,
                provenance_reason="runtime-observed ContextSelector file bytes",
            )
        )
        complete = complete and record_complete
    if user_objective is not None:
        encoded = user_objective.encode("utf-8")
        text = user_objective[:MAX_RECORD_TEXT_CHARS]
        if len(user_objective) > MAX_RECORD_TEXT_CHARS:
            complete = False
        records.append(
            CodeEvidenceRecord(
                evidence_id=_new_evidence_id("user_literal"),
                kind="USER_LITERAL",
                content=text,
                complete=len(text) == len(user_objective),
                truncated=len(text) != len(user_objective),
                text_lossless=True,
                sha256=hashlib.sha256(encoded).hexdigest(),
                provenance_reason="current user objective bound by runtime",
            )
        )
        total_text += len(text)
    manifest = CodeEvidenceManifest(
        manifest_id=f"code-manifest:{uuid4().hex}",
        records=tuple(records),
        complete=complete and omitted == 0,
        omitted_count=omitted,
        total_text_chars=total_text,
    )
    if not manifest.validate():
        return CodeEvidenceManifest(
            manifest.manifest_id,
            manifest.records,
            complete=False,
            omitted_count=max(1, manifest.omitted_count),
            total_text_chars=manifest.total_text_chars,
        )
    return manifest


def prepared_change_evidence(
    changes: Sequence[FileChange],
    preview: Any,
) -> tuple[ContextSourceRecord, ...]:
    """Project a bounded prepared-operation manifest without raw diff framing."""

    records: list[ContextSourceRecord] = []
    for index, change in enumerate(changes[:MAX_EVIDENCE_RECORDS]):
        data: dict[str, Any] = {
            "evidence_id": f"prepared-change:{getattr(preview, 'change_set_id', '')}:{index}",
            "kind": change.kind.value.upper(),
            "source_path": change.path,
            "base_hash": change.base_hash,
        }
        if change.destination_path is not None:
            data["destination_path"] = change.destination_path
        if change.kind.value != "move":
            data["prepared_content_hash"] = hashlib.sha256(
                str(change.content or "").encode("utf-8")
            ).hexdigest()
        records.append(
            ContextSourceRecord(
                source_id=str(data["evidence_id"]),
                source_kind="prepared_change",
                necessity=REQUIRED_EVIDENCE,
                trust_class=UNTRUSTED_WORKSPACE,
                reason="runtime-prepared operation manifest",
                data=data,
            )
        )
    return tuple(records)


__all__ = ["bind_selected_file_evidence", "prepared_change_evidence"]
