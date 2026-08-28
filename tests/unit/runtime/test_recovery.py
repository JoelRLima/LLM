from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError
from types import MappingProxyType, SimpleNamespace

import pytest

from agent.planning.replan_models import ReplanAction, ReplanContext
from agent.runtime.failures import FailureFact
from agent.runtime.recovery import RecoveryBudgetState, RecoveryPolicy, RecoveryScope


def _policy(**overrides: int) -> RecoveryPolicy:
    limits = {scope: 2 for scope in RecoveryScope}
    for name, value in overrides.items():
        limits[RecoveryScope(name)] = value
    return RecoveryPolicy(limits)


def test_default_policy_preserves_proven_recovery_limits() -> None:
    policy = RecoveryPolicy.default()

    assert policy.limit(RecoveryScope.STRUCTURED_RESPONSE_REPAIRS) == 1
    assert policy.limit(RecoveryScope.SEMANTIC_SELECTION_REPAIRS) == 1
    assert policy.limit(RecoveryScope.VALIDATION_REPAIRS) == 1
    assert policy.limit(RecoveryScope.HEURISTIC_REPLANS) == 2
    assert policy.limit(RecoveryScope.LLM_REPLANS) == 1
    assert policy.limit(RecoveryScope.EFFECT_CONTINUATIONS) == 1
    assert policy.limit(RecoveryScope.REASONING_CONTINUATIONS) == 3
    assert policy.aggregate_replan_cap == 2


@pytest.mark.parametrize("configured_value", (0, 3, 99))
def test_aggregate_replan_cap_ignores_new_config_key(
    configured_value: int,
) -> None:
    default = RecoveryPolicy.from_config({})
    configured = RecoveryPolicy.from_config(
        {"max_total_replans": configured_value}
    )

    assert configured.aggregate_replan_cap == 2
    assert configured == default
    budget = RecoveryBudgetState(configured)
    assert budget.try_consume(RecoveryScope.HEURISTIC_REPLANS) is True
    assert budget.try_consume(RecoveryScope.HEURISTIC_REPLANS) is True
    assert budget.try_consume(RecoveryScope.LLM_REPLANS) is False


def test_aggregate_replan_cap_is_not_a_policy_constructor_option() -> None:
    with pytest.raises(TypeError):
        RecoveryPolicy(
            {scope: 2 for scope in RecoveryScope},
            max_total_replans=3,
        )


def test_scopes_are_independent_and_consumption_is_atomic() -> None:
    budget = RecoveryBudgetState(
        _policy(heuristic_replans=1, llm_replans=1)
    )

    assert budget.try_consume(RecoveryScope.HEURISTIC_REPLANS) is True
    assert budget.try_consume(RecoveryScope.HEURISTIC_REPLANS) is False
    assert budget.try_consume(RecoveryScope.LLM_REPLANS) is True
    assert budget.used(RecoveryScope.HEURISTIC_REPLANS) == 1
    assert budget.used(RecoveryScope.LLM_REPLANS) == 1


def test_recovery_consumption_is_atomic_under_concurrent_attempts() -> None:
    budget = RecoveryBudgetState(_policy(heuristic_replans=1))

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _item: budget.try_consume(RecoveryScope.HEURISTIC_REPLANS),
                range(8),
            )
        )

    assert sum(results) == 1
    assert budget.used(RecoveryScope.HEURISTIC_REPLANS) == 1


def test_replan_fallback_uses_distinct_heuristic_and_llm_scopes(monkeypatch) -> None:
    import importlib

    replan_module = importlib.import_module("agent.planning.replan")
    budget = RecoveryBudgetState(_policy(heuristic_replans=1, llm_replans=1))
    orchestrator = SimpleNamespace(
        agent_state=SimpleNamespace(recovery_budget=budget),
    )
    context = ReplanContext(
        task="locate the missing file",
        current_step={"tool": "file_reader", "args": {"file_path": "missing.txt"}},
        failure=FailureFact.from_code("FILE_NOT_FOUND"),
    )
    monkeypatch.setattr(
        replan_module,
        "_validate_and_optimize_new_steps",
        lambda action, *_args, **_kwargs: action,
    )
    monkeypatch.setattr(
        replan_module,
        "ask_llm_for_alternative",
        lambda *_args, **_kwargs: ReplanAction(
            steps=[{"tool": "directory_lister", "args": {"path": "."}}],
            source="llm",
        ),
    )

    heuristic = replan_module.replan(context, context.failure, orchestrator)
    llm = replan_module.replan(context, context.failure, orchestrator)
    exhausted = replan_module.replan(context, context.failure, orchestrator)

    assert heuristic is not None and heuristic.source == "heuristic"
    assert llm is not None and llm.source == "llm"
    assert exhausted is None
    assert budget.snapshot()[RecoveryScope.HEURISTIC_REPLANS.value] == 1
    assert budget.snapshot()[RecoveryScope.LLM_REPLANS.value] == 1


def test_default_replan_aggregate_preserves_baseline_sequences() -> None:
    budget = RecoveryBudgetState(_policy())

    assert budget.try_consume(RecoveryScope.HEURISTIC_REPLANS) is True
    assert budget.try_consume(RecoveryScope.HEURISTIC_REPLANS) is True
    assert budget.try_consume(RecoveryScope.LLM_REPLANS) is False
    assert budget.used(RecoveryScope.LLM_REPLANS) == 0

    budget.reset()
    assert budget.try_consume(RecoveryScope.HEURISTIC_REPLANS) is True
    assert budget.try_consume(RecoveryScope.LLM_REPLANS) is True
    assert budget.snapshot()[RecoveryScope.HEURISTIC_REPLANS.value] == 1
    assert budget.snapshot()[RecoveryScope.LLM_REPLANS.value] == 1
    assert budget.try_consume(RecoveryScope.HEURISTIC_REPLANS) is False


def test_recreated_context_cannot_reset_task_owned_budget() -> None:
    budget = RecoveryBudgetState(_policy(heuristic_replans=1))
    assert budget.try_consume(RecoveryScope.HEURISTIC_REPLANS) is True

    ReplanContext(
        task="task",
        current_step={"tool": "file_reader", "args": {}},
        failure=FailureFact.unknown(),
    )
    assert budget.try_consume(RecoveryScope.HEURISTIC_REPLANS) is False


def test_policy_and_snapshot_are_immutable_or_deterministic() -> None:
    policy = RecoveryPolicy.default()
    with pytest.raises(FrozenInstanceError):
        policy.limits = {}
    with pytest.raises(TypeError):
        policy.limits[RecoveryScope.LLM_REPLANS] = 99

    budget = RecoveryBudgetState(policy)
    assert budget.snapshot() == budget.snapshot()
    assert tuple(budget.snapshot()) == tuple(scope.value for scope in RecoveryScope)
    assert "total" not in budget.snapshot()


@pytest.mark.parametrize(
    ("scope", "value"),
    [
        (RecoveryScope.LLM_REPLANS, 3),
        (RecoveryScope.REASONING_CONTINUATIONS, 4),
        (RecoveryScope.HEURISTIC_REPLANS, 3),
    ],
)
def test_projection_setter_rejects_counts_above_effective_limits(
    scope: RecoveryScope, value: int
) -> None:
    budget = RecoveryBudgetState(RecoveryPolicy.default())
    before = budget.snapshot()

    with pytest.raises(ValueError):
        budget.set_projection_used(scope, value)

    assert budget.snapshot() == before


def test_projection_setter_rejects_replan_aggregate_overflow_without_mutation() -> None:
    budget = RecoveryBudgetState(RecoveryPolicy.default())
    budget.set_projection_used(RecoveryScope.HEURISTIC_REPLANS, 2)

    with pytest.raises(ValueError, match="aggregate"):
        budget.set_projection_used(RecoveryScope.LLM_REPLANS, 1)

    assert budget.used(RecoveryScope.HEURISTIC_REPLANS) == 2
    assert budget.used(RecoveryScope.LLM_REPLANS) == 0


@pytest.mark.parametrize(
    "contracted_policy",
    (
        _policy(heuristic_replans=1),
        _policy(reasoning_continuations=1),
    ),
)
def test_policy_contraction_is_rejected_before_invalid_state_is_installed(
    contracted_policy: RecoveryPolicy,
) -> None:
    budget = RecoveryBudgetState(_policy())
    if contracted_policy.limit(RecoveryScope.HEURISTIC_REPLANS) == 1:
        budget.set_projection_used(RecoveryScope.HEURISTIC_REPLANS, 2)
    else:
        budget.set_projection_used(RecoveryScope.REASONING_CONTINUATIONS, 2)
    before = budget.snapshot()
    old_policy = budget.policy

    with pytest.raises(ValueError):
        budget.reconfigure(contracted_policy)

    assert budget.policy is old_policy
    assert budget.snapshot() == before


@pytest.mark.parametrize(
    "snapshot",
    (
        {"used": {RecoveryScope.HEURISTIC_REPLANS.value: 2, RecoveryScope.LLM_REPLANS.value: 1}},
        {"used": {RecoveryScope.LLM_REPLANS.value: 2}},
    ),
)
def test_canonical_restore_rejects_per_scope_or_aggregate_overflow(
    snapshot: dict[str, object]
) -> None:
    budget = RecoveryBudgetState(RecoveryPolicy.default())
    before = budget.snapshot()

    with pytest.raises(ValueError):
        budget.restore_snapshot(snapshot)

    assert budget.snapshot() == before


@pytest.mark.parametrize(
    "counts",
    (
        {"total": 2, "heuristic": 1, "llm": 0},
        {"total": 1, "heuristic": 2, "llm": 0},
        {"total": 3, "heuristic": 2, "llm": 1},
    ),
)
def test_legacy_replan_totals_with_surplus_or_inconsistent_history_fail_closed(
    counts: dict[str, int]
) -> None:
    budget = RecoveryBudgetState(RecoveryPolicy.default())

    with pytest.raises(ValueError):
        budget.restore_legacy_projection(
            continuation_attempts=0,
            replan_counts=counts,
            reasoning_turns_used=0,
        )

    assert budget.snapshot() == {
        scope.value: 0 for scope in RecoveryScope
    }


def test_valid_legacy_replan_checkpoint_migrates_exactly_once() -> None:
    budget = RecoveryBudgetState(RecoveryPolicy.default())

    budget.restore_legacy_projection(
        continuation_attempts=1,
        replan_counts={"total": 2, "heuristic": 1, "llm": 1},
        reasoning_turns_used=2,
    )

    expected = {scope.value: 0 for scope in RecoveryScope}
    expected.update(
        {
            RecoveryScope.EFFECT_CONTINUATIONS.value: 1,
            RecoveryScope.HEURISTIC_REPLANS.value: 1,
            RecoveryScope.LLM_REPLANS.value: 1,
            RecoveryScope.REASONING_CONTINUATIONS.value: 2,
        }
    )
    assert budget.snapshot() == expected


def test_agent_state_legacy_surplus_total_is_rejected_before_migration() -> None:
    from agent.state import AgentState

    checkpoint = AgentState().to_checkpoint_dict()
    checkpoint.pop("recovery_budget")
    checkpoint["replan_counts"] = {"total": 2, "heuristic": 1, "llm": 0}

    with pytest.raises(ValueError, match="replan total"):
        AgentState().from_checkpoint_dict(checkpoint)


@pytest.mark.parametrize(
    "snapshot",
    (
        {"used": {RecoveryScope.LLM_REPLANS.value: -1}},
        {"used": {RecoveryScope.LLM_REPLANS.value: True}},
        {"used": {"unknown_scope": 1}},
        {"used": {RecoveryScope.LLM_REPLANS.value: 2}},
    ),
)
def test_invalid_restored_counts_fail_closed(snapshot: dict[str, object]) -> None:
    budget = RecoveryBudgetState(RecoveryPolicy.default())

    with pytest.raises(ValueError):
        budget.restore_snapshot(snapshot)


def test_checkpoint_projection_restores_without_a_writable_dict_owner() -> None:
    source = RecoveryBudgetState(RecoveryPolicy.default())
    source.try_consume(RecoveryScope.EFFECT_CONTINUATIONS)
    source.try_consume(RecoveryScope.REASONING_CONTINUATIONS)

    restored = RecoveryBudgetState(RecoveryPolicy.default())
    restored.restore_snapshot(source.to_checkpoint_dict())

    assert restored.snapshot() == source.snapshot()
    assert isinstance(restored.to_checkpoint_dict()["used"], dict)


def test_serialized_budget_cannot_override_canonical_aggregate_cap() -> None:
    source = RecoveryBudgetState(RecoveryPolicy.default())
    serialized = source.__getstate__()
    assert "max_total_replans" not in serialized

    invalid_used = {scope.value: 0 for scope in RecoveryScope}
    invalid_used[RecoveryScope.HEURISTIC_REPLANS.value] = 2
    invalid_used[RecoveryScope.LLM_REPLANS.value] = 1
    restored = RecoveryBudgetState.__new__(RecoveryBudgetState)
    with pytest.raises(ValueError, match="aggregate"):
        restored.__setstate__(
            {
                **serialized,
                "max_total_replans": 3,
                "used": invalid_used,
            }
        )


def test_legacy_state_counter_projection_is_read_only() -> None:
    from agent.state import AgentState

    state = AgentState()
    state.recovery_budget.try_consume(RecoveryScope.HEURISTIC_REPLANS)

    assert isinstance(state.replan_counts, MappingProxyType)
    assert dict(state.replan_counts) == {"total": 1, "heuristic": 1, "llm": 0}
    with pytest.raises(TypeError):
        state.replan_counts["llm"] = 1


def test_agent_state_checkpoint_prefers_canonical_recovery_and_migrates_legacy() -> None:
    from agent.state import AgentState

    source = AgentState()
    for scope in (
        RecoveryScope.STRUCTURED_RESPONSE_REPAIRS,
        RecoveryScope.VALIDATION_REPAIRS,
        RecoveryScope.LLM_REPLANS,
        RecoveryScope.EFFECT_CONTINUATIONS,
    ):
        assert source.recovery_budget.try_consume(scope)
    checkpoint = source.to_checkpoint_dict()
    assert "recovery_budget" in checkpoint

    restored = AgentState()
    restored.from_checkpoint_dict(checkpoint)
    assert restored.recovery_budget.snapshot() == source.recovery_budget.snapshot()

    legacy = deepcopy(checkpoint)
    legacy.pop("recovery_budget")
    migrated = AgentState()
    migrated.from_checkpoint_dict(legacy)
    expected_legacy = source.recovery_budget.snapshot()
    expected_legacy[RecoveryScope.STRUCTURED_RESPONSE_REPAIRS.value] = 0
    expected_legacy[RecoveryScope.VALIDATION_REPAIRS.value] = 0
    assert migrated.recovery_budget.snapshot() == expected_legacy


def test_agent_state_checkpoint_rejects_recovery_legacy_conflict() -> None:
    from agent.state import AgentState

    source = AgentState()
    source.recovery_budget.try_consume(RecoveryScope.LLM_REPLANS)
    checkpoint = source.to_checkpoint_dict()
    checkpoint["replan_counts"]["llm"] = 0
    checkpoint["replan_counts"]["total"] = 0

    with pytest.raises(ValueError, match="recovery budget conflicts"):
        AgentState().from_checkpoint_dict(checkpoint)
