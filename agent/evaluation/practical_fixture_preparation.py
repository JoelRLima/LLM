"""Preparation callbacks for practical evaluation fixtures."""

from __future__ import annotations

import hashlib
from pathlib import Path

from agent.evaluation.contracts import CapabilityScenario
from agent.evaluation.evaluation_identity import run_fixture_git
from agent.memory.memory import AgentMemory
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext


def _run_git(root: Path, *arguments: str) -> None:
    return_code, stdout, stderr = run_fixture_git(root, *arguments)
    if return_code != 0:
        detail = (stderr or stdout or "git fixture command failed").strip()
        raise RuntimeError(detail[-2_000:])


def prepare_practical_workspace(
    scenario: CapabilityScenario,
    workspace: Path,
) -> None:
    """Perform only the declared pre-measurement fixture preparation."""

    preparation = str(scenario.metadata.get("preparation", "none"))
    if preparation == "stale_memory":
        return
    if preparation == "dirty_repository":
        _run_git(workspace, "init", "-b", "main")
        _run_git(workspace, "add", "--", "app.py", "notes.txt")
        _run_git(
            workspace,
            "-c",
            "user.name=Practical Fixture",
            "-c",
            "user.email=practical-fixture@example.invalid",
            "commit",
            "-m",
            "fixture baseline",
        )
        (workspace / "notes.txt").write_text(
            "preexisting local edit\n",
            encoding="utf-8",
        )
        return
    if preparation != "none":
        raise ValueError(f"preparação prática desconhecida: {preparation}")


def prepare_practical_application(
    scenario: CapabilityScenario,
    workspace: Path,
    app_paths: AppPaths,
) -> None:
    """Prepare only isolated application-home state for a practical fixture."""

    preparation = str(scenario.metadata.get("preparation", "none"))
    if preparation == "stale_memory":
        old_bytes = b'MODE = "old"\n'
        workspace_context = WorkspaceContext.create(workspace)
        workspace_paths = app_paths.for_workspace(workspace_context.workspace_id)
        workspace_paths.ensure_directories()
        memory = AgentMemory(
            db_path=workspace_paths.memory_db_file,
            default_file=workspace_paths.memory_file,
            backup_dir=workspace_paths.memory_backup_dir,
        )
        memory.store_file_observation(
            "feature.py",
            "MODE atual \u00e9 old",
            {
                "source_hash": hashlib.sha256(old_bytes).hexdigest(),
                "source": "file_reader",
                "observed": True,
            },
        )
        memory.persist_to_file()
        del memory
        return
    if preparation == "dirty_repository":
        # Git state is entirely workspace-visible and was established by the
        # pre-measurement fixture callback.
        return
    if preparation != "none":
        raise ValueError("unknown practical preparation")


def prepare_practical_scenario(
    scenario: CapabilityScenario,
    workspace: Path,
    app_paths: AppPaths,
) -> None:
    """Compatibility wrapper for callers that still invoke both phases."""

    prepare_practical_workspace(scenario, workspace)
    prepare_practical_application(scenario, workspace, app_paths)

__all__ = [
    "prepare_practical_application",
    "prepare_practical_scenario",
    "prepare_practical_workspace",
]
