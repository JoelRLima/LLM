from types import SimpleNamespace

from agent.reporting.operational_outcome import project_operational_outcome


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
