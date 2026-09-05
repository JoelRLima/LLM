from __future__ import annotations

from agent.interaction.profile import (
    deterministic_effort_signal,
    response_reasoning_budget,
    select_fresh_profile,
)
from agent.runtime.task_directives import DeliberationProfile, TaskDirective


def test_effort_profile_precedence_and_plain_only_matching() -> None:
    assert deterministic_effort_signal("seja breve") is DeliberationProfile.ECONOMY
    assert deterministic_effort_signal("analise de forma minuciosa") is DeliberationProfile.SMART
    assert deterministic_effort_signal("faça uma auditoria adversarial") is DeliberationProfile.CAUTIOUS
    assert deterministic_effort_signal('"faça uma auditoria adversarial"') is None
    assert deterministic_effort_signal("adversarial, seja breve") is DeliberationProfile.CAUTIOUS


def test_explicit_profile_wins_and_unexplicit_inferred_do_has_normal_floor() -> None:
    assert select_fresh_profile(
        "execute rapidamente os testes",
        directive=TaskDirective.DO,
        profile_explicit=False,
    ) is DeliberationProfile.NORMAL
    assert select_fresh_profile(
        "execute rapidamente os testes",
        directive=TaskDirective.DO,
        profile_explicit=True,
        explicit_profile=DeliberationProfile.ECONOMY,
    ) is DeliberationProfile.ECONOMY


def test_response_desired_reasoning_uses_profile_hierarchy() -> None:
    assert response_reasoning_budget(DeliberationProfile.NORMAL, 0) == 0
    assert response_reasoning_budget(DeliberationProfile.SMART, 0) == 1024
    assert response_reasoning_budget(DeliberationProfile.CAUTIOUS, 0) == 2048
    assert response_reasoning_budget(DeliberationProfile.NORMAL, 1500) == 1500
    assert response_reasoning_budget(DeliberationProfile.SMART, 1500) == 1500
    assert response_reasoning_budget(DeliberationProfile.CAUTIOUS, 1500) == 2048
