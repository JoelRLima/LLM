from __future__ import annotations

import pytest

from agent.interaction.model_contract import parse_interaction_resolution


def test_surrogate_scalar_is_rejected_before_typed_projection() -> None:
    with pytest.raises(ValueError):
        parse_interaction_resolution(r'{"action":"respond","directive":"none","ambiguity":"none","grounding":"none","operation_requested":false,"proposal_only":false,"resume_requested":false,"evidence":"\udfff"}')
