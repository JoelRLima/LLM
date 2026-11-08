"""Trusted desktop inputs; plugin output and model text never call this owner."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from llm_agent.agent.capabilities import Capability
from llm_agent.application.plugin_administration import PluginAdministration
from llm_agent.interactive.session_navigation import _close_runtime, _reset_presentation


def snapshot(session: Any) -> dict[str, Any]:
    service = PluginAdministration(session.start.app_paths, session.workspace)
    result = service.snapshot()
    authorized = dict(session.start.startup_extension_fingerprints or ())
    for item in result["items"]:
        item["session_authorized"] = (
            item["activation_status"] == "ready" and authorized.get(item["extension_id"]) == item["manifest_sha256"]
        )
        item["execution"]["session_mode"] = session.execution_mode()
        item["execution"]["session_authorized"] = item["session_authorized"]
    result["authorized_ids"] = sorted(authorized)
    result["mode"] = session.execution_mode()
    return result


def _selection(state: dict[str, Any], extension_id: str, fingerprint: str) -> dict[str, Any]:
    item = next((item for item in state["items"] if item["extension_id"] == extension_id), None)
    if item is None or not fingerprint or item["manifest_sha256"] != fingerprint:
        raise ValueError("Plugin alterado ou selecao desatualizada. Atualize a lista.")
    return dict(item)


def _authorize(session: Any, state: dict[str, Any], item: dict[str, Any], remove: bool) -> None:
    authorized = dict(session.start.startup_extension_fingerprints or ())
    extension_id = item["extension_id"]
    if remove:
        authorized.pop(extension_id, None)
    else:
        if item["activation_status"] != "ready" or item["manifest_status"] != "unchanged":
            raise ValueError("Habilite o plugin e conceda suas permissoes antes de autorizar.")
        if item["execution"]["review_status"] != "matched":
            raise ValueError("O comando do plugin não pôde ser conferido. Atualize a lista e revise o manifest.")
        known = {cap.value for cap in Capability}
        if set(item["required_capabilities"]) - known:
            raise ValueError("Plugin declara capacidades nao suportadas por esta interface.")
        authorized[extension_id] = item["manifest_sha256"]
        for selected_id, fingerprint in authorized.items():
            selected = _selection(state, selected_id, fingerprint)
            if selected["activation_status"] != "ready":
                raise ValueError("Uma autorizacao anterior ficou indisponivel. Limpe as autorizacoes.")
    _close_runtime(session)
    _reset_presentation(session)
    session.start = replace(
        session.start,
        startup_capabilities=tuple(cap.value for cap in Capability) if authorized else None,
        startup_extension_ids=tuple(sorted(authorized)) if authorized else None,
        startup_extension_fingerprints=tuple(sorted(authorized.items())) if authorized else None,
    )


def _admin_apply(
    session: Any, state: dict[str, Any], action: str, extension_id: str, value: str, fingerprint: str
) -> None:
    if action not in {
        "register",
        "enable",
        "disable",
        "grant",
        "revoke",
        "clear_authority",
        "replace",
        "remove",
        "forget",
    }:
        raise ValueError("Operacao de plugin desconhecida.")
    if action == "forget":
        if not any(item["extension_id"] == extension_id and item["manifest_sha256"] is None for item in state["items"]):
            raise ValueError("Only orphaned workspace configurations can be forgotten")
    elif action not in {"register", "clear_authority"}:
        _selection(state, extension_id, fingerprint)
    _close_runtime(session)
    _reset_presentation(session)
    session.start = replace(
        session.start, startup_capabilities=None, startup_extension_ids=None, startup_extension_fingerprints=None
    )
    service = PluginAdministration(session.start.app_paths, session.workspace)
    if action == "replace":
        service.replace_manifest(extension_id, fingerprint, value)
    elif action != "clear_authority":
        service.apply(action, extension_id, value)


def apply(
    session: Any, workspace_id: str, action: str, extension_id: str, value: str, fingerprint: str
) -> dict[str, Any]:
    if workspace_id != session.workspace.workspace_id:
        raise ValueError("O projeto mudou. Abra o gerenciador novamente.")
    if session.controller.pending.list():
        raise ValueError("Resolva os pedidos pendentes antes de alterar plugins.")
    state = snapshot(session)
    if action in {"authorize", "deauthorize"}:
        _authorize(session, state, _selection(state, extension_id, fingerprint), action == "deauthorize")
    else:
        _admin_apply(session, state, action, extension_id, value, fingerprint)
    return snapshot(session)
