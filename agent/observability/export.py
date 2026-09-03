"""Deterministic, redacted diagnostic bundle export."""

from __future__ import annotations

import hashlib
import platform
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent import __version__
from agent.observability.bookmarks import BookmarkStore
from agent.observability.redaction import canonical_json, redact_observation_value
from agent.presentation.service import InspectionService
from agent.runtime.filesystem_primitives import write_bytes_atomic
from agent.runtime.path_safety import WorkspacePathError, assert_path_safe, resolve_path

EXPORT_SCHEMA_VERSION = 1
DEFAULT_EXPORT_NAME = "diagnostic-trace.zip"


@dataclass(frozen=True, slots=True)
class ExportReceipt:
    path: str
    run_id: str
    completeness: str
    sha256: str
    size_bytes: int
    files: tuple[str, ...]
    overwritten: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "run_id": self.run_id,
            "completeness": self.completeness,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "files": list(self.files),
            "overwritten": self.overwritten,
        }


class DiagnosticExporter:
    """Export only the selected service's redacted observability read model."""

    def __init__(self, service: InspectionService) -> None:
        self.service = service

    def _default_output(self, run_id: str) -> Path:
        export_root = Path(self.service.workspace_paths.trace_exports_dir)
        return resolve_path(
            export_root / f"{run_id_hash(run_id)}-{DEFAULT_EXPORT_NAME}",
            reject_link_like=True,
        )

    @staticmethod
    def _safe_output(path: Path) -> Path:
        try:
            selected = resolve_path(path, reject_link_like=True)
            assert_path_safe(selected.parent, directory=True)
            assert_path_safe(selected)
        except (WorkspacePathError, IsADirectoryError) as exc:
            raise FileExistsError("export destination is link-like or not a regular path") from exc
        selected.parent.mkdir(parents=True, exist_ok=True)
        try:
            assert_path_safe(selected.parent, directory=True)
            assert_path_safe(selected)
        except (WorkspacePathError, IsADirectoryError) as exc:
            raise FileExistsError("export destination is link-like or not a regular path") from exc
        return selected

    @staticmethod
    def _zip_bytes(files: Mapping[str, bytes]) -> bytes:
        from io import BytesIO

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in sorted(files):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 0
                info.external_attr = 0
                archive.writestr(info, files[name])
        return buffer.getvalue()

    def export(
        self,
        run_id: str | None = None,
        *,
        output: str | Path | None = None,
        force: bool = False,
        include_bookmarks: bool = False,
    ) -> ExportReceipt:
        selected = self.service.select(run_id)
        chosen_run_id = selected.metadata.run_id
        destination = self._safe_output(Path(output) if output is not None else self._default_output(chosen_run_id))
        existed = destination.exists()
        if existed and not force:
            raise FileExistsError(f"export destination exists; use --force: {destination}")

        trace_lines = [item.to_json() for item in selected.read_result.records]
        metadata = redact_observation_value(selected.metadata.to_dict())
        snapshot = redact_observation_value(self.service.snapshot(chosen_run_id, limit=100).to_dict())
        files: dict[str, bytes] = {
            "metadata.json": (canonical_json(metadata) + "\n").encode("utf-8"),
            "trace.jsonl": ("\n".join(trace_lines) + ("\n" if trace_lines else "")).encode("utf-8"),
            "snapshot.json": (canonical_json(snapshot) + "\n").encode("utf-8"),
            "environment.json": (
                canonical_json(
                    redact_observation_value(
                        {
                            "schema_version": EXPORT_SCHEMA_VERSION,
                            "agent_version": __version__,
                            "platform": platform.system(),
                            "python": platform.python_version(),
                            "observability_mode": selected.metadata.observability_mode.value,
                        }
                    )
                )
                + "\n"
            ).encode("utf-8"),
        }
        if include_bookmarks:
            bookmarks = BookmarkStore(self.service.workspace_paths).reader(chosen_run_id)
            files["bookmarks.json"] = (canonical_json(redact_observation_value({"bookmarks": bookmarks})) + "\n").encode("utf-8")

        manifest = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "run_id": chosen_run_id,
            "trace_completeness": selected.read_result.completeness.value,
            "issues": list(selected.read_result.issues),
            "files": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())},
        }
        files["manifest.json"] = (canonical_json(redact_observation_value(manifest)) + "\n").encode("utf-8")
        bundle = self._zip_bytes(files)

        write_bytes_atomic(destination, bundle)
        return ExportReceipt(
            path=str(destination),
            run_id=chosen_run_id,
            completeness=selected.read_result.completeness.value,
            sha256=hashlib.sha256(bundle).hexdigest(),
            size_bytes=len(bundle),
            files=tuple(sorted(files)),
            overwritten=existed,
        )


def run_id_hash(run_id: str) -> str:
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]


__all__ = ["DiagnosticExporter", "ExportReceipt", "EXPORT_SCHEMA_VERSION", "run_id_hash"]
