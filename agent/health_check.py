"""Public health-check facade and command-line entry point."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict

from agent.health.core import CheckResult
from agent.health.runtime_checks import (
    check_logs,
    check_orphan_dirs,
    check_permissions,
    check_skills,
)
from agent.health.standalone import (
    OutputFormat,
    render_health_report,
    run_standalone_health_check,
)
from agent.health.state_checks import (
    check_config,
    check_file_hashes,
    check_memory,
    check_python_version,
)
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext

__all__ = [
    "CheckResult", "check_config", "check_file_hashes", "check_logs", "check_memory",
    "check_orphan_dirs", "check_permissions", "check_python_version", "check_skills",
    "render_health_report", "run_health_check",
]


def run_health_check(
    write_report: bool = False,
    verbose: bool = True,
    *,
    app_paths: AppPaths | None = None,
    workspace: WorkspaceContext | str | Path | None = None,
    config_path: str | Path | None = None,
    profile: str | None = None,
    environment: Mapping[str, str] | None = None,
    output_format: OutputFormat = "human",
) -> Dict[str, Any]:
    paths = app_paths or AppPaths.discover()
    selected_workspace = workspace or Path.cwd()
    report = run_standalone_health_check(
        app_paths=paths,
        workspace=selected_workspace,
        config_path=config_path,
        profile=profile,
        environment=environment,
        write_report=write_report,
    )
    if verbose:
        print(render_health_report(report, output_format))
    return report


def main() -> int:
    report = run_health_check(write_report=False, verbose=True)
    return 0 if report["readiness"]["offline_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
