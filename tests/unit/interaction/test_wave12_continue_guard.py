from __future__ import annotations

import pytest

from agent.interaction.continue_intent import DirectTaskResumeGuard, ResumeClassification


@pytest.mark.parametrize(
    "text",
    [
        "continue a tarefa",
        "por favor continue a tarefa anterior agora por favor",
        "pode retomar a tarefa",
        "poderia retomar a execução da tarefa agora",
        "resume the task",
        "please continue the previous task now please",
        "can you resume the previous task",
        "could you resume task execution",
    ],
)
def test_exact_resume_grammar_is_accepted(text: str) -> None:
    assert DirectTaskResumeGuard.classify(text) is ResumeClassification.DIRECT_RESUME


@pytest.mark.parametrize(
    "text",
    [
        "continue",
        "continue explaining",
        "do not resume the previous task",
        "if I said resume the task",
        'Explain "resume the task"',
        "resume the previous task; read only",
        "resume the previous task, please",
    ],
)
def test_resume_guard_rejects_ambiguous_negated_meta_and_override_forms(text: str) -> None:
    assert DirectTaskResumeGuard.classify(text) is not ResumeClassification.DIRECT_RESUME
