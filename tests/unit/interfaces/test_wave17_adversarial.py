from pathlib import Path

import pytest

from scripts.run_wave17_adversarial import (
    _W17_BOUNDED_GIT_TOKENS,
    _assert_required_git_tokens,
    _scenario_group_05,
    run_campaign,
)


def test_wave17_adversarial_campaign_is_complete_and_green() -> None:
    results = run_campaign()
    assert len(results) == 64
    assert [item.scenario_id for item in results] == [f"W17-A{index:02d}" for index in range(1, 65)]
    assert all(item.passed for item in results), [item for item in results if not item.passed]


def test_wave17_a17_uses_canonical_query_git_owner_without_retired_module(tmp_path: Path) -> None:
    _scenario_group_05(tmp_path)[0]()
    source = Path("scripts/run_wave17_adversarial.py").read_text(encoding="utf-8")
    assert "query_diff.py" not in source


@pytest.mark.parametrize("token", _W17_BOUNDED_GIT_TOKENS)
def test_wave17_a17_rejects_missing_bounded_git_token(token: str) -> None:
    source = Path("agent/application_services/query_git.py").read_text(encoding="utf-8")
    broken_source = source.replace(token, "", 1)
    with pytest.raises(AssertionError):
        _assert_required_git_tokens(broken_source, _W17_BOUNDED_GIT_TOKENS)
