"""Workspace-confined paths used by the trace spine."""

from __future__ import annotations

from pathlib import Path


class WorkspaceTracePaths:
    """Trace paths mixed into the existing workspace path value object."""

    state_dir: Path

    @property
    def traces_dir(self) -> Path:
        return self.state_dir / "traces"

    @property
    def trace_index_file(self) -> Path:
        return self.traces_dir / "index.json"

    @property
    def trace_exports_dir(self) -> Path:
        return self.state_dir / "trace_exports"

    @property
    def trace_directories(self) -> tuple[Path, ...]:
        return self.traces_dir, self.trace_exports_dir
