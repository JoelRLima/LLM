"""UI neutral session navigation actions shared by interactive adapters."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application.inspection import (
    InspectionListRequest,
    InspectionReadContext,
    InspectionReadRequest,
    InspectionUnavailableError,
    list_inspection_runs,
    read_inspection,
)
from llm_agent.application.model_profile_selection import select_default_model_profile
from llm_agent.application.services.queries import ReadOnlyWorkspaceQueryService
from llm_agent.application.task_execution import close_task_execution
from llm_agent.application.workbench.artifacts import WorkbenchArtifacts
from llm_agent.application.workspace_recents import list_recent_workspaces, remember_recent_workspace
from llm_agent.interactive.availability import Availability
from llm_agent.interactive.query_executor import BoundedQueryExecutor
from llm_agent.interactive.ui_plane import RunViewModel
from llm_agent.workspace.context import WorkspaceContext

ACTIONS = frozenset({
    "model.show", "model.select", "workspace.show", "workspace.switch", "inspection.open",
})


def _load_config(session: Any) -> dict[str, Any]:
    overrides = {"default_model_profile": session.start.profile} if session.start.profile else None
    return ConfigRepository(session.start.app_paths, config_path=session.start.config_path).load(
        overrides=overrides
    ).to_dict()


def _payload(match: Any) -> str:
    value = getattr(match, "raw_payload", "")
    return value.strip() if isinstance(value, str) else ""


def navigation_options(session: Any) -> dict[str, Any]:
    """Project safe labels only; credentials and endpoint settings stay private."""
    error = None
    try:
        config = _load_config(session)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        config = {}
        error = str(exc)[:1000]
    profiles = config.get("model_profiles", {})
    return {
        "workspace_id": session.workspace.workspace_id,
        "profiles": [
            {"name": name, "provider": value.get("provider"), "model": value.get("model")}
            for name, value in list(profiles.items())[:100]
            if isinstance(name, str) and isinstance(value, dict)
        ] if isinstance(profiles, dict) else [],
        "recent_workspaces": [str(path) for path in list_recent_workspaces(session.start.app_paths)],
        **({"configuration_error": error} if error is not None else {}),
    }


def _profile(session: Any, selected: str, config: dict[str, Any]) -> dict[str, Any]:
    if selected not in config.get("model_profiles", {}):
        raise ValueError(f"unknown model profile: {selected}")
    _close_runtime(session)
    select_default_model_profile(
        config, selected, session.start.app_paths,
        config_path=session.start.config_path,
    )
    session.start = replace(session.start, profile=selected)
    session.availability = Availability()
    session.availability.refresh(session.start)
    _reset_presentation(session)
    answer = f"Model profile {selected} selected."
    return {"status": "succeeded", "action": "model.select", "profile": selected,
            "answer": answer, "session_changed": True}


def configure_profile(session: Any, name: str, model: str, base_url: str) -> dict[str, Any]:
    from llm_agent.application.model_profile_setup import configure_compatible_profile

    saved = configure_compatible_profile(
        session.start.app_paths,
        name,
        model,
        base_url,
        config_path=session.start.config_path,
    )
    config = _load_config(session)
    result = _profile(session, str(saved["profile"]["name"]), config)
    return {**result, "configured_path": saved["config_path"]}


def _close_runtime(session: Any) -> None:
    if session._detach is not None:
        session._detach()
        session._detach = None
    if session._runtime is not None:
        close_task_execution(session._runtime)
        session._runtime = None
    session._runtime_identity = None


def _reset_presentation(session: Any) -> None:
    mailbox = getattr(session, "mailbox", None)
    if mailbox is not None:
        mailbox.drain()
    session.view = RunViewModel()
    session._previews.clear()


def _switch_workspace(session: Any, requested: str) -> dict[str, Any]:
    if session.controller.pending.list():
        raise ValueError("discard or submit pending items before switching workspace")
    requested = requested.strip()
    if len(requested) >= 2 and requested[0] == requested[-1] and requested[0] in {"'", '"'}:
        requested = requested[1:-1]
    target_path = Path(requested).expanduser()
    if not target_path.is_dir():
        raise ValueError("workspace path must be an existing directory")
    target = WorkspaceContext.create(target_path)
    if target.root == session.workspace.root:
        return {"status": "succeeded", "action": "workspace.switch", "workspace_root": str(target.root),
                "answer": f"Workspace already active: {target.root}", "session_changed": False}
    _close_runtime(session)
    session.start = replace(session.start, workspace=target.root, startup_capabilities=None,
                            startup_extension_ids=None, startup_extension_fingerprints=None)
    session.workspace = target
    session.query_service = ReadOnlyWorkspaceQueryService(target)
    session.queries = BoundedQueryExecutor(workspace_id=target.workspace_id)
    session._artifacts = WorkbenchArtifacts(session.start.app_paths.for_workspace(target.workspace_id))
    _reset_presentation(session)
    session.availability = Availability()
    session.availability.refresh(session.start)
    remember_recent_workspace(session.start.app_paths, target.root)
    answer = f"Workspace switched to {target.root}."
    return {"status": "succeeded", "action": "workspace.switch", "workspace_id": target.workspace_id,
            "workspace_root": str(target.root), "answer": answer, "session_changed": True}


def execute(session: Any, match: Any) -> tuple[dict[str, Any], None]:
    """Run one supported idle-only navigation action on the session worker."""
    action = getattr(match, "action_id", None)
    if action not in ACTIONS:
        raise ValueError(f"unsupported session navigation action: {action}")
    payload = _payload(match)
    if action == "model.show":
        return (_model_summary(session, action), None)
    if action == "model.select":
        if not payload:
            raise ValueError("model.select requires a profile name")
        return (_select_profile(session, payload), None)
    if action == "workspace.show":
        return (_workspace_summary(session, action), None)
    if action == "workspace.switch":
        if not payload:
            raise ValueError("workspace.switch requires a workspace path")
        return (_switch_workspace(session, payload), None)
    return (_inspection_summary(session, action, payload), None)


def _model_summary(session: Any, action: str) -> dict[str, Any]:
    config = _load_config(session)
    profiles = config.get("model_profiles", {})
    profile = session.start.profile or config.get("default_model_profile")
    value = profiles.get(profile, {}) if isinstance(profiles, dict) else {}
    provider = value.get("provider") if isinstance(value, dict) else None
    model = value.get("model") if isinstance(value, dict) else None
    return {
        "status": "succeeded",
        "action": action,
        "profile": profile,
        "provider": provider,
        "model": model,
        "answer": f"Profile {profile}: {provider or 'unknown'} / {model or 'unknown'}",
    }


def _select_profile(session: Any, payload: str) -> dict[str, Any]:
    return _profile(session, payload, _load_config(session))


def _workspace_summary(session: Any, action: str) -> dict[str, Any]:
    root = str(session.workspace.root)
    name = session.workspace.root.name or root
    return {
        "status": "succeeded",
        "action": action,
        "workspace_id": session.workspace.workspace_id,
        "workspace_root": root,
        "workspace_name": name,
        "answer": f"Active workspace: {root}",
    }


def _inspection_summary(session: Any, action: str, payload: str) -> dict[str, Any]:
    # /inspect [run-id] returns the latest snapshot; `list` enumerates retained runs.
    workspace_paths = session.start.app_paths.for_workspace(session.workspace.workspace_id)
    context = InspectionReadContext(workspace_paths)
    if payload.casefold() in {"list", "runs"}:
        runs = list_inspection_runs(context, InspectionListRequest())
        projected = [dict(run) for run in runs]
        return {"status": "succeeded", "action": action, "runs": projected,
                "answer": json.dumps(projected, ensure_ascii=False)}
    request = InspectionReadRequest(run_id=payload or None)
    try:
        inspection = dict(read_inspection(context, request))
    except InspectionUnavailableError:
        if payload:
            raise
        return {"status": "succeeded", "action": action, "available": False,
                "reason_code": "NO_RETAINED_TRACES", "answer": "Ainda nao ha execucoes retidas neste projeto para inspecionar."}
    return {"status": "succeeded", "action": action, "inspection": inspection,
            "answer": json.dumps(inspection, ensure_ascii=False)}


__all__ = ["ACTIONS", "execute"]
