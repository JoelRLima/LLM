"""Deterministic offline health backend for the canonical Engineering registry."""

from __future__ import annotations

from collections.abc import Mapping

from agent.engineering.contracts import (
    EngineeringBackendOutcome,
    EngineeringBackendStatus,
    EngineeringExecutionContext,
    EngineeringRequest,
)


class HealthBackend:
    def execute(self, request: EngineeringRequest, context: EngineeringExecutionContext) -> EngineeringBackendOutcome:
        del request
        if context.workspace is None:
            return EngineeringBackendOutcome(
                EngineeringBackendStatus.FAILED,
                {"status": "workspace_required", "checks": []},
                (),
                False,
                False,
            )
        from agent.health.standalone import run_standalone_health_check

        report = run_standalone_health_check(
            app_paths=context.app_paths,
            workspace=context.workspace.root,
            write_report=False,
            online=False,
        )
        checks = [
            {"id": item.get("id"), "status": item.get("status")}
            for item in report.get("checks", ())
            if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("status"), str)
        ]
        return EngineeringBackendOutcome(
            EngineeringBackendStatus.SUCCEEDED,
            {
                "status": "ok" if report.get("readiness", {}).get("offline_ready") is True else "failed",
                "offline": True,
                "write_report": False,
                "checks": checks,
            },
            (),
            False,
            False,
        )


def project_health(report: Mapping[str, object]) -> list[dict[str, str]]:
    checks = report.get("checks", ())
    if not isinstance(checks, (list, tuple)):
        return []
    result: list[dict[str, str]] = []
    for item in checks[:64]:
        if isinstance(item, Mapping) and isinstance(item.get("id"), str) and isinstance(item.get("status"), str):
            result.append({"id": str(item["id"])[:512], "status": str(item["status"])[:512]})
    return result


__all__ = ["HealthBackend", "project_health"]
