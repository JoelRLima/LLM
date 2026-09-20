from agent.interfaces.cli.action_parser import parse_action


def test_memory_preferred_and_legacy_paths_share_identity():
    assert parse_action("/memory show").action_id == parse_action("/memory").action_id
