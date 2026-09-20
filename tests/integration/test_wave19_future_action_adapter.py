from agent.actions.defaults import DEFAULT_ACTION_CATALOG


def test_future_adapter_can_enumerate_actions_without_cli_import():
    assert any(item.action_id == "query.read" for item in DEFAULT_ACTION_CATALOG.list())
