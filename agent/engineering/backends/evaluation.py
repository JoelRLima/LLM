"""PRACTICAL backend adapter owned by the canonical evaluation runner."""

from __future__ import annotations

from collections.abc import Mapping

from agent.engineering.contracts import (
    EngineeringBackendOutcome,
    EngineeringBackendProtocolError,
    EngineeringBackendStatus,
    EngineeringExecutionContext,
    EngineeringRequest,
)
from agent.evaluation.practical import FaultPlanV1, FaultPlanV1Error


class EvaluationBackend:
    def execute(self, request: EngineeringRequest, context: EngineeringExecutionContext) -> EngineeringBackendOutcome:
        if context.source_repository is None:
            return EngineeringBackendOutcome(EngineeringBackendStatus.FAILED, {"status": "source_required"}, (), False, False)
        # Keep the optional MCP startup path free of AgentApplication/model imports.
        from agent.evaluation.practical import run_practical_scripted

        profile = request.parameters.get("profile", "current")
        if profile not in {"current", "persona-reference-w18"}:
            raise EngineeringBackendProtocolError("unknown evaluation profile")
        selected_fault_plan = context.fault_plan
        if selected_fault_plan is None and request.parameters.get("fault") is not None:
            try:
                selected_fault_plan = FaultPlanV1.from_dict(request.parameters["fault"])
            except (FaultPlanV1Error, TypeError, ValueError) as exc:
                raise EngineeringBackendProtocolError("invalid evaluation fault plan") from exc
        if selected_fault_plan is not None and not isinstance(selected_fault_plan, FaultPlanV1):
            raise EngineeringBackendProtocolError("invalid evaluation fault plan")
        report = run_practical_scripted(
            context.source_repository.root,
            profile_id=profile,
            fault_plan=selected_fault_plan,
        )
        result_summary = report.get("summary")
        result_summary = result_summary if isinstance(result_summary, Mapping) else {}
        complete = report.get("execution_complete") is True
        accepted = bool(report.get("candidate_unchanged")) and complete and result_summary.get("failed") == 0
        scenarios = report.get("scenarios", ())
        receipt_ids = [
            item.get("receipt", {}).get("receipt_id")
            for item in scenarios
            if isinstance(item, Mapping) and isinstance(item.get("receipt"), Mapping)
        ]
        status = "passed" if accepted else "failed"
        summary = {
            "status": status,
            "accepted": accepted,
            "profile_id": report.get("profile_id"),
            "experiment_id": report.get("experiment_id"),
            "trial_id": report.get("trial_id"),
            "scenario_set_identity": report.get("practical_fixture_identity"),
            "execution_complete": complete,
            "count": len(scenarios) if isinstance(scenarios, (list, tuple)) else 0,
            "passed": result_summary.get("passed", 0),
            "failed": result_summary.get("failed", 0),
            "candidate_unchanged": bool(report.get("candidate_unchanged")),
            "receipt_count": len(receipt_ids),
            "receipt_ids": receipt_ids,
            "fault_plan_fingerprint": report.get("fault_plan_fingerprint"),
            "fault_steps": report.get("fault_steps", []),
            "faults_requested": report.get("faults_requested", 0),
            "faults_triggered": report.get("faults_triggered", 0),
            "faults_untriggered": report.get("faults_untriggered", 0),
        }
        return EngineeringBackendOutcome(
            EngineeringBackendStatus.SUCCEEDED if accepted else EngineeringBackendStatus.FAILED,
            summary,
            (),
            False,
            False,
        )


__all__ = ["EvaluationBackend"]
