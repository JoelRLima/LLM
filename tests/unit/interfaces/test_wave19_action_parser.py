from agent.interfaces.cli.action_parser import parse_action


def test_memory_legacy_alias_remains_accepted():
    match = parse_action("/memoria")
    assert match is not None and match.action_id == "memory.show"


def test_code_help_remains_payload_of_the_canonical_code_action():
    match = parse_action("/code help")
    assert match is not None
    assert match.action_id == "interaction.code_submit"
    assert match.raw_payload == "help"
