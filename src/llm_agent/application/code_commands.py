"""Execute the finite /code use case for CLI consumers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from llm_agent.agent.code.application import CodeRequest, CodingApplicationService, build_code_context
from llm_agent.agent.code.changes import ChangePreview as _AgentChangePreview
from llm_agent.agent.code.commands import CODE_COMMAND_HELP, CodeCommandError, parse_code_command
from llm_agent.agent.code.policy import ProposalAssessment as _AgentProposalAssessment
from llm_agent.agent.tools.invocation_semantics import CODE_TASK_ACTIONS
from llm_agent.agent.tools.mode_enforcement import requests_test_execution
from llm_agent.application.code_review import CodeReviewAssessment, CodeReviewPreview, _project_code_review
from llm_agent.application.conversation import ConversationRuntime, _resolve_gateway

_ApprovalCallback = Callable[[CodeReviewPreview, CodeReviewAssessment], bool]
_ApprovalFactory = Callable[[bool], _ApprovalCallback | None]
_Kind = Literal["parse_error", "mode_denied", "tests_denied", "help", "executed"]


@dataclass(frozen=True)
class CodeCommandOutcome:
    kind: _Kind
    status: str
    summary: str = ""
    error: str | None = None
    answer: str | None = None
    help_text: str | None = None
    artifacts: tuple[tuple[str, str | None], ...] = ()
    diagnostics: tuple[tuple[str, str, str, str], ...] = ()


class _CallbackApprover:
    requires_explicit_approval = True

    def __init__(self, callback: _ApprovalCallback) -> None:
        self._callback = callback

    def approve(self, preview: _AgentChangePreview, assessment: _AgentProposalAssessment) -> bool:
        review_preview, review_assessment = _project_code_review(preview, assessment)
        return self._callback(review_preview, review_assessment)


def execute_code_command(
    text: str,
    *,
    config: dict[str, Any],
    conversation: ConversationRuntime,
    workspace_root: str | Path,
    allows_write_validate: Callable[[], bool],
    is_full_mode: Callable[[], bool],
    approval_factory: _ApprovalFactory,
    register_cancellation: Callable[[Callable[[], None]], None] | None = None,
    cancellation_requested: Callable[[], bool] | None = None,
) -> CodeCommandOutcome:
    """Interpret, admit, execute and project one explicit code command."""
    try:
        parsed = parse_code_command(text)
    except CodeCommandError as exc:
        message = str(exc)
        return CodeCommandOutcome("parse_error", "failed", summary=message, error=message)

    if parsed.action in CODE_TASK_ACTIONS - {"analyze", "review"}:
        if not allows_write_validate():
            return CodeCommandOutcome(
                "mode_denied", "blocked", summary="ação negada",
                error="Ação negada pelo modo operacional ativo.",
            )
        if requests_test_execution({"include_tests": parsed.include_tests}) and not is_full_mode():
            return CodeCommandOutcome(
                "tests_denied", "blocked", summary="modo FULL exigido",
                error="Execução de testes exige modo FULL.",
            )

    if parsed.action == "help":
        return CodeCommandOutcome(
            "help", "succeeded", summary="/code help", answer="/code help", help_text=CODE_COMMAND_HELP,
        )

    request = CodeRequest(
        action=parsed.action,
        objective=parsed.objective,
        targets=parsed.targets,
        include_tests=parsed.include_tests,
        template=parsed.template,
    )
    context = build_code_context(config, _resolve_gateway(conversation))
    if register_cancellation is not None:
        register_cancellation(context.cancellation.cancel)
        if cancellation_requested is not None and cancellation_requested():
            context.cancellation.cancel()

    service = CodingApplicationService(workspace_root, context, config)
    callback = approval_factory(parsed.assume_yes)
    result = service.execute(request, approver=_CallbackApprover(callback) if callback is not None else None)
    return CodeCommandOutcome(
        "executed",
        result.status.value,
        summary=result.summary,
        error=result.error,
        artifacts=tuple((artifact.kind, artifact.content) for artifact in result.artifacts),
        diagnostics=tuple(
            (
                str(item.get("code", "diagnostic")),
                str(item.get("file_path", "")),
                str(item.get("line", "")),
                str(item.get("message", "")),
            )
            for item in result.diagnostics
        ),
    )


__all__ = ["CodeCommandOutcome", "execute_code_command"]
