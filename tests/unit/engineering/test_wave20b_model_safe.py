"""Model-safe Engineering adversarial cases; intentionally not run in W20-B."""

from __future__ import annotations

from types import SimpleNamespace

from agent.engineering.contracts import (
    EngineeringEffects,
    EngineeringEnvironmentV1,
    EngineeringOperationViewV1,
    EngineeringRunPhase,
    EngineeringRunResultV1,
    EngineeringScope,
    EngineeringTerminalStatus,
)
from agent.engineering.model_safe import ModelSafeEngineering, ModelSafeStatus


def _result(workspace_id: str) -> EngineeringRunResultV1:
    return EngineeringRunResultV1(
        run_id="engr-" + "a" * 32,
        operation_id="workspace.read",
        phase=EngineeringRunPhase.TERMINAL,
        status=EngineeringTerminalStatus.SUCCEEDED,
        reason_codes=(),
        started_at_utc="2026-01-01T00:00:00.000000Z",
        finished_at_utc="2026-01-01T00:00:01.000000Z",
        duration_ms=1000,
        workspace_id=workspace_id,
        persisted=True,
        summary={"status": "ok", "path": "must-not-leak"},
        references=(),
        environment=EngineeringEnvironmentV1(workspace_id, None),
    )


def test_support_workspace_mismatch_is_unknown() -> None:
    descriptor = EngineeringOperationViewV1(
        "workspace.read", EngineeringScope.WORKSPACE, EngineeringEffects(False, False, False, False, True), True, None, {}, model_safe=True
    )
    service = SimpleNamespace(describe=lambda _operation, _context: descriptor)
    store = SimpleNamespace(result_for_workspace=lambda _run_id, _context: _result("other"))
    context = SimpleNamespace(workspace=SimpleNamespace(workspace_id="trusted"))
    response = ModelSafeEngineering(service, store).engineering_result("engr-" + "a" * 32, context)
    assert response.status is ModelSafeStatus.UNKNOWN


def test_support_model_safe_projection_omits_paths_and_reference_labels() -> None:
    descriptor = EngineeringOperationViewV1(
        "workspace.read", EngineeringScope.WORKSPACE, EngineeringEffects(False, False, False, False, True), True, None, {}, model_safe=True
    )
    service = SimpleNamespace(describe=lambda _operation, _context: descriptor)
    store = SimpleNamespace(result_for_workspace=lambda _run_id, _context: _result("trusted"))
    context = SimpleNamespace(workspace=SimpleNamespace(workspace_id="trusted"))
    response = ModelSafeEngineering(service, store).engineering_result("engr-" + "a" * 32, context)
    assert response.data is not None
    assert "path" not in response.data["summary"]


def test_support_model_safe_operations_are_not_cacheable() -> None:
    assert ModelSafeEngineering(
        SimpleNamespace(list_operations=lambda _context: ()), object()
    ).engineering_list(SimpleNamespace()).cacheable is False
