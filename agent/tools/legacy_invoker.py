"""Explicit compatibility invoker for pre-microkernel callers.

The standalone composition never installs this path.  Keeping it isolated
prevents legacy skill execution from silently becoming a second authority
boundary in the new runtime.
"""

from __future__ import annotations

from typing import Any

from agent.llm.prompts import ERROR_PATTERNS
from agent.parsers import normalize_tool_result


class LegacyToolInvoker:
    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    def invoke(self, tool_name: str, args: dict[str, Any], record_result: bool = True) -> dict[str, Any]:
        if tool_name not in self.orchestrator.skills:
            raise KeyError(f"Tool '{tool_name}' não foi registrada no Orchestrator.")
        result: dict[str, Any]
        if self.orchestrator.active_skills and tool_name not in self.orchestrator.active_skills:
            result = {
                "ok": False,
                "done": True,
                "status": "permission_denied",
                "data": None,
                "error": f"Tool '{tool_name}' não está permitida para esta persona.",
                "message": None,
            }
        else:
            try:
                raw_result = self.orchestrator.skills[tool_name].execute(args)
            except Exception as exc:
                raw_result = {
                    "ok": False,
                    "done": True,
                    "status": "failed",
                    "data": None,
                    "error": f"Erro ao executar tool: {exc}",
                    "message": "Exceção durante a execução da ferramenta.",
                }
            result = dict(normalize_tool_result(raw_result, ERROR_PATTERNS))
        if record_result:
            self.orchestrator.agent_state.record_tool_result(tool_name, args, result)
        return result
