from agent.actions.defaults import DEFAULT_ACTION_CATALOG


def test_action_catalog_has_stable_memory_action():
    assert DEFAULT_ACTION_CATALOG.get("memory.show").action_id == "memory.show"


def test_code_action_preserves_historical_help_discovery():
    assert "/code help" in DEFAULT_ACTION_CATALOG.get("interaction.code_submit").description
