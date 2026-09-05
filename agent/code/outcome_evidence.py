"""Runtime-bound evidence records and manifest validation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.llm.context_projection import (
    REQUIRED_EVIDENCE,
    UNTRUSTED_WORKSPACE,
    ContextSourceRecord,
)
from agent.runtime.path_safety import assert_no_link_ancestors, resolve_workspace_path

from .outcome_evidence_support import parent_depth
from .outcome_evidence_support import supports_no_change as _supports_no_change

MAX_EVIDENCE_RECORDS = 32
MAX_FILE_CONTENT_RECORDS = 16
MAX_RECORD_TEXT_CHARS = 16_384
MAX_TOTAL_TEXT_CHARS = 32_768
MAX_PARENT_IDS = 8
MAX_PARENT_DEPTH = 4


@dataclass(frozen=True, slots=True)
class CodeEvidenceRecord:
    evidence_id: str
    kind: str
    path: str | None = None
    sha256: str | None = None
    content: str | None = None
    complete: bool = True
    truncated: bool = False
    text_lossless: bool = True
    provenance_reason: str = "runtime observation"
    parent_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.evidence_id) is not str
            or type(self.kind) is not str
            or not self.evidence_id.strip()
            or not self.kind.strip()
        ):
            raise ValueError("evidence_id obrigatÃ³rio")
        if len(self.parent_evidence_ids) > MAX_PARENT_IDS:
            raise ValueError("parent_evidence_ids excede o limite")
        if self.content is not None and len(self.content) > MAX_RECORD_TEXT_CHARS:
            raise ValueError("payload textual excede o limite por registro")
        if self.truncated and self.complete:
            object.__setattr__(self, "complete", False)
        if not self.text_lossless and self.complete:
            object.__setattr__(self, "complete", False)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "complete": self.complete,
            "truncated": self.truncated,
            "text_lossless": self.text_lossless,
            "provenance_reason": self.provenance_reason,
            "parent_evidence_ids": list(self.parent_evidence_ids),
        }
        if self.path is not None:
            result["path"] = self.path
        if self.sha256 is not None:
            result["sha256"] = self.sha256
        if self.content is not None:
            result["content"] = self.content
        return result

    @classmethod
    def from_dict(cls, value: Any) -> "CodeEvidenceRecord":
        """Restore one runtime-bound record without accepting extra fields."""

        if not isinstance(value, Mapping):
            raise ValueError("registro de evidencia invalido")
        allowed = {
            "evidence_id", "kind", "path", "sha256", "content", "complete",
            "truncated", "text_lossless", "provenance_reason", "parent_evidence_ids",
        }
        if set(value) - allowed:
            raise ValueError("registro de evidencia contem campos desconhecidos")
        required = {"evidence_id", "kind", "complete", "truncated", "text_lossless"}
        if not required.issubset(value):
            raise ValueError("registro de evidencia incompleto")
        evidence_id = value["evidence_id"]
        kind = value["kind"]
        if type(evidence_id) is not str or type(kind) is not str:
            raise ValueError("identidade do registro de evidencia invalida")
        flags = tuple(value[key] for key in ("complete", "truncated", "text_lossless"))
        if not all(type(flag) is bool for flag in flags):
            raise ValueError("flags do registro de evidencia invalidas")
        parents = value.get("parent_evidence_ids", [])
        if not isinstance(parents, list) or any(type(item) is not str for item in parents):
            raise ValueError("parent_evidence_ids invalido")
        for key in ("path", "sha256", "content", "provenance_reason"):
            if key in value and value[key] is not None and not isinstance(value[key], str):
                raise ValueError(f"{key} invalido")
        return cls(
            evidence_id=evidence_id,
            kind=kind,
            path=value.get("path"),
            sha256=value.get("sha256"),
            content=value.get("content"),
            complete=flags[0],
            truncated=flags[1],
            text_lossless=flags[2],
            provenance_reason=value.get("provenance_reason", "runtime observation"),
            parent_evidence_ids=tuple(parents),
        )

    def to_context_record(self) -> ContextSourceRecord:
        return ContextSourceRecord(
            source_id=self.evidence_id,
            source_kind="code_evidence",
            necessity=REQUIRED_EVIDENCE,
            trust_class=UNTRUSTED_WORKSPACE,
            reason=self.provenance_reason,
            estimated_tokens=max(1, len(json.dumps(self.to_dict(), ensure_ascii=False)) // 4),
            truncated=self.truncated,
            complete=self.complete,
            data=self.to_dict(),
            identity=self.sha256,
            freshness="CURRENT_RUNTIME_OBSERVATION" if self.complete else "INCOMPLETE",
        )


@dataclass(frozen=True, slots=True)
class CodeEvidenceManifest:
    manifest_id: str
    records: tuple[CodeEvidenceRecord, ...]
    complete: bool = True
    omitted_count: int = 0
    total_text_chars: int = 0

    @property
    def by_id(self) -> dict[str, CodeEvidenceRecord]:
        return {record.evidence_id: record for record in self.records}

    @property
    def context_records(self) -> tuple[ContextSourceRecord, ...]:
        return tuple(record.to_context_record() for record in self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_manifest_id": self.manifest_id,
            "records": [record.to_dict() for record in self.records],
            "complete": self.complete,
            "omitted_count": self.omitted_count,
            "total_text_chars": self.total_text_chars,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "CodeEvidenceManifest":
        """Restore the exact runtime manifest projection used by a seal."""

        if not isinstance(value, Mapping):
            raise ValueError("manifesto de evidencia invalido")
        allowed = {
            "evidence_manifest_id", "records", "complete", "omitted_count",
            "total_text_chars",
        }
        if set(value) != allowed:
            raise ValueError("manifesto de evidencia contem campos invalidos")
        records = value["records"]
        if not isinstance(records, list):
            raise ValueError("records do manifesto invalidos")
        complete = value["complete"]
        omitted_count = value["omitted_count"]
        total_text_chars = value["total_text_chars"]
        manifest_id = value["evidence_manifest_id"]
        if type(complete) is not bool:
            raise ValueError("complete do manifesto invalido")
        if type(omitted_count) is not int or omitted_count < 0:
            raise ValueError("omitted_count do manifesto invalido")
        if type(total_text_chars) is not int or total_text_chars < 0:
            raise ValueError("total_text_chars do manifesto invalido")
        if type(manifest_id) is not str or not manifest_id.strip():
            raise ValueError("identidade do manifesto invalida")
        manifest = cls(
            manifest_id=manifest_id,
            records=tuple(CodeEvidenceRecord.from_dict(item) for item in records),
            complete=complete,
            omitted_count=omitted_count,
            total_text_chars=total_text_chars,
        )
        if not manifest.validate():
            raise ValueError("manifesto de evidencia fora dos limites")
        return manifest

    def required_file_content_ids(self, evidence_ids: Sequence[str]) -> tuple[str, ...]:
        """Return the complete FILE_CONTENT leaves of a cited parent DAG."""

        known = self.by_id
        leaves: list[str] = []
        visiting: set[str] = set()

        def visit(evidence_id: str) -> bool:
            if evidence_id in visiting:
                return False
            record = known.get(evidence_id)
            if record is None:
                return False
            if record.kind == "FILE_CONTENT":
                if evidence_id not in leaves:
                    leaves.append(evidence_id)
                return True
            if record.kind != "ANALYSIS_FACT" or not record.parent_evidence_ids:
                return False
            visiting.add(evidence_id)
            valid = all(visit(parent) for parent in record.parent_evidence_ids)
            visiting.remove(evidence_id)
            return valid

        if any(
            not isinstance(evidence_id, str) or not visit(evidence_id)
            for evidence_id in evidence_ids
        ):
            return ()
        return tuple(leaves)

    def validate(self) -> bool:
        if len(self.records) > MAX_EVIDENCE_RECORDS:
            return False
        if len({record.evidence_id for record in self.records}) != len(self.records):
            return False
        if sum(record.kind == "FILE_CONTENT" for record in self.records) > MAX_FILE_CONTENT_RECORDS:
            return False
        total = sum(len(record.content or "") for record in self.records)
        if total > MAX_TOTAL_TEXT_CHARS or total != self.total_text_chars:
            return False
        known = self.by_id
        for record in self.records:
            if len(record.parent_evidence_ids) > MAX_PARENT_IDS:
                return False
            if any(parent not in known for parent in record.parent_evidence_ids):
                return False
            if parent_depth(
                record.evidence_id,
                known,
                set(),
                max_depth=MAX_PARENT_DEPTH,
            ) > MAX_PARENT_DEPTH:
                return False
        return True

    def revalidate_files(
        self,
        root: str | Path,
        evidence_ids: Sequence[str] | None = None,
    ) -> bool:
        workspace = Path(root).expanduser().resolve()
        wanted = set(evidence_ids or (record.evidence_id for record in self.records))
        for record in self.records:
            if record.evidence_id not in wanted or record.kind != "FILE_CONTENT":
                continue
            if not record.path or not record.sha256 or not record.complete:
                return False
            try:
                current = resolve_workspace_path(workspace, record.path, require_file=True)
                assert_no_link_ancestors(current)
                if hashlib.sha256(current.read_bytes()).hexdigest() != record.sha256:
                    return False
            except (OSError, RuntimeError, ValueError):
                return False
        return True

    def supports_no_change(
        self,
        evidence_ids: Sequence[str],
        *,
        target_paths: Sequence[str] = (),
    ) -> bool:
        return _supports_no_change(
            self,
            tuple(evidence_ids),
            target_paths=tuple(target_paths),
        )


__all__ = [
    "CodeEvidenceManifest",
    "CodeEvidenceRecord",
    "MAX_EVIDENCE_RECORDS",
    "MAX_FILE_CONTENT_RECORDS",
    "MAX_PARENT_DEPTH",
    "MAX_PARENT_IDS",
    "MAX_RECORD_TEXT_CHARS",
    "MAX_TOTAL_TEXT_CHARS",
]
