
from agent.cost_guard import CostGuard
from agent.runtime.budget import TaskBudgetLedger
from agent.runtime.limits import default_runtime_limit


def test_default_limits_coming_from_config_constants() -> None:
    assert default_runtime_limit("max_steps") == 30
    assert default_runtime_limit("max_task_tokens") == 200000
    assert default_runtime_limit("max_task_tool_calls") == 60


def test_check_limits_uses_config_values() -> None:
    config = {
        "max_task_steps": 5,
        "max_task_tokens": 100,
        "max_task_tool_calls": 2,
    }

    ledger = TaskBudgetLedger(
        max_model_calls=20,
        max_task_tool_calls=2,
        max_task_tokens=100,
    )

    assert CostGuard.check_limits(1, [], 0, config, ledger) is False
    ledger.reserve_tool_call()
    ledger.reserve_tool_call()
    assert CostGuard.check_limits(1, [], 0, config, ledger) is True
    assert CostGuard.check_limits(6, [], 0, config, ledger) is True

    token_ledger = TaskBudgetLedger(max_task_tokens=100)
    call_number = token_ledger.reserve_model_call()
    token_ledger.finalize_model_call(call_number, estimated_tokens=101)
    assert CostGuard.check_limits(1, [], 0, config, token_ledger) is True


def test_check_limits_uses_defaults_if_config_missing() -> None:
    config = {}
    ledger = TaskBudgetLedger()
    assert CostGuard.check_limits(31, [], 0, config, ledger) is True
    call_number = ledger.reserve_model_call()
    ledger.finalize_model_call(call_number, estimated_tokens=250001)
    assert CostGuard.check_limits(1, [], 0, config, ledger) is True
    tool_ledger = TaskBudgetLedger()
    for _ in range(default_runtime_limit("max_task_tool_calls")):
        tool_ledger.reserve_tool_call()
    assert CostGuard.check_limits(1, [], 0, config, tool_ledger) is True


def test_build_limit_reached_event_contains_expected_fields() -> None:
    config = {
        "max_task_steps": 10,
        "max_task_tokens": 1234,
        "max_task_tool_calls": 4,
    }
    ledger = TaskBudgetLedger(
        max_model_calls=20,
        max_task_tool_calls=4,
        max_task_tokens=1234,
    )
    ledger.reserve_tool_call()
    event = CostGuard.build_limit_reached_event(11, [], 0, config, ledger)
    assert event["reason"].startswith("Limite de custo")
    assert event["max_steps"] == 10
    assert event["max_tokens"] == 1234
    assert event["max_model_calls"] == 20
    assert event["max_tool_calls"] == 4
    assert event["tool_calls"] == 1
    assert event["reported_tokens"] == 0
    assert event["estimated_tokens"] == 0


def test_cost_guard_ignores_history_and_context_estimates() -> None:
    config = {"max_task_steps": 10, "max_task_tokens": 100, "max_task_tool_calls": 3}
    ledger = TaskBudgetLedger(max_task_tool_calls=3, max_task_tokens=100)

    assert CostGuard.check_limits(1, [{}] * 100, 1000000, config, ledger) is False
    ledger.reserve_tool_call()
    ledger.reserve_tool_call()
    ledger.reserve_tool_call()
    assert CostGuard.check_limits(1, [], 0, config, ledger) is True
