"""Borda de skill para os casos de uso do domínio de código."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional, cast

from agent.approval import (
    ApprovalPort,
    AutoApprove,
    RequireExplicitApproval,
)
from agent.code.application import (
    CodeRequest,
    CodingApplicationService,
    build_code_context,
)
from agent.llm.contracts import ModelGateway
from agent.planning.task_semantics_proposal import is_proposal_only_objective
from agent.runtime.context import TaskExecutionContext
from agent.runtime.mutation_evidence import project_mutation_evidence
from agent.skills.base import BaseSkill
from agent.skills.code_task_support import (
    OrchestratorMetricsSink,
    PolicyApprover,
    ProposalOnlyApprover,
)
from agent.skills.invocation_cancellation import with_invocation_cancellation
from agent.tools.invocation_semantics import resolve_invocation_semantics


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
            "type": "object",
            "properties": {
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
                "objective": {
                    "type": "string",
                    "description": "Objetivo da mudança ou análise.",
                },
                "targets": {
                    "type": "array",
                    "description": "Arquivos relevantes ao objetivo.",
                },
                "include_tests": {
                    "type": "boolean",
                    "description": "Executa testes descobertos além de sintaxe.",
                },
                "graph": {
                    "type": "object",
                    "description": "TaskGraph usado pela ação multitask.",
                },
                "template": {
                    "type": "string",
                    "enum": [
                        "parallel_analyze",
                        "parallel_review",
                        "analyze_then_modify",
                    ],
                    "description": "Template determinístico, sem planejamento por LLM.",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        }

    @staticmethod
    def _result_dict(result: Any) -> Dict[str, Any]:
        data = asdict(result)
        data["status"] = result.status.value
        return data

    @staticmethod
    def _effect_projection(result: Any) -> Dict[str, Any]:
        evidence = project_mutation_evidence(result)
        return {
            "affected_files": evidence.surviving_files,
            "attempted_files": evidence.affected_files,
            "mutation_occurred": evidence.mutation_occurred,
            "attempted_mutation": evidence.attempted,
            "persisted_mutation": evidence.persisted_mutation,
            "surviving_mutation": evidence.survives,
            "rollback_occurred": evidence.rollback_occurred,
            "validation": evidence.validation_status,
            "final_state": evidence.final_state or ("proposed" if evidence.attempted else None),
        }

    def execute(self, args: dict) -> dict:
        return self._execute(args)

    def execute_with_context(
        self,
        args: dict,
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> dict:
        """Carry the gateway cancellation boundary into the code workflow."""

        return self._execute(
            args,
            cancellation_token=cancellation_token,
            cancellation_event=cancellation_event,
        )

    def _execute(
        self,
        args: dict,
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> dict:
        action = str(args.get("action", "analyze"))
        objective = str(args.get("objective", ""))
        targets_raw = args.get("targets", [])
        targets = [str(item) for item in targets_raw] if isinstance(targets_raw, list) else []
        include_tests = bool(args.get("include_tests", False))
        try:
            metrics_sink = (
                OrchestratorMetricsSink(self.orchestrator._log_metric)
                if self.orchestrator is not None
                else None
            )
            parent_context = (
                self.orchestrator.task_execution_context
                if self.orchestrator is not None
                and hasattr(self.orchestrator, "task_execution_context")
                else None
            )
            parent_context = with_invocation_cancellation(
                parent_context,
                cancellation_token,
                cancellation_event,
            )
            # The concrete code operation, rather than the tool-wide
            # descriptor ceiling, determines the child context's minimum
            # permissions.  This keeps read-only analyze/review paths from
            # inheriting write authority.
            child_permissions = resolve_invocation_semantics(self, args).required_capabilities
            if self.orchestrator is not None:
                child_permissions &= frozenset(
                    getattr(self.orchestrator, "allowed_capabilities", child_permissions)
                )
            context = build_code_context(
                self.config,
                self.model_gateway,
                metrics_sink=metrics_sink,
                parent_context=parent_context,
                permissions=child_permissions if parent_context is not None else None,
            )
            if parent_context is None:
                context = cast(
                    TaskExecutionContext,
                    with_invocation_cancellation(
                        context,
                        cancellation_token,
                        cancellation_event,
                    ),
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
                    include_tests=include_tests,
                    graph=graph if isinstance(graph, dict) else None,
                    template=str(args["template"]) if isinstance(args.get("template"), str) else None,
                ),
                approver=(
                    ProposalOnlyApprover()
                    if is_proposal_only_objective(objective)
                    else PolicyApprover(self.approval_policy)
                ),
            )
        except Exception as exc:
            return {"ok": False, "done": True, "error": str(exc), "message": str(exc)}
        data = self._result_dict(result)
        effect_projection = self._effect_projection(result)
        return {
            "ok": result.status.value == "succeeded",
            "done": True,
            "status": result.status.value,
            "data": data,
            **effect_projection,
            "error": result.error,
            "message": result.summary,
        }
