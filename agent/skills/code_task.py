"""Borda de skill para os casos de uso do domínio de código."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

from agent.approval import (
    ApprovalDecision,
    ApprovalPort,
    ApprovalRequest,
    AutoApprove,
    RequireExplicitApproval,
)
from agent.code.application import (
    CodeRequest,
    CodingApplicationService,
    build_code_context,
)
from agent.code.changes import ChangePreview
from agent.code.policy import ProposalAssessment
from agent.llm.contracts import ModelGateway
from agent.skills.base import BaseSkill


class _PolicyApprover:
    requires_explicit_approval = True

    def __init__(self, policy: ApprovalPort) -> None:
        self.policy = policy

    def approve(
        self,
        preview: ChangePreview,
        assessment: ProposalAssessment,
    ) -> ApprovalDecision:
        return self.policy.request(
            ApprovalRequest(
                action="apply_changeset",
                resource=", ".join(preview.affected_files),
                prompt=f"Aplicar ChangeSet em {len(preview.affected_files)} arquivo(s)?",
                metadata={
                    "confidence": assessment.confidence,
                    "reasons": assessment.reasons,
                },
            )
        )


class _OrchestratorMetricsSink:
    def __init__(self, callback: Any) -> None:
        self._callback = callback

    def record(self, metric: Dict[str, Any]) -> None:
        self._callback(metric)


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
        approval_policy: ApprovalPort | None = None,
    ) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.model_gateway = model_gateway
        self.config = config or {}
        self.approval_policy = approval_policy or (
            AutoApprove()
            if self.config.get("auto_confirm") is True
            else RequireExplicitApproval()
        )
        self.orchestrator: Any | None = None

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
            metrics_sink = (
                _OrchestratorMetricsSink(self.orchestrator._log_metric)
                if self.orchestrator is not None
                else None
            )
            context = build_code_context(
                self.config, self.model_gateway, metrics_sink=metrics_sink
            )
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
                approver=_PolicyApprover(self.approval_policy),
            )
        except Exception as exc:
            return {"ok": False, "done": True, "error": str(exc), "message": str(exc)}
        return {
            "ok": result.status.value == "succeeded",
            "done": True,
            "status": result.status.value,
            "data": self._result_dict(result),
            "error": result.error,
            "message": result.summary,
        }
