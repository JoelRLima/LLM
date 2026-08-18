from types import SimpleNamespace

import pytest

from agent.planning.task_completion import (
    allow_linear_completion,
    complete_direct_answer,
    mark_terminal_blocked,
    mark_terminal_cancelled,
)
from agent.reporting.operational_outcome import project_operational_outcome
from agent.state import AgentState


def _orchestrator(state: AgentState | None = None) -> SimpleNamespace:
    current = state or AgentState()
    return SimpleNamespace(
        agent_state=current,
        tool_registry=None,
        _task_failed=False,
        _cancelled=False,
        _emit=lambda event_type, data=None: current.events.append(
            {"type": event_type, "data": data or {}}
        ),
    )


@pytest.mark.parametrize("route", ["direct", "linear", "reactive", "hierarchical", "security"])
def test_explicit_completion_projects_succeeded_across_routes(route: str) -> None:
    orchestrator = _orchestrator()
    orchestrator.agent_state.last_result = {
        "ok": True,
        "status": "succeeded",
        "message": f"{route} evidence",
    }

    if route == "direct":
        answer = complete_direct_answer(orchestrator, "objetivo", "resposta")
    else:
        answer = allow_linear_completion(orchestrator, "objetivo")

    outcome = project_operational_outcome(orchestrator.agent_state)
    assert answer is None if route != "direct" else answer == "resposta"
    assert orchestrator.agent_state.terminal_disposition == "complete"
    assert outcome.terminal_status == "succeeded"


@pytest.mark.parametrize("route", ["linear", "security"])
def test_permission_denial_projects_permission_denied_across_routes(route: str) -> None:
    del route
    orchestrator = _orchestrator()
    orchestrator.agent_state.last_result = {
        "ok": False,
        "status": "permission_denied",
        "error_code": "AUTH_REQUIRED",
        "message": "autorizacao ausente",
    }

    answer = allow_linear_completion(orchestrator, "objetivo")

    assert answer == "autorizacao ausente"
    assert project_operational_outcome(orchestrator.agent_state).terminal_status == "permission_denied"


@pytest.mark.parametrize("route", ["linear", "reactive"])
def test_pending_requested_write_projects_blocked_across_routes(route: str) -> None:
    del route
    state = AgentState()
    state.requested_effects = ["write"]
    orchestrator = _orchestrator(state)

    answer = allow_linear_completion(orchestrator, "altere o arquivo")

    assert "permanece pendente" in answer
    assert project_operational_outcome(state).terminal_status == "blocked"


@pytest.mark.parametrize("route", ["direct", "linear", "reactive", "hierarchical", "security"])
def test_budget_exhaustion_cannot_project_succeeded_from_any_route(route: str) -> None:
    del route
    orchestrator = _orchestrator()
    orchestrator.agent_state.last_result = {
        "ok": False,
        "status": "blocked",
        "error_code": "TASK_BUDGET_EXHAUSTED",
        "message": "limite atingido",
    }

    answer = allow_linear_completion(orchestrator, "objetivo")

    assert answer == "limite atingido"
    assert project_operational_outcome(orchestrator.agent_state).terminal_status == "blocked"


def test_canonical_tool_failure_cannot_be_overwritten_by_later_prose() -> None:
    orchestrator = _orchestrator()
    orchestrator.agent_state.last_result = {
        "ok": False,
        "status": "failed",
        "error_code": "PROVIDER_FAILED",
        "error": "provedor indisponivel",
        "message": "provedor indisponivel",
    }

    answer = allow_linear_completion(orchestrator, "objetivo")

    assert answer == "provedor indisponivel"
    assert project_operational_outcome(orchestrator.agent_state).terminal_status == "failed"


def test_cancelled_state_always_projects_cancelled() -> None:
    orchestrator = _orchestrator()

    answer = mark_terminal_cancelled(orchestrator)

    assert answer == "Tarefa cancelada pelo usuario."
    assert project_operational_outcome(
        orchestrator.agent_state,
        cancelled=orchestrator._cancelled,
    ).terminal_status == "cancelled"


def test_unknown_terminal_evidence_projects_unverified() -> None:
    orchestrator = _orchestrator()

    answer = mark_terminal_blocked(
        orchestrator,
        reason_code="UNKNOWN_EXECUTABILITY",
        message="capacidade nao demonstrada",
        status="unverified",
    )

    assert answer == "capacidade nao demonstrada"
    assert project_operational_outcome(orchestrator.agent_state).terminal_status == "unverified"
