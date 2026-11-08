from types import SimpleNamespace

import pytest

from llm_agent.agent.planning.task_completion_constraints import (
    required_validation_satisfied,
)
from llm_agent.agent.state import AgentState


def _orchestrator(*results):
    state = AgentState()
    state.tool_history = [
        {"tool": "code_task", "result": result} for result in results
    ]
    return SimpleNamespace(
        agent_state=state,
        _admitted_intent=SimpleNamespace(
            requires_validation=True,
            admitted_effects=("write",),
        ),
    )


def _mutation_result(
    *,
    status="succeeded",
    ok=True,
    validation="passed",
    final_state="applied",
    rollback=False,
    resources=("target.txt",),
):
    result = {
        "status": status,
        "executed": True,
        "data": {
            "metadata": {
                "mutation_occurred": True,
                "applied": True,
                "final_state": final_state,
                "rollback_occurred": rollback,
                "validation_status": validation,
                "affected_files": list(resources),
            }
        },
    }
    if ok is not None:
        result["ok"] = ok
    return result


def test_unvalidated_later_mutation_invalidates_validation_for_the_same_resource():
    first = _mutation_result()
    second = _mutation_result(validation=None)

    assert required_validation_satisfied(_orchestrator(first, second)) is False


def test_failed_mutation_can_be_repaired_and_validated_on_the_same_resource():
    failed = _mutation_result(status="failed", ok=False, validation=None)
    repaired = _mutation_result()

    assert required_validation_satisfied(_orchestrator(failed, repaired)) is True


@pytest.mark.parametrize(
    ("later", "expected"),
    [
        (_mutation_result(status="failed", ok=False), False),
        (_mutation_result(status="failed", ok=True), False),
        (_mutation_result(status="succeeded", ok=False), False),
        (_mutation_result(validation="failed"), False),
        (_mutation_result(final_state="restored", rollback=True), False),
        (_mutation_result(final_state="unknown", rollback=True), False),
        (_mutation_result(), True),
        (_mutation_result(ok=None), False),
    ],
)
def test_later_mutation_status_and_state_cannot_be_overridden_by_prior_validation(
    later, expected
):
    assert required_validation_satisfied(
        _orchestrator(_mutation_result(), later)
    ) is expected


def test_required_validation_remains_pending_until_a_mutation_occurs():
    assert required_validation_satisfied(_orchestrator()) is True


def test_failed_mutation_on_a_distinct_resource_remains_unresolved():
    repaired = _mutation_result(resources=("a.txt",))
    failed = _mutation_result(
        status="failed",
        ok=False,
        validation=None,
        resources=("b.txt",),
    )

    assert required_validation_satisfied(_orchestrator(repaired, failed)) is False


def test_partial_repair_leaves_unrepaired_resource_pending():
    initial = _mutation_result(
        status="failed",
        ok=False,
        validation=None,
        resources=("a.txt", "b.txt"),
    )
    repaired = _mutation_result(resources=("a.txt",))

    assert required_validation_satisfied(_orchestrator(initial, repaired)) is False


def test_unvalidated_artifact_cannot_borrow_another_artifacts_validation():
    result = {
        "ok": True,
        "status": "succeeded",
        "artifacts": [
            {
                "metadata": {
                    "mutation_occurred": True,
                    "applied": True,
                    "final_state": "applied",
                    "affected_files": ["a.txt"],
                    "validation_status": "passed",
                }
            },
            {
                "metadata": {
                    "mutation_occurred": True,
                    "applied": True,
                    "final_state": "applied",
                    "affected_files": ["b.txt"],
                }
            },
        ],
    }

    assert required_validation_satisfied(_orchestrator(result)) is False


def test_explicit_no_mutation_does_not_create_validation_obligation():
    result = {
        "ok": True,
        "status": "succeeded",
        "data": {
            "metadata": {
                "mutation_occurred": False,
                "affected_files": ["target.txt"],
            }
        },
    }

    assert required_validation_satisfied(_orchestrator(result)) is True


def test_checkpoint_history_is_canonical_over_stale_last_result():
    state = AgentState()
    state.objective = "edit and validate"
    state.tool_history = [
        {"tool": "code_task", "result": _mutation_result(validation=None)}
    ]
    state.last_result = _mutation_result()

    checkpoint = state.to_checkpoint_dict()
    restored = AgentState()
    restored.from_checkpoint_dict(checkpoint)
    orchestrator = SimpleNamespace(
        agent_state=restored,
        _admitted_intent=SimpleNamespace(
            requires_validation=True,
            admitted_effects=("write",),
        ),
    )

    assert required_validation_satisfied(orchestrator) is False


def test_legacy_last_result_supplies_evidence_only_when_history_is_empty():
    state = AgentState()
    state.last_result = _mutation_result()
    orchestrator = SimpleNamespace(
        agent_state=state,
        _admitted_intent=SimpleNamespace(
            requires_validation=True,
            admitted_effects=("write",),
        ),
    )

    assert required_validation_satisfied(orchestrator) is True

