"""Borda de skill para os casos de uso do domínio de código."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

from agent.code.application import (
    CodeRequest,
    CodingApplicationService,
    build_code_context,
)
from agent.code.changes import ChangePreview
from agent.code.policy import ProposalAssessment
from agent.llm.contracts import ModelGateway
from agent.skills.base import BaseSkill


class _ConfiguredApprover:
    def approve(self, preview: ChangePreview, assessment: ProposalAssessment) -> bool:
        del preview, assessment
        return True


class CodeTaskSkill(BaseSkill):
    name = "code_task"
    description = (
        "Executa casos de uso modulares de código: analyze, review, generate, "
        "modify, repair, refactor e multitask. Mudanças usam ChangeSet e validação."
    )

    def __init__(
        self,
        base_dir: str = ".",
        model_gateway: Optional[ModelGateway] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.model_gateway = model_gateway
        self.config = config or {}

    def get_schema(self) -> dict:
        return {
            "action": {
                "type": "string",
                "enum": [
                    "analyze",
                    "review",
                    "generate",
                    "modify",
                    "repair",
                    "refactor",
                    "multitask",
                    "template",
                ],
                "description": "Caso de uso de engenharia de código.",
            },
            "objective": {"type": "string", "description": "Objetivo da mudança ou análise."},
            "targets": {"type": "array", "description": "Arquivos relevantes ao objetivo."},
            "include_tests": {"type": "boolean", "description": "Executa testes descobertos além de sintaxe."},
            "graph": {"type": "object", "description": "TaskGraph usado pela ação multitask."},
            "template": {
                "type": "string",
                "enum": ["parallel_analyze", "parallel_review", "analyze_then_modify"],
                "description": "Template determinístico, sem planejamento por LLM.",
            },
        }

    @staticmethod
    def _result_dict(result: Any) -> Dict[str, Any]:
        data = asdict(result)
        data["status"] = result.status.value
        return data

    def execute(self, args: dict) -> dict:
        action = str(args.get("action", "analyze"))
        objective = str(args.get("objective", ""))
        targets_raw = args.get("targets", [])
        targets = [str(item) for item in targets_raw] if isinstance(targets_raw, list) else []
        try:
            context = build_code_context(self.config, self.model_gateway)
            graph = args.get("graph")
            result = CodingApplicationService(
                self.base_dir,
                context,
                self.config,
            ).execute(
                CodeRequest(
                    action=action,
                    objective=objective,
                    targets=tuple(targets),
                    include_tests=bool(args.get("include_tests", False)),
                    graph=graph if isinstance(graph, dict) else None,
                    template=str(args["template"]) if isinstance(args.get("template"), str) else None,
                ),
                approver=_ConfiguredApprover() if self.config.get("auto_confirm") else None,
            )
        except Exception as exc:
            return {"ok": False, "done": True, "error": str(exc), "message": str(exc)}
        return {
            "ok": result.status.value == "succeeded",
            "done": True,
            "data": self._result_dict(result),
            "error": result.error,
            "message": result.summary,
        }
