"""Operation adapters exposed to the native Workbench bridge."""

from __future__ import annotations

from typing import Any, cast

from llm_agent.interfaces.workbench import (
    _bridge_controls,
    _bridge_plugins,
    _bridge_test_queues,
)


class WorkbenchBridgeOperationsMixin:
    def attention(self: Any) -> dict[str, Any] | None:
        return _bridge_controls.attention(self)


    def plugin_state(self: Any) -> dict[str, Any]:
        return cast(dict[str, Any], _bridge_plugins.plugin_request(self))


    def plugin_pick_manifest(self: Any, workspace_id: str) -> dict[str, Any]:
        if not isinstance(workspace_id, str) or len(workspace_id) > 256:
            return {"accepted": False, "message": "Identificador de projeto invalido."}
        return cast(dict[str, Any], _bridge_plugins.pick_manifest(self, workspace_id))


    def test_queue_state(self: Any, workspace_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], _bridge_test_queues.request(self, lambda: self._session.test_queue_state(workspace_id)))


    def test_queue_select(self: Any, workspace_id: str, action: str, value: str) -> dict[str, Any]:
        return cast(dict[str, Any], _bridge_test_queues.request(self, lambda: self._session.test_queue_select(workspace_id, action, value), idle=True))


    def test_queue_action(self: Any, workspace_id: str, queue_id: str, fingerprint: str, action: str,
                          value: str = "", options: dict[str, Any] | None = None) -> dict[str, Any]:
        if action not in {"pause", "stop", "next", "check", "details", "detach"}:
            return {"accepted": False, "message": "Acao de testes invalida."}
        return cast(dict[str, Any], _bridge_test_queues.request(self,
            lambda: self._session.test_queue_action(workspace_id, queue_id, fingerprint, action, value, options),
            idle=action in {"next", "check", "detach"}))


    def test_queue_pick_directory(self: Any, workspace_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], _bridge_test_queues.pick(self, workspace_id))


    def test_case_create(self: Any, workspace_id: str, source_root: str, case_id: str, objective: str,
                         check_kind: str, output_path: str, expected_text: str = "",
                         seed_path: str = "", seed_content: str = "") -> dict[str, Any]:
        limits = (256, 15_000, 64, 16_000, 32, 1_024, 65_536, 1_024, 65_536)
        values = (
            workspace_id, source_root, case_id, objective, check_kind,
            output_path, expected_text, seed_path, seed_content,
        )
        if any(not isinstance(value, str) or len(value) > limit for value, limit in zip(values, limits, strict=True)):
            return {"accepted": False, "message": "Dados da tarefa inválidos ou extensos demais."}
        return cast(dict[str, Any], _bridge_test_queues.request(
            self,
            lambda: self._session.create_test_case(workspace_id, source_root, case_id, objective,
                                                   check_kind, output_path, expected_text, seed_path, seed_content),
            idle=True,
        ))


    def test_queue_export(self: Any, workspace_id: str, queue_id: str, fingerprint: str) -> dict[str, Any]:
        return cast(dict[str, Any], _bridge_test_queues.export(self, workspace_id, queue_id, fingerprint))


    def test_queue_start(self: Any, workspace_id: str, queue_id: str, fingerprint: str, options: dict[str, Any]) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._start_request(
                lambda _request_id, on_admitted: self._session.test_queue_start(
                    workspace_id,
                    queue_id,
                    fingerprint,
                    options,
                    _on_admitted=on_admitted,
                )
            ),
        )


    def plugin_action(self: Any, workspace_id: str, action: str, extension_id: str = "", value: str = "", fingerprint: str = "") -> dict[str, Any]:
        return cast(dict[str, Any], _bridge_plugins.plugin_request(self, (workspace_id, action, extension_id, value, fingerprint)))


    def navigation_options(self: Any) -> dict[str, Any]:
        if self._closed:
            return {"accepted": False, "message": "Sessão encerrada."}
        read = getattr(self._session, "navigation_options", None)
        if read is None:
            return {"accepted": False, "message": "Use Comandos para escolher projeto e perfil."}
        try:
            return {"accepted": True, **read()}
        except (OSError, RuntimeError, ValueError) as exc:
            return {"accepted": False, "message": str(exc)}


    def configure_profile(self: Any, workspace_id: str, name: str, model: str, base_url: str) -> dict[str, Any]:
        values = (workspace_id, name, model, base_url)
        if any(not isinstance(value, str) or len(value) > 4096 for value in values):
            return {"accepted": False, "message": "Dados do perfil inválidos."}
        with self._condition:
            if self._closed or self._active_request_id or self._events or self._maintenance_running:
                return {"accepted": False, "message": "Aguarde a sessão ficar livre antes de configurar o perfil."}
            if workspace_id != self._session.workspace.workspace_id:
                return {"accepted": False, "message": "O projeto mudou; atualize a interface."}
            self._maintenance_running = True
        try:
            self._session.configure_model_profile(workspace_id, name, model, base_url)
            return {"accepted": True, "session": self.initialize()}
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            return {"accepted": False, "message": str(exc)[:1000]}
        finally:
            with self._condition:
                self._maintenance_running = False


    def resolve_attention(self: Any, attention_id: int, decision: str, run_generation: int, request_fingerprint: str) -> dict[str, Any]:
        return _bridge_controls.resolve_attention(self, attention_id, decision, run_generation, request_fingerprint)
