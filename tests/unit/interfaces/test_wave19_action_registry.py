from agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY


def test_memory_show_preferred_path_is_canonical():
    match = DEFAULT_CLI_ACTION_REGISTRY.match("/memory show")
    assert match is not None and match.action_id == "memory.show"


def test_output_is_not_a_second_action_namespace():
    assert all("/output" not in path for path in DEFAULT_CLI_ACTION_REGISTRY.preferred_commands())
