from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.llm.context_pressure import (
    ContextPressureDecision,
    decide_context_pressure,
)
from agent.llm.context_projection import (
    REQUIRED_EVIDENCE,
    context_record_from_text,
)
from agent.llm.contracts import ModelMessage, ModelRequest
from agent.planning.execution_frontier import build_execution_frontier
from agent.planning.observation_receipts import (
    ObservationClassification,
    build_observation_receipt,
    classify_observation,
)
from agent.planning.progress_receipt import (
    ProgressReceiptV1,
    build_progress_receipt,
    compare_progress_receipts,
)
from agent.runtime.convergence import (
    ConvergenceAccountingContext,
    ConvergenceStateV1,
)
from agent.runtime.limits import runtime_limit_values
from agent.runtime.request_measurement import (
    PROVIDER_CHAT_INPUT_TOKENS,
    RequestInputMeasurement,
)


def _receipt(
    credits: tuple[str, ...] = (),
    *,
    pending: tuple[str, ...] = (),
    running: tuple[str, ...] = (),
    completed: tuple[str, ...] = (),
    state_id: str | None = None,
) -> ProgressReceiptV1:
    return ProgressReceiptV1(
        current_state_id=state_id or "state:" + ":".join(credits or ("empty",)),
        credit_fact_ids=credits,
        credit_fact_projection=credits[:1],
        pending_unit_ids=pending,
        running_unit_ids=running,
        completed_unit_ids=completed,
    )


def _request(required: str | None = None, optional: str | None = None) -> ModelRequest:
    messages = [ModelMessage("system", "system"), ModelMessage("user", "current")]
    if required:
        messages.insert(1, ModelMessage("user", required))
    if optional:
        messages.insert(-1, ModelMessage("user", optional))
    return ModelRequest(
        messages=tuple(messages),
        model="test",
        temperature=0.0,
        max_output_tokens=128,
        context_limit=8192,
    )


class _ExactGateway:
    capabilities = SimpleNamespace(token_counting=False)

    def __init__(self, tokens: int) -> None:
        self.tokens = tokens
        self.calls = 0

    def measure_request_input_tokens(self, request: ModelRequest) -> RequestInputMeasurement:
        del request
        self.calls += 1
        return RequestInputMeasurement(
            self.tokens,
            PROVIDER_CHAT_INPUT_TOKENS,
            exact=True,
            available=True,
        )


class _InexactGateway:
    capabilities = SimpleNamespace(token_counting=False)

    def measure_request_input_tokens(self, request: ModelRequest) -> None:
        del request
        return None


def test_receipt_delta_uses_complete_credit_set_not_bounded_projection() -> None:
    before = _receipt(("fact:a", "fact:b"))
    after = ProgressReceiptV1(
        current_state_id="same-shape",
        credit_fact_ids=("fact:a", "fact:b", "fact:omitted-from-view"),
        credit_fact_projection=("fact:a",),
    )

    delta = compare_progress_receipts(before, after)

    assert delta.raw_new_credit_fact_ids == ("fact:omitted-from-view",)
    assert after.credit_fact_projection == ("fact:a",)
    assert after.aggregate_progress_id == "progress:" + __import__(
        "hashlib"
    ).sha256(b'["fact:a","fact:b","fact:omitted-from-view"]').hexdigest()


def test_reactive_fresh_step_id_is_not_progress_by_itself() -> None:
    owner = ConvergenceStateV1(6)
    before = _receipt((), pending=())
    after = _receipt(
        ("step_terminal:fresh-reactive-id",),
        completed=("fresh-reactive-id",),
    )
    owner.bootstrap(before)

    observation = owner.observe(
        before,
        after,
        ConvergenceAccountingContext.root_attempt("root", "reactive_iteration"),
    )

    assert observation.advanced is False
    assert observation.cycle_charged is True
    assert owner.cycles_since_progress == 1


def test_convergence_is_monotonic_across_loss_and_cross_epoch_restoration() -> None:
    owner = ConvergenceStateV1(6)
    root = ConvergenceAccountingContext.root_attempt("root")
    first = _receipt(("fact:a",))
    owner.bootstrap(first)

    advanced_b = owner.observe(first, _receipt(("fact:a", "fact:b")), root)
    advanced_c = owner.observe(
        _receipt(("fact:a", "fact:b")),
        _receipt(("fact:a", "fact:c")),
        root,
    )
    epoch = owner.plateau_epoch_id
    lost_b = owner.observe(_receipt(("fact:a", "fact:c")), _receipt(("fact:a", "fact:b")), root)
    restored_c = owner.observe(_receipt(("fact:a", "fact:b")), _receipt(("fact:a", "fact:c")), root)

    assert advanced_b.advanced and advanced_c.advanced
    assert lost_b.advanced is False
    assert restored_c.advanced is False
    assert owner.credited_fact_ids_seen == ("fact:a", "fact:b", "fact:c")
    assert owner.plateau_epoch_id == epoch
    assert owner.cycles_since_progress == 2


def test_stage_boundaries_are_one_shot_and_terminal() -> None:
    owner = ConvergenceStateV1(6)
    root = ConvergenceAccountingContext.root_attempt("root")
    before = _receipt((), state_id="before")
    observations = []
    for index in range(1, 7):
        after = _receipt((), state_id=f"after-{index}")
        observation = owner.observe(before, after, root)
        observations.append(observation)
        before = after

    assert observations[1].should_refresh is True
    assert observations[3].should_replan is True
    assert observations[5].terminal is True
    assert owner.refresh_performed_in_epoch is True
    assert owner.replan_performed_in_epoch is True
    assert owner.stage == "plateau_terminal"


def test_delegated_and_zero_io_cache_attempts_do_not_charge_root_cycles() -> None:
    owner = ConvergenceStateV1(6)
    before = _receipt((), state_id="before")
    after = _receipt((), state_id="after")
    owner.bootstrap(before)

    delegated = owner.observe(
        before,
        after,
        ConvergenceAccountingContext.delegated_attempt("root", "inner_plan"),
    )
    reused = owner.observe(
        after,
        _receipt((), state_id="after-cache"),
        ConvergenceAccountingContext.cache_reuse_attempt("root"),
    )

    assert delegated.cycle_charged is False
    assert reused.cycle_charged is False
    assert owner.cycles_since_progress == 0


def test_frontier_is_frozen_bounded_and_redacted() -> None:
    state = SimpleNamespace(
        root_task_id="root",
        plan_identity="plan-1",
        current_step_id="step-0",
        plan=[{"step_id": f"step-{index}", "tool": "reader"} for index in range(20)],
        step_records={},
        tool_history=[],
        task_semantics=None,
        budget_ledger=None,
        recovery_budget=None,
        convergence=None,
    )
    observations = [
        {
            "source_identity": f"src-{index}",
            "source_hash": "hash",
            "source_extent": {"kind": "whole"},
            "provenance": "exact_source",
            "freshness": "CURRENT",
            "complete": True,
            "truncated": False,
            "content": "must not escape",
            "prompt": "must not escape",
            "credentials": "must not escape",
        }
        for index in range(30)
    ]

    frontier = build_execution_frontier(state, observations=observations)
    projected = frontier.to_dict()

    assert frontier.next_units.truncated is True
    assert frontier.next_units.complete is False
    assert frontier.next_units.total_count == 20
    assert frontier.next_units.omitted_count == 4
    assert frontier.observations.truncated is True
    assert frontier.observations.total_count == 30
    assert frontier.observations.omitted_count == 6
    assert "content" not in str(projected)
    assert "credentials" not in str(projected)
    with pytest.raises(TypeError):
        frontier.validation["status"] = "passed"  # type: ignore[index]


def test_context_pressure_exact_full_compact_and_mandatory_overflow() -> None:
    required = context_record_from_text(
        "runtime:frontier",
        "execution_frontier",
        "frontier",
        necessity=REQUIRED_EVIDENCE,
    )
    optional = context_record_from_text("memory:history", "memory", "history")

    full = decide_context_pressure(
        mandatory_request=_request(),
        required_records=(required,),
        optional_records=(optional,),
        context_limit=4096,
        gateway=_ExactGateway(20),
        build_request=_request,
    )
    compact = decide_context_pressure(
        mandatory_request=_request(),
        required_records=(required,),
        optional_records=(optional,),
        context_limit=660,
        gateway=_ExactGateway(20),
        build_request=_request,
    )
    overflow = decide_context_pressure(
        mandatory_request=_request(),
        required_records=(required,),
        context_limit=600,
        gateway=_ExactGateway(200),
        build_request=_request,
    )

    assert full.decision is ContextPressureDecision.FULL
    assert full.fit_proven is True
    assert compact.decision is ContextPressureDecision.COMPACT
    assert compact.dropped_source_kinds == ("memory",)
    assert overflow.decision is ContextPressureDecision.MANDATORY_OVERFLOW
    assert overflow.dispatch_allowed is False


def test_inexact_context_measurement_drops_optional_without_claiming_fit() -> None:
    optional = context_record_from_text("memory:history", "memory", "history")
    result = decide_context_pressure(
        mandatory_request=_request(),
        optional_records=(optional,),
        context_limit=4096,
        gateway=_InexactGateway(),
        build_request=_request,
    )

    assert result.decision is ContextPressureDecision.COMPACT
    assert result.fit_proven is False
    assert result.projection.optional_auxiliary_message is None
    assert result.dispatch_allowed is True


def test_observation_classification_distinguishes_reuse_rehydration_stale_and_summary() -> None:
    exact_data = {
        "source_identity": "src.py",
        "source_hash": "hash-a",
        "source_extent": {"kind": "whole"},
        "evidence_provenance": "EXACT_SOURCE",
        "complete": True,
        "truncated": False,
    }
    prior = build_observation_receipt(exact_data)
    current = build_observation_receipt(exact_data)

    assert classify_observation(prior, current) is ObservationClassification.CACHE_REUSE
    assert classify_observation(
        prior,
        current,
        exact_bytes_available=False,
        pending_need=True,
        physical_execution=True,
    ) is ObservationClassification.CONTEXT_REHYDRATION
    assert classify_observation(
        prior,
        build_observation_receipt({**exact_data, "source_hash": "hash-b"}),
        pending_need=True,
        physical_execution=True,
    ) is ObservationClassification.STALE_REREAD
    assert classify_observation(
        prior,
        build_observation_receipt(
            {
                **exact_data,
                "evidence_provenance": "DERIVED_LOSSY",
                "complete": False,
            }
        ),
        pending_need=True,
        physical_execution=True,
    ) is ObservationClassification.CONTEXT_REHYDRATION


def test_checkpoint_object_is_closed_and_preserves_monotonic_state() -> None:
    owner = ConvergenceStateV1(6)
    owner.bootstrap(_receipt(("fact:a",)))
    owner.observe(
        _receipt(("fact:a",)),
        _receipt(("fact:a",), state_id="changed"),
        ConvergenceAccountingContext.root_attempt("root"),
    )
    checkpoint = owner.to_checkpoint_dict()
    restored = ConvergenceStateV1.from_checkpoint_dict(checkpoint)

    assert set(checkpoint) == {
        "schema_version",
        "plateau_epoch_id",
        "credited_fact_ids_seen",
        "last_observed_current_state_id",
        "cycles_since_progress",
        "refresh_performed_in_epoch",
        "replan_performed_in_epoch",
    }
    assert restored.cycles_since_progress == 1
    assert restored.credited_fact_ids_seen == ("fact:a",)
    with pytest.raises(ValueError):
        ConvergenceStateV1.from_checkpoint_dict({**checkpoint, "stage": "normal"})


def test_resume_reconciliation_unions_external_credit_without_reset() -> None:
    owner = ConvergenceStateV1(6)
    owner.bootstrap(_receipt(("fact:a",)))
    for index in range(3):
        owner.observe(
            _receipt(("fact:a",), state_id=f"before-{index}"),
            _receipt(("fact:a",), state_id=f"after-{index}"),
            ConvergenceAccountingContext.root_attempt("root"),
        )
    epoch = owner.plateau_epoch_id
    reconciled = owner.reconcile_resume(_receipt(("fact:a", "fact:offline")))

    assert reconciled["classification"] == "RESUME_STATE_DIVERGED"
    assert owner.cycles_since_progress == 3
    assert owner.plateau_epoch_id == epoch
    assert "fact:offline" in owner.credited_fact_ids_seen


def test_runtime_limit_has_strict_w15_domain() -> None:
    assert runtime_limit_values({"max_no_progress_plateau": 4})[
        "max_no_progress_plateau"
    ] == 4
    for invalid in (3, 101, True, "6"):
        with pytest.raises(ValueError):
            runtime_limit_values({"max_no_progress_plateau": invalid})


def test_build_progress_receipt_requires_exact_relevant_observation_for_credit() -> None:
    summary = {
        "source_identity": "src.py",
        "source_hash": "hash-a",
        "source_extent": {"kind": "whole"},
        "provenance": "DERIVED_LOSSY",
        "complete": False,
        "truncated": False,
        "satisfies_pending_need": True,
    }
    exact = {**summary, "provenance": "EXACT_SOURCE", "complete": True}

    summary_receipt = build_progress_receipt(observations=(summary,))
    exact_receipt = build_progress_receipt(observations=(exact,))

    assert summary_receipt.credit_fact_ids == ()
    assert len(exact_receipt.credit_fact_ids) == 1
