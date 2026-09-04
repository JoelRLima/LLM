from __future__ import annotations

import pytest

from agent.interfaces.task_directives import (
    TASK_CONTINUE_ARGUMENTS_NOT_ALLOWED,
    TASK_DIRECTIVE_CONFLICT,
    TASK_DIRECTIVE_OBJECTIVE_REQUIRED,
    TASK_DIRECTIVE_OBJECTIVE_TOO_LONG,
    TASK_DIRECTIVE_UNKNOWN_PREFIX_TOKEN,
    TASK_PROFILE_CONFLICT,
    ParsedTaskRequest,
    TaskDirectiveParseError,
    TaskRequestAction,
    parse_task_request,
)
from agent.runtime.task_directives import DeliberationProfile, TaskDirective


def test_default_request_is_auto_normal_with_exact_subject() -> None:
    parsed = parse_task_request("  Analyze  repo  ")

    assert parsed.action is TaskRequestAction.RUN
    assert parsed.directive is not None
    assert parsed.directive.directive is TaskDirective.AUTO
    assert parsed.directive.deliberation_profile is DeliberationProfile.NORMAL
    assert parsed.directive.subject == "Analyze  repo"
    assert isinstance(parsed, ParsedTaskRequest)


@pytest.mark.parametrize(
    ("raw", "directive", "profile", "subject"),
    [
        ("/read /smart Analyze repo", TaskDirective.READ, DeliberationProfile.SMART, "Analyze repo"),
        ("/cautious /plan Refactor parser", TaskDirective.PLAN, DeliberationProfile.CAUTIOUS, "Refactor parser"),
        ("/economy Analyze repo", TaskDirective.AUTO, DeliberationProfile.ECONOMY, "Analyze repo"),
    ],
)
def test_prefix_composition(
    raw: str,
    directive: TaskDirective,
    profile: DeliberationProfile,
    subject: str,
) -> None:
    parsed = parse_task_request(raw)

    assert parsed.directive is not None
    assert parsed.directive.directive is directive
    assert parsed.directive.deliberation_profile is profile
    assert parsed.directive.subject == subject


def test_prefix_order_is_commutative_for_one_directive_and_profile() -> None:
    left = parse_task_request("/read /smart Analyze repo").directive
    right = parse_task_request("/smart /read Analyze repo").directive

    assert left == right


@pytest.mark.parametrize("raw", ["/read /do Analyze repo", "/read /read Analyze repo", "/plan /do Analyze repo"])
def test_multiple_directives_fail(raw: str) -> None:
    with pytest.raises(TaskDirectiveParseError) as error:
        parse_task_request(raw)

    assert error.value.reason_code == TASK_DIRECTIVE_CONFLICT


@pytest.mark.parametrize("raw", ["/smart /cautious Analyze repo", "/smart /smart Analyze repo"])
def test_multiple_profiles_fail(raw: str) -> None:
    with pytest.raises(TaskDirectiveParseError) as error:
        parse_task_request(raw)

    assert error.value.reason_code == TASK_PROFILE_CONFLICT


def test_unknown_leading_prefix_token_fails() -> None:
    with pytest.raises(TaskDirectiveParseError) as error:
        parse_task_request("/read /turbo Analyze repo")

    assert error.value.reason_code == TASK_DIRECTIVE_UNKNOWN_PREFIX_TOKEN


def test_unknown_first_token_preserves_baseline_compatibility() -> None:
    parsed = parse_task_request("/custom /read Analyze repo")

    assert parsed.directive is not None
    assert parsed.directive.directive is TaskDirective.AUTO
    assert parsed.directive.deliberation_profile is DeliberationProfile.NORMAL
    assert parsed.directive.subject == "/custom /read Analyze repo"


def test_later_slash_text_is_subject_text() -> None:
    parsed = parse_task_request("/read Explain /custom here")

    assert parsed.directive is not None
    assert parsed.directive.subject == "Explain /custom here"


def test_continue_is_an_entry_action_without_running_directive() -> None:
    parsed = parse_task_request(" /CONTINUE ")

    assert parsed.action is TaskRequestAction.CONTINUE
    assert parsed.directive is None


@pytest.mark.parametrize(
    "raw",
    ["/continue /smart", "/continue anything", "/smart /continue", "/continue /read", "/read /continue"],
)
def test_continue_cannot_carry_controls_or_subject(raw: str) -> None:
    with pytest.raises(TaskDirectiveParseError) as error:
        parse_task_request(raw)

    assert error.value.reason_code in {TASK_DIRECTIVE_CONFLICT, TASK_CONTINUE_ARGUMENTS_NOT_ALLOWED}


@pytest.mark.parametrize("raw", ["", "   ", "/read", "/smart /plan"])
def test_objective_is_required(raw: str) -> None:
    with pytest.raises(TaskDirectiveParseError) as error:
        parse_task_request(raw)

    assert error.value.reason_code == TASK_DIRECTIVE_OBJECTIVE_REQUIRED


def test_plan_prefix_owns_deterministic_canonicalization() -> None:
    parsed = parse_task_request("/plan Refactor parser")

    assert parsed.directive is not None
    assert parsed.directive.canonical_objective().endswith("Subject: Refactor parser")


def test_parse_rejects_overlong_subject_before_run_value() -> None:
    with pytest.raises(TaskDirectiveParseError) as error:
        parse_task_request("/do " + ("x" * 8193))

    assert error.value.reason_code == TASK_DIRECTIVE_OBJECTIVE_TOO_LONG
