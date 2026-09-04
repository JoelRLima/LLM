from __future__ import annotations

import pytest

from agent.runtime.task_directives import (
    MAX_STRING_LENGTH,
    DeliberationProfile,
    TaskDirective,
    TaskRunDirective,
)


def test_closed_directive_and_profile_vocabularies() -> None:
    assert {item.value for item in TaskDirective} == {"auto", "read", "plan", "do"}
    assert {item.value for item in DeliberationProfile} == {"economy", "normal", "smart", "cautious"}


def test_canonical_objective_is_owned_by_task_run_directive() -> None:
    subject = "Refactor the parser"
    directive = TaskRunDirective(TaskDirective.PLAN, DeliberationProfile.SMART, subject)

    assert directive.canonical_objective() == (
        "Propose a validated execution plan for the following objective; "
        "do not apply or execute the proposed changes. Subject: Refactor the parser"
    )
    assert directive.canonical_objective().count(subject) == 1
    assert directive.capability_ceiling() is None


def test_read_ceiling_is_exact_and_does_not_grant_effects() -> None:
    directive = TaskRunDirective(TaskDirective.READ, DeliberationProfile.NORMAL, "Analyze repo")
    ceiling = directive.capability_ceiling()

    assert ceiling == frozenset({"read", "vcs_read", "analyze"})
    assert ceiling is not None
    assert not ceiling.intersection(
        {"write", "vcs_write", "process", "network", "memory", "validate", "package_install"}
    )


@pytest.mark.parametrize(
    ("profile", "baseline", "reasoning", "hierarchy", "trivial"),
    [
        (DeliberationProfile.ECONOMY, 512, 0, False, True),
        (DeliberationProfile.NORMAL, 512, 512, True, True),
        (DeliberationProfile.SMART, 512, 1024, True, True),
        (DeliberationProfile.CAUTIOUS, 512, 2048, True, False),
    ],
)
def test_profile_policy_facts(
    profile: DeliberationProfile,
    baseline: int,
    reasoning: int,
    hierarchy: bool,
    trivial: bool,
) -> None:
    directive = TaskRunDirective(TaskDirective.DO, profile, "Analyze repo")

    assert directive.effective_reasoning_budget(baseline) == reasoning
    assert directive.hierarchical_allowed() is hierarchy
    assert directive.trivial_shortcut_allowed() is trivial


def test_plan_always_disables_trivial_shortcut() -> None:
    directive = TaskRunDirective(TaskDirective.PLAN, DeliberationProfile.NORMAL, "Analyze repo")

    assert directive.trivial_shortcut_allowed() is False


@pytest.mark.parametrize("directive", list(TaskDirective))
@pytest.mark.parametrize("profile", list(DeliberationProfile))
def test_checkpoint_round_trip(directive: TaskDirective, profile: DeliberationProfile) -> None:
    value = TaskRunDirective(directive, profile, "Refactor the parser")

    restored = TaskRunDirective.from_checkpoint_dict(value.to_checkpoint_dict())

    assert restored == value
    assert restored.to_checkpoint_dict() == value.to_checkpoint_dict()


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "directive": "read", "deliberation_profile": "normal", "subject": "x", "extra": 1},
        {"schema_version": 1, "directive": "read", "deliberation_profile": "normal"},
        {"schema_version": True, "directive": "read", "deliberation_profile": "normal", "subject": "x"},
        {"schema_version": 1, "directive": "unknown", "deliberation_profile": "normal", "subject": "x"},
        {"schema_version": 1, "directive": "read", "deliberation_profile": "unknown", "subject": "x"},
        {"schema_version": 1, "directive": "read", "deliberation_profile": "normal", "subject": "   "},
    ],
)
def test_checkpoint_value_is_strict(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        TaskRunDirective.from_checkpoint_dict(payload)


def test_subject_bound_is_not_truncated() -> None:
    subject = "x" * MAX_STRING_LENGTH

    value = TaskRunDirective(TaskDirective.AUTO, DeliberationProfile.NORMAL, subject)

    assert value.subject == subject
    with pytest.raises(ValueError, match="TASK_DIRECTIVE_OBJECTIVE_TOO_LONG"):
        TaskRunDirective(TaskDirective.PLAN, DeliberationProfile.NORMAL, subject)
