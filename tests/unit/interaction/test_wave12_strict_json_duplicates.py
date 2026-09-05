from __future__ import annotations

import pytest

from agent.interaction.model_contract import parse_interaction_resolution

from ._helpers import decision


@pytest.mark.parametrize("key", ["action", "directive", "evidence"])
def test_each_public_key_rejects_duplicate_occurrence(key: str) -> None:
    with pytest.raises(ValueError):
        parse_interaction_resolution(decision()[:-1] + f',"{key}":"none"}}')
