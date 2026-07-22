from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from agent.security.security_scanner import consolidate


class SecurityAnalysisService:
    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    def run(self, objective: str, on_chunk: Callable[[str], None] | None = None) -> str | None:
        target = self._target_file(objective)
        skill = self.orchestrator.skills.get("code_analyzer")
        if not target or not skill:
            return None
        result = skill.execute({"target": target, "mode": "security"})
        if not result.get("ok"):
            return None
        findings = consolidate(result.get("data", {}))
        if not findings:
            return self._answer_without_findings(target, objective, on_chunk)
        prompt = self._build_prompt(target, objective, findings)
        return self._answer_with_prompt(prompt, objective, on_chunk)

    def _target_file(self, objective: str) -> str | None:
        hints = self.orchestrator.context_manager.get_file_hints(objective)
        for line in hints.splitlines():
            if ".py" in line:
                return str(line.strip("- ").split(" ")[0])
        return None

    def _answer_without_findings(
        self, target: str, objective: str, on_chunk: Callable[[str], None] | None
    ) -> str:
        result = self.orchestrator.execution_gateway.execute_validated_plan(
            [{"tool": "file_reader", "args": {"file_path": target}}], objective, {}
        )
        if result.aborted:
            return result.final_answer or "A análise foi interrompida."
        return str(self.orchestrator.final_responder.build_final_answer(objective, on_chunk=on_chunk))

    @staticmethod
    def _build_prompt(target: str, objective: str, findings: list[Any]) -> str:
        selected = sorted(
            findings, key=lambda item: item.metadata.get("default_priority", 0), reverse=True
        )[:10]
        facts = json.dumps([
            {
                "id": item.pattern_id,
                "padrão": item.pattern,
                "arquivo": item.location,
                "linha": item.start_line,
                "símbolo": item.symbol,
                "trecho": item.snippet,
                "por_que": item.metadata.get("why_interesting", ""),
            }
            for item in selected
        ], indent=2, ensure_ascii=False)
        return (
            f"Você é um auditor de segurança. Analise os fatos extraídos de '{target}':\n{facts}\n\n"
            f"Objetivo original: {objective}\n"
            "Confirme cada vulnerabilidade, classifique a severidade e descreva a exploração."
        )

    def _answer_with_prompt(
        self, prompt: str, objective: str, on_chunk: Callable[[str], None] | None
    ) -> str:
        if not self.orchestrator.session.messages:
            return str(self.orchestrator.final_responder.build_final_answer(objective, on_chunk=on_chunk))
        original = self.orchestrator.session.messages[-1]["content"]
        self.orchestrator.session.messages[-1]["content"] = prompt
        try:
            return str(self.orchestrator.final_responder.build_final_answer(objective, on_chunk=on_chunk))
        finally:
            self.orchestrator.session.messages[-1]["content"] = original
