"""Canonical result commit and incident preservation for gateway invocations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Dict

from agent.execution_incidents import (
    CANONICAL_COMMIT_FAILED,
    EFFECT_NONE,
    EFFECT_PROVEN,
    EFFECT_UNKNOWN,
)
from agent.reporting.artifact_projection import project_artifact_evidence
from agent.runtime.logging import logger
from agent.tools.contracts import ToolError, ToolInvocation, ToolResult, ToolStatus
from agent.tools.invocation_lifecycle import InvocationAttempt


class InvocationCommitMixin:
    def _emit_denial(
        self: Any,
        result: ToolResult,
        record_result: bool,
        tool_name: str,
        args: Dict[str, Any],
    ) -> None:
        self._emit(
            "tool_denied",
            {
                "invocation_id": result.invocation_id,
                "status": result.status.value,
                "reason": result.error.code if result.error else "DENIED",
            },
        )
        self._record(tool_name, args, result, record_result)

    def _complete_denial(
        self: Any,
        attempt: InvocationAttempt,
        result: ToolResult,
        record_result: bool,
        tool_name: str,
        args: Dict[str, Any],
    ) -> None:
        if not attempt.worker_pending:
            attempt.mark_quiescent()
        if not attempt.claim_terminal():
            return
        self._emit_denial(result, record_result, tool_name, args)
        if attempt.can_release():
            self._finish_invocation(attempt.invocation_id)

    def _complete_result(
        self: Any,
        attempt: InvocationAttempt,
        invocation: ToolInvocation,
        result: ToolResult,
        record_result: bool,
    ) -> ToolResult:
        if not attempt.claim_terminal():
            return result
        committed_result = result
        commit_error = self._record(
            invocation.tool_name, invocation.args, result, record_result
        )
        if commit_error is not None:
            committed_result = self._canonical_commit_failure(invocation, result)
        self._emit(
            "tool_end",
            {
                "tool": invocation.tool_name,
                "invocation_id": invocation.invocation_id,
                "status": committed_result.status.value,
                "ok": committed_result.ok,
                "lifecycle": attempt.lifecycle.value,
            },
        )
        if attempt.can_release():
            self._finish_invocation(attempt.invocation_id)
        return committed_result

    def _canonical_commit_failure(
        self: Any, invocation: ToolInvocation, result: ToolResult
    ) -> ToolResult:
        effect_state = self._incident_effect_state(
            result,
            result.to_legacy_dict(include_details=True),
        )
        with self._invocation_lock:
            self._canonical_commit_failed.add(invocation.invocation_id)
        self._record_execution_incident(invocation, result)
        return replace(
            result,
            status=ToolStatus.UNVERIFIED,
            error=ToolError(
                CANONICAL_COMMIT_FAILED,
                "A execucao terminou, mas o commit do estado canonico falhou.",
                {
                    "original_status": result.status.value,
                    "physical_effect_unknown": effect_state == EFFECT_UNKNOWN,
                },
            ),
            message=(
                "Execucao nao verificada: commit canonico falhou; "
                "efeitos fisicos podem ter ocorrido."
            ),
        )

    @staticmethod
    def _incident_effect_state(result: ToolResult, projected: Mapping[str, Any]) -> str:
        artifact = project_artifact_evidence(projected)
        if artifact.persisted_mutation or artifact.mutation_occurred or artifact.rollback_occurred:
            return EFFECT_PROVEN
        if result.executed is False:
            return EFFECT_NONE
        data = projected.get("data")
        if isinstance(data, Mapping) and _explicit_no_effect(data):
            return EFFECT_NONE
        return EFFECT_UNKNOWN

    def _record_execution_incident(
        self: Any,
        invocation: ToolInvocation,
        result: ToolResult,
    ) -> None:
        recorder = getattr(self, "incident_recorder", None)
        if not callable(recorder):
            logger.warning(
                "[GATEWAY] Canonical commit failure has no incident recorder for '%s'",
                invocation.tool_name,
            )
            return
        projected = result.to_legacy_dict(include_details=True)
        artifact = project_artifact_evidence(projected)
        rollback = _rollback_projection(projected, artifact.rollback_occurred)
        incident = {
            "incident_type": CANONICAL_COMMIT_FAILED,
            "invocation_id": invocation.invocation_id,
            "tool": invocation.tool_name,
            "original_tool_status": result.status.value,
            "executed": result.executed,
            "effect_state": self._incident_effect_state(result, projected),
            "affected_files": list(artifact.affected_files),
            "rollback_occurred": rollback,
            "error_code": CANONICAL_COMMIT_FAILED,
        }
        try:
            recorder(incident)
        except Exception as exc:
            logger.warning(
                "[GATEWAY] Failed to preserve canonical commit incident: %s",
                type(exc).__name__,
            )

    def _emit(self: Any, event_type: str, data: Dict[str, Any]) -> None:
        if self.event_emitter is not None:
            try:
                self.event_emitter(event_type, data)
            except Exception as exc:
                logger.warning(
                    "[GATEWAY] Erro ao emitir evento '%s': %s",
                    event_type,
                    type(exc).__name__,
                )

    def _record(
        self: Any,
        tool_name: str,
        args: Dict[str, Any],
        result: ToolResult,
        record_result: bool,
    ) -> Exception | None:
        if not record_result or self.state_recorder is None:
            return None
        try:
            self.state_recorder(tool_name, args, result)
        except Exception as exc:
            logger.warning(
                "[GATEWAY] Erro ao registrar resultado no estado: %s",
                type(exc).__name__,
            )
            if self.state_recorder_is_canonical:
                return exc
        return None

    def _is_canonical_commit_failed(self: Any, invocation_id: str) -> bool:
        with self._invocation_lock:
            return invocation_id in self._canonical_commit_failed


def _explicit_no_effect(data: Mapping[str, Any]) -> bool:
    return bool(
        data.get("mutation_occurred") is False
        and any(
            key in data
            for key in (
                "mutation_occurred",
                "persisted_mutation",
                "rollback_occurred",
                "final_state",
            )
        )
    )


def _rollback_projection(projected: Mapping[str, Any], default: bool) -> bool:
    data = projected.get("data")
    if isinstance(data, Mapping) and type(data.get("rollback_occurred")) is bool:
        return bool(data["rollback_occurred"])
    return default


__all__ = ["InvocationCommitMixin"]
