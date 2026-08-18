import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.runtime.budget import BudgetExhausted, TaskBudgetLedger


def test_model_reservation_is_atomic_and_bounded_under_concurrency() -> None:
    ledger = TaskBudgetLedger(max_model_calls=4)

    def reserve() -> int | None:
        try:
            return ledger.reserve_model_call()
        except BudgetExhausted:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        reservations = list(pool.map(lambda _item: reserve(), range(20)))

    assert sorted(number for number in reservations if number is not None) == [1, 2, 3, 4]
    assert reservations.count(None) == 16
    assert ledger.snapshot().model_calls == 4


def test_tool_reservation_refuses_n_plus_one_without_incrementing() -> None:
    ledger = TaskBudgetLedger(max_task_tool_calls=1)

    assert ledger.reserve_tool_call() == 1
    with pytest.raises(BudgetExhausted) as caught:
        ledger.reserve_tool_call()

    assert caught.value.resource == "tool_calls"
    assert ledger.snapshot().tool_calls == 1


def test_token_exhaustion_blocks_new_model_and_tool_reservations() -> None:
    ledger = TaskBudgetLedger(max_task_tokens=5)
    call_number = ledger.reserve_model_call()
    ledger.finalize_model_call(call_number, estimated_tokens=5)

    with pytest.raises(BudgetExhausted) as model_error:
        ledger.reserve_model_call()
    with pytest.raises(BudgetExhausted) as tool_error:
        ledger.reserve_tool_call()

    assert model_error.value.resource == "task_tokens"
    assert tool_error.value.resource == "task_tokens"
    assert ledger.snapshot().model_calls == 1
    assert ledger.snapshot().tool_calls == 0


def test_usage_snapshot_separates_reported_and_estimated_accounting() -> None:
    ledger = TaskBudgetLedger()

    complete = ledger.reserve_model_call()
    ledger.finalize_model_call(
        complete,
        usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 0},
        estimated_tokens=99,
    )
    normalized = ledger.reserve_model_call()
    ledger.finalize_model_call(
        normalized,
        usage={"input_tokens": 4, "output_tokens": 5},
        estimated_tokens=99,
    )
    estimated = ledger.reserve_model_call()
    ledger.finalize_model_call(estimated, estimated_tokens=7)

    snapshot = ledger.snapshot()
    assert snapshot.reported_input_tokens == 6
    assert snapshot.reported_output_tokens == 8
    assert snapshot.reported_total_tokens == 0
    assert snapshot.estimated_tokens == 7
    assert snapshot.accounted_tokens == 16
    assert snapshot.model_calls_with_reported_usage == 2
    assert snapshot.model_calls_without_reported_usage == 1
    assert snapshot.token_usage_complete is False
    assert json.loads(json.dumps(snapshot.to_dict()))["accounted_tokens"] == 16


def test_independent_ledgers_do_not_share_usage() -> None:
    first = TaskBudgetLedger(max_model_calls=1)
    second = TaskBudgetLedger(max_model_calls=1)

    assert first.reserve_model_call() == 1
    assert second.reserve_model_call() == 1
    assert first.snapshot().model_calls == 1
    assert second.snapshot().model_calls == 1


def test_reset_clears_all_task_usage_without_replacing_ledger() -> None:
    ledger = TaskBudgetLedger()
    call_number = ledger.reserve_model_call()
    ledger.finalize_model_call(
        call_number,
        usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
    )
    ledger.reserve_tool_call()

    ledger.reset()

    snapshot = ledger.snapshot()
    assert snapshot.model_calls == 0
    assert snapshot.tool_calls == 0
    assert snapshot.reported_input_tokens == 0
    assert snapshot.reported_output_tokens == 0
    assert snapshot.reported_total_tokens == 0
    assert snapshot.estimated_tokens == 0
    assert snapshot.accounted_tokens == 0
    assert snapshot.model_calls_with_reported_usage == 0
    assert snapshot.model_calls_without_reported_usage == 0
    assert snapshot.token_usage_complete is True


def test_checkpoint_snapshot_restores_consumed_task_usage() -> None:
    source = TaskBudgetLedger(max_model_calls=3, max_task_tool_calls=3)
    call_number = source.reserve_model_call()
    source.finalize_model_call(
        call_number,
        usage={"input_tokens": 2, "output_tokens": 3},
    )
    source.reserve_tool_call()
    checkpoint = source.snapshot().to_dict()

    restored = TaskBudgetLedger(max_model_calls=3, max_task_tool_calls=3)
    restored.restore_snapshot(checkpoint)

    assert restored.snapshot() == source.snapshot()
    assert restored.reserve_model_call() == 2


def test_cumulative_accounting_can_cross_token_limit_then_stops_next_call() -> None:
    ledger = TaskBudgetLedger(max_task_tokens=100)

    for estimate in (40, 35, 30):
        call_number = ledger.reserve_model_call()
        ledger.finalize_model_call(call_number, estimated_tokens=estimate)

    assert ledger.snapshot().accounted_tokens == 105
    with pytest.raises(BudgetExhausted):
        ledger.reserve_model_call()
