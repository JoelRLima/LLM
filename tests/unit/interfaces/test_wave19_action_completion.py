from agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY


def test_memory_completion_prefers_canonical_leaf_paths():
    assert "/memory show" in DEFAULT_CLI_ACTION_REGISTRY.completion_items("/memory s")


def test_code_completion_exposes_the_canonical_root_path():
    assert "/code" in DEFAULT_CLI_ACTION_REGISTRY.completion_items("/co")
