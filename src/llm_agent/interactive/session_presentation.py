"""Status, profile and plugin projections for interactive session adapters."""

from __future__ import annotations

from typing import Any, cast

from llm_agent.interactive import _session_actions


class InteractiveSessionPresentationMixin:
    def availability_snapshot(self: Any) -> dict[str, Any]:
        self.availability.refresh(self.start)
        return cast(
            dict[str, Any],
            self.availability.project(self.controller.is_busy() or self.queries.is_busy()),
        )


    def presentation_snapshot(self: Any) -> dict[str, Any]:
        availability = self.availability_snapshot()
        profile = self.availability.profile
        return {
            "workspace_id": self.workspace.workspace_id,
            "workspace_name": self.workspace.root.name or str(self.workspace.root),
            "workspace_root": str(self.workspace.root),
            "mode": self.execution_mode(),
            "profile": (
                {"name": profile.name, "provider": profile.provider, "model": profile.model}
                if profile is not None
                else None
            ),
            "availability": availability,
            "llm_enabled": self.llm_enabled,
            "commands": self.command_catalog(),
        }


    def plugin_state(self: Any) -> dict[str, Any]:
        from llm_agent.interactive import session_plugins

        with self._lock:
            self._check_idle()
            return cast(dict[str, Any], session_plugins.snapshot(self))


    def plugin_action(self: Any, workspace_id: str, action: str, extension_id: str = "", value: str = "", fingerprint: str = "") -> dict[str, Any]:
        from llm_agent.interactive import session_plugins

        if any(not isinstance(item, str) or len(item) > 16000 for item in (workspace_id, action, extension_id, value, fingerprint)):
            raise ValueError("Invalid plugin request")
        with self._lock:
            self._check_idle()
            return cast(dict[str, Any], session_plugins.apply(self, workspace_id, action, extension_id, value, fingerprint))


    def execution_mode(self: Any) -> str:
        return _session_actions.execution_mode(self)


    def navigation_options(self: Any) -> dict[str, Any]:
        from llm_agent.interactive.session_navigation import navigation_options

        with self._lock:
            self._check_idle()
            return cast(dict[str, Any], navigation_options(self))


    def configure_model_profile(self: Any, workspace_id: str, name: str, model: str, base_url: str) -> dict[str, Any]:
        from llm_agent.interactive.session_navigation import configure_profile

        with self._lock:
            self._check_idle()
            if workspace_id != self.workspace.workspace_id:
                raise ValueError("O projeto mudou; reabra a configuração do perfil.")
            return configure_profile(self, name, model, base_url)


    def command_catalog(self: Any) -> list[dict[str, Any]]:
        return _session_actions.command_catalog(self)
