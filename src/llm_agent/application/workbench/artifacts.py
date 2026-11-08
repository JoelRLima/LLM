"""Lazy presentation references; reporting and OutputService retain ownership."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from pathlib import Path

from llm_agent.application.context import WorkspacePaths
from llm_agent.application.workbench.contracts import (
    NATIVE_CONTRIBUTIONS,
    Completeness,
    PanelContent,
    PanelReference,
)
from llm_agent.filesystem.primitives import is_link_like
from llm_agent.outputs.service import OutputService

REPORT_PREVIEW_BYTES = 256 * 1024


class WorkbenchArtifacts:
    """Session-local references, not another artifact store or report writer."""

    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths
        self.outputs = OutputService(paths)
        self._references: dict[tuple[str, str | None, str], PanelReference] = {}

    def _retain(self, reference: PanelReference) -> PanelReference:
        if reference.key not in self._references and len(self._references) >= 256:
            self._references.pop(next(iter(self._references)))
        self._references[reference.key] = reference
        return reference

    def offer_report(self, observation: Mapping[str, object]) -> PanelReference | None:
        """Only the application settlement may offer a report; never parse chat links."""
        path = observation.get("report_path")
        if not isinstance(path, str) or not path:
            return None
        run_id = observation.get("run_id")
        phase = observation.get("report_completeness")
        completeness: Completeness = "final" if phase == "final" else "partial" if phase == "partial" else "unknown"
        return self._retain(PanelReference(
            self.paths.workspace_id, run_id if isinstance(run_id, str) else None,
            "report:" + os.path.abspath(path), "Relatório da execução", "TaskReportBuilder",
            "report", completeness, os.path.abspath(path),
        ))

    def list_outputs(self) -> tuple[PanelReference, ...]:
        """Metadata only; absent lifecycle metadata stays unknown, even after a run."""
        return tuple(self._retain(PanelReference(
            self.paths.workspace_id, item.run_id, item.output_id, item.title,
            item.source.value, item.kind.value, "partial" if item.source_truncated else "unknown",
            item.output_id,
        )) for item in self.outputs.list())

    def read(self, reference: PanelReference, *, offset: int = 0) -> PanelContent:
        if self._references.get(reference.key) != reference:
            raise ValueError("artifact was not offered in this workspace session")
        renderer = next(item.renderer for item in NATIVE_CONTRIBUTIONS if item.kind == reference.kind)
        if reference.kind != "report":
            metadata = self.outputs.metadata(reference.locator)
            chunk = self.outputs.read_chunk(reference.locator, offset=offset)
            return PanelContent(reference, chunk.text, renderer, metadata.payload_sha256,
                                metadata.source_truncated or not chunk.eof, chunk.next_offset)
        if offset:
            raise ValueError("reports use a bounded preview, not pagination")
        path = Path(reference.locator)
        if any(is_link_like(part) for part in (path, *path.parents)):
            raise ValueError("linked report paths are not supported by the preview")
        with path.open("rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("report is not a regular file")
            raw = stream.read(REPORT_PREVIEW_BYTES + 1)
        truncated = len(raw) > REPORT_PREVIEW_BYTES
        preview = raw[:REPORT_PREVIEW_BYTES]
        # A truncated digest must never be represented as the whole artifact's digest.
        digest = None if truncated else hashlib.sha256(raw).hexdigest()
        text = preview.decode("utf-8", errors="replace")
        if path.suffix.lower() != ".json" or truncated:
            renderer = "text"
        return PanelContent(reference, text, renderer, digest, truncated)
