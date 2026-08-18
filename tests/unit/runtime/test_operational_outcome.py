from types import SimpleNamespace

from agent.planning.completion_observations import publish_outcome
from agent.reporting.operational_outcome import (
    normalize_terminal_status,
    project_operational_outcome,
)


def test_pristine_and_tool_only_success_are_unverified() -> None:
    assert normalize_terminal_status() == "unverified"
    assert normalize_terminal_status(last_result_status="succeeded") == "unverified"


def test_explicit_success_requires_canonical_completion() -> None:
    assert normalize_terminal_status(explicit_status="succeeded") == "unverified"
    assert normalize_terminal_status(
        explicit_status="succeeded", terminal_disposition="block"
    ) == "blocked"
    assert normalize_terminal_status(
        explicit_status="succeeded", last_result_status="permission_denied"
    ) == "permission_denied"


def test_canonical_completion_and_non_success_evidence_are_distinct() -> None:
    assert normalize_terminal_status(terminal_disposition="complete") == "succeeded"
    assert normalize_terminal_status(terminal_disposition="block") == "blocked"
    assert normalize_terminal_status(terminal_disposition="fail") == "failed"
    assert normalize_terminal_status(last_result_status="unavailable") == "unavailable"
    assert normalize_terminal_status(cancelled=True) == "cancelled"


def _state(metadata, *, executed=(), waived=(), pending=(), terminal="complete"):
    result = {
        "status": "unverified",
        "executed": True,
        "invocation_id": "write-1",
        "data": {"artifacts": [{"metadata": metadata}]},
    }
    return SimpleNamespace(
        terminal_disposition=terminal,
        requested_effects=["write"],
        executed_effects=list(executed),
        waived_effects=list(waived),
        pending_effects=lambda: tuple(pending),
        last_result=result,
        tool_history=[
            {"tool": "code_task", "invocation_id": "write-1", "result": result}
        ],
    )


def test_operational_outcome_projects_persisted_write() -> None:
    outcome = project_operational_outcome(
        _state(
            {
                "applied": True,
                "mutation_occurred": True,
                "rollback_occurred": False,
                "final_state": "applied",
                "validation": "unavailable",
                "affected_files": ["controle.txt"],
            },
            executed=("write",),
        )
    )

    assert outcome.mutation_occurred is True
    assert outcome.executed_effects == ("write",)
    assert outcome.files_affected == ("controle.txt",)
    assert outcome.validation_status == "unavailable"


def test_operational_outcome_distinguishes_noop_from_transient_rolled_back_mutation() -> None:
    noop = project_operational_outcome(
        _state(
            {
                "applied": True,
                "mutation_occurred": False,
                "final_state": "applied",
                "affected_files": ["controle.txt"],
            },
            pending=("write",),
            terminal="block",
        )
    )
    rolled_back = project_operational_outcome(
        _state(
            {
                "applied": True,
                "mutation_occurred": True,
                "rollback_occurred": True,
                "final_state": "restored",
                "affected_files": ["controle.txt"],
            },
            terminal="fail",
        )
    )

    assert noop.mutation_occurred is False
    assert noop.files_affected == ()
    assert noop.pending_effects == ("write",)
    assert rolled_back.mutation_occurred is True
    assert rolled_back.rollback_occurred is True
    assert rolled_back.files_affected == ("controle.txt",)
    assert rolled_back.executed_effects == ()


def test_task_outcome_duplicate_is_suppressed_across_intervening_event() -> None:
    state = _state({"applied": False}, terminal="block")
    state.events = []
    orchestrator = SimpleNamespace(
        agent_state=state,
        _task_failed=False,
        _cancelled=False,
        _emit=lambda event_type, data=None: state.events.append(
            {"type": event_type, "data": data or {}}
        ),
    )

    publish_outcome(orchestrator)
    state.events.append({"type": "unrelated", "data": {}})
    publish_outcome(orchestrator)

    assert [event["type"] for event in state.events].count("task_outcome") == 1
