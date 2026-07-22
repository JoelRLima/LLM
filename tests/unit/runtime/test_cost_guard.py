
from agent.cost_guard import DEFAULT_MAX_TASK_STEPS, DEFAULT_MAX_TASK_TOKENS, DEFAULT_MAX_TASK_TOOL_CALLS, CostGuard


def test_default_limits_coming_from_config_constants() -> None:
    assert DEFAULT_MAX_TASK_STEPS == 30
    assert DEFAULT_MAX_TASK_TOKENS == 200000
    assert DEFAULT_MAX_TASK_TOOL_CALLS == 60


def test_check_limits_uses_config_values() -> None:
    config = {
        "max_task_steps": 5,
        "max_task_tokens": 100,
        "max_task_tool_calls": 2,
    }

    assert CostGuard.check_limits(1, [], 0, config) is False
    assert CostGuard.check_limits(6, [], 0, config) is True
    assert CostGuard.check_limits(1, [], 101, config) is True
    assert CostGuard.check_limits(1, [{"tool": "x"}, {"tool": "y"}, {"tool": "z"}], 0, config) is True


def test_check_limits_uses_defaults_if_config_missing() -> None:
    config = {}
    assert CostGuard.check_limits(31, [], 0, config) is True
    assert CostGuard.check_limits(1, [], 250001, config) is True
    assert CostGuard.check_limits(1, [{}] * 61, 0, config) is True


def test_build_limit_reached_event_contains_expected_fields() -> None:
    config = {
        "max_task_steps": 10,
        "max_task_tokens": 1234,
        "max_task_tool_calls": 4,
    }
    event = CostGuard.build_limit_reached_event(11, [{"tool": "a"}], 100, config)
    assert event["reason"].startswith("Limite de custo")
    assert event["max_steps"] == 10
    assert event["max_tokens"] == 1234
    assert event["max_tool_calls"] == 4
