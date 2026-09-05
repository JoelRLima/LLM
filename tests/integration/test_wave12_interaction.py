from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.interaction.admission import admit_interaction
from agent.interaction.service import InteractionService
from agent.interaction.types import ActionGrounding, InteractionAction, InteractionBoundary, InteractionModelDecision
from agent.runtime.task_directives import TaskDirective
from tests.unit.interaction._helpers import application, decision


def _candidate(**overrides: object) -> InteractionModelDecision:
    fields: dict[str, object] = {
        "action": "respond",
        "directive": None,
        "ambiguity": "none",
        "grounding": "none",
        "operation_requested": False,
        "proposal_only": False,
        "resume_requested": False,
        "evidence": "",
    }
    fields.update(overrides)
    return InteractionModelDecision(**fields)  # type: ignore[arg-type]


def test_flow_conversation_is_not_a_task_and_commits_one_pair() -> None:
    app = application([decision(), "AST answer"])
    result = InteractionService(app).interact("What is an AST?")
    assert result.success is True
    assert result.resolution is not None
    assert result.resolution.action is InteractionAction.RESPOND
    assert app.session.messages[-2:] == [
        {"role": "user", "content": "What is an AST?"},
        {"role": "assistant", "content": "AST answer"},
    ]


def test_flow_natural_read_is_admitted_as_w11_read() -> None:
    subject = "Analyze parser.py"
    result = admit_interaction(
        boundary=InteractionBoundary.NATURAL,
        visible_user_text=subject,
        subject=subject,
        model_decision=_candidate(
            action=InteractionAction.RUN,
            directive=TaskDirective.READ,
            grounding=ActionGrounding.CURRENT_TURN,
            evidence=subject,
        ),
    )
    assert result.action is InteractionAction.RUN
    assert result.directive is TaskDirective.READ


@pytest.mark.parametrize("subject", ["Write a short summary", "Delete parser.py from the explanation"])
def test_flow_false_do_stays_clarify(subject: str) -> None:
    result = admit_interaction(
        boundary=InteractionBoundary.NATURAL,
        visible_user_text=subject,
        subject=subject,
        model_decision=_candidate(
            action=InteractionAction.RUN,
            directive=TaskDirective.DO,
            grounding=ActionGrounding.CURRENT_TURN,
            operation_requested=True,
            evidence=subject,
        ),
    )
    assert result.action is InteractionAction.CLARIFY
    assert result.reason_code == "INTERACTION_EFFECT_AMBIGUOUS"


def test_flow_task_dispatch_restores_compressed_visible_history() -> None:
    app = application([])

    def run(*_args, **_kwargs):
        app.session.messages = [{"role": "system", "content": "compressed"}]
        return SimpleNamespace(status="succeeded", success=True, answer="done", error=None)

    app.run = run
    result = InteractionService(app).interact(
        "/read Analyze parser.py",
        boundary=InteractionBoundary.TASK,
        visible_user_text="/agent /read Analyze parser.py",
        task_payload="/read Analyze parser.py",
    )
    assert result.success is True
    assert app.session.messages == [
        {"role": "system", "content": "test system"},
        {"role": "user", "content": "/agent /read Analyze parser.py"},
        {"role": "assistant", "content": "done"},
    ]
