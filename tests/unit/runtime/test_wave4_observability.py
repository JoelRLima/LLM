from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from agent.application_result import AgentRunResult
from agent.reporting.metrics import project_run_metrics
from agent.reporting.run_receipt import build_run_receipt
from agent.reporting.run_snapshot import build_canonical_run_snapshot
from agent.reporting.task_report import TaskReportBuilder
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_data import bounded_event_data
from agent.runtime.event_dispatch import RuntimeEventDispatcher
from agent.runtime.events import MAX_EVENT_DATA_CHARS, RuntimeEvent, RuntimeEventKind
from agent.runtime.failures import FailureFact
from agent.state import AgentState
from agent.tools.contracts import ToolError, ToolResult, ToolStatus
from scripts.check_wave4_architecture import check_source, run_checks


def _state(last_result: object = None) -> SimpleNamespace:
    return SimpleNamespace(
        objective="wave4",
        last_result=last_result,
        last_tool="file_reader",
        current_step_id="step-1",
        tool_history=[],
        events=[],
        execution_incidents=[],
        requested_effects=(),
        executed_effects=(),
        waived_effects=(),
        pending_effects=(),
        terminal_disposition="complete" if last_result is None else "fail",
        _task_failed=False,
        _cancelled=False,
    )


def test_run_correlation_preserves_domains_across_fresh_resume_and_children() -> None:
    fresh = RunCorrelation.fresh()
    child = fresh.child("node-a")
    unrelated = fresh.unrelated_task()
    resumed = RunCorrelation.resume(fresh.root_task_id)

    assert fresh.run_id != fresh.root_task_id
    assert child.run_id == fresh.run_id
    assert child.root_task_id == fresh.root_task_id
    assert child.task_id not in {fresh.task_id, fresh.root_task_id}
    assert child.parent_task_id == fresh.task_id
    assert child.node_id == "node-a"
    assert unrelated.root_task_id != fresh.root_task_id
    assert resumed.run_id != fresh.run_id
    assert resumed.root_task_id == fresh.root_task_id
    assert resumed.task_id == fresh.root_task_id


def test_dispatcher_fans_out_the_same_immutable_event_and_observes_checkpoint_once() -> None:
    first: list[RuntimeEvent] = []
    second: list[RuntimeEvent] = []
    checkpoints: list[RuntimeEvent] = []
    correlation = RunCorrelation.fresh()
    event = RuntimeEvent.from_fields(
        RuntimeEventKind.STEP_COMPLETED,
        correlation,
        {"plan_id": "plan-1", "step_id": "step-1", "invocation_id": "inv-1"},
        plan_id="plan-1",
        step_id="step-1",
        invocation_id="inv-1",
    )
    dispatcher = RuntimeEventDispatcher(
        [first.append, second.append],
        checkpoint_observer=checkpoints.append,
    )

    dispatcher.emit(event)

    assert first == [event]
    assert second == [event]
    assert first[0] is second[0] is checkpoints[0] is event
    assert event.run_id == correlation.run_id
    assert event.root_task_id == correlation.root_task_id
    assert event.plan_id == "plan-1"
    assert event.step_id == "step-1"
    assert event.invocation_id == "inv-1"


def test_runtime_event_payload_is_bounded_and_taxonomy_is_fail_closed() -> None:
    event = RuntimeEvent.from_fields(
        "warning",
        RunCorrelation.fresh(),
        {"large": "x" * (MAX_EVENT_DATA_CHARS * 2), "unsupported": object()},
    )
    serialized = event.to_legacy_dict()

    assert len(str(serialized["data"])) <= MAX_EVENT_DATA_CHARS + 256
    assert serialized["data"]
    with pytest.raises(ValueError, match="unsupported runtime event kind"):
        RuntimeEvent.from_fields("not_a_runtime_event", RunCorrelation.fresh())


def test_bounded_event_data_redacts_sensitive_values_across_shapes() -> None:
    payload = {
        'run_id': 'run-diagnostic',
        'task_id': 'task-diagnostic',
        'plan_id': 'plan-diagnostic',
        'step_id': 'step-diagnostic',
        'invocation_id': 'invocation-diagnostic',
        'path': 'src/app.py',
        'pattern': 'H2_MARKER',
        'status': 'failed',
        'reason_code': 'TASK_AUTHORITY_DENIED',
        'token_usage_complete': True,
        'total_tokens': 7,
        'authorization': 'AUTH_TOP_SECRET',
        'api_key': 'API_UNDERSCORE_SECRET',
        'api-key': 'API_DASH_SECRET',
        'apikey': 'API_COMPACT_SECRET',
        'token': 'TOKEN_TOP_SECRET',
        'password': 'PASSWORD_TOP_SECRET',
        'secret': 'SECRET_TOP_SECRET',
        'nested': {
            'Authorization': 'Bearer AUTH_NESTED_SECRET',
            'items': [
                {'api-key': 'API_NESTED_SECRET'},
                ('apikey=INLINE_TUPLE_SECRET', 'path=src/app.py'),
            ],
        },
        'set_values': {'api_key=INLINE_SET_SECRET', 'path=src/app.py'},
        'frozen_values': frozenset({'token=INLINE_FROZEN_SECRET'}),
        'text': (
            'api_key=INLINE_API_SECRET api-key=INLINE_DASH_SECRET '
            'apikey=INLINE_COMPACT_SECRET token=INLINE_TOKEN_SECRET '
            'password=INLINE_PASSWORD_SECRET secret=INLINE_SECRET_SECRET '
            'Authorization: Bearer INLINE_AUTH_SECRET Bearer INLINE_BEARER_SECRET'
        ),
    }

    projected = bounded_event_data(payload)
    rendered = repr(projected)

    for secret in (
        'AUTH_TOP_SECRET',
        'API_UNDERSCORE_SECRET',
        'API_DASH_SECRET',
        'API_COMPACT_SECRET',
        'TOKEN_TOP_SECRET',
        'PASSWORD_TOP_SECRET',
        'SECRET_TOP_SECRET',
        'AUTH_NESTED_SECRET',
        'API_NESTED_SECRET',
        'INLINE_TUPLE_SECRET',
        'INLINE_SET_SECRET',
        'INLINE_FROZEN_SECRET',
        'INLINE_API_SECRET',
        'INLINE_DASH_SECRET',
        'INLINE_COMPACT_SECRET',
        'INLINE_TOKEN_SECRET',
        'INLINE_PASSWORD_SECRET',
        'INLINE_SECRET_SECRET',
        'INLINE_AUTH_SECRET',
        'INLINE_BEARER_SECRET',
    ):
        assert secret not in rendered

    for name, value in (
        ('run_id', 'run-diagnostic'),
        ('task_id', 'task-diagnostic'),
        ('plan_id', 'plan-diagnostic'),
        ('step_id', 'step-diagnostic'),
        ('invocation_id', 'invocation-diagnostic'),
        ('path', 'src/app.py'),
        ('pattern', 'H2_MARKER'),
        ('status', 'failed'),
        ('reason_code', 'TASK_AUTHORITY_DENIED'),
    ):
        assert projected[name] == value
    assert projected['token_usage_complete'] is True
    assert projected['total_tokens'] == 7


def test_bounded_event_data_redacts_quoted_and_json_like_secrets() -> None:
    payload = {
        'quoted_authorization': 'Authorization="Bearer AUTH_SECRET"',
        'json_api_key': '{"api_key": "API_SECRET"}',
        'json_password': '{"password": "PASSWORD_SECRET"}',
        'single_quoted_token': "token='TOKEN_SECRET'",
        'spaced_password': 'password="hunter 2"',
        'normal_diagnostics': {
            'run_id': 'run-diagnostic',
            'task_id': 'task-diagnostic',
            'path': '/tmp/normal/path',
            'pattern': '*.json',
            'status': 'failed',
            'reason_code': 'expected_failure',
        },
    }

    rendered = repr(bounded_event_data(payload))

    for secret in (
        'AUTH_SECRET',
        'API_SECRET',
        'PASSWORD_SECRET',
        'TOKEN_SECRET',
        'hunter',
        'hunter 2',
    ):
        assert secret not in rendered
    for diagnostic in (
        'run-diagnostic',
        'task-diagnostic',
        '/tmp/normal/path',
        '*.json',
        'expected_failure',
    ):
        assert diagnostic in rendered


def test_bounded_event_data_redacts_authorization_schemes_and_composite_keys() -> None:
    payload = {
        'basic': 'Authorization: Basic BASIC_SECRET',
        'access': '{"access_token":"ACCESS_SECRET"}',
        'refresh': 'refresh_token=REFRESH_SECRET',
        'client': 'client_secret=CLIENT_SECRET',
        'bearer': 'Authorization: Bearer BEARER_SECRET',
        'diagnostics': 'path=src/app.py status=failed reason_code=AUTH_DENIED',
    }

    rendered = repr(bounded_event_data(payload))

    for secret in (
        'BASIC_SECRET',
        'ACCESS_SECRET',
        'REFRESH_SECRET',
        'CLIENT_SECRET',
        'BEARER_SECRET',
    ):
        assert secret not in rendered
    assert 'Authorization: Basic [REDACTED]' in rendered
    assert 'Authorization: Bearer [REDACTED]' in rendered
    assert 'Bearer [REDACTED]]' not in rendered
    for diagnostic in ('src/app.py', 'status=failed', 'AUTH_DENIED'):
        assert diagnostic in rendered


def test_bounded_event_data_redacts_multipart_authorization_as_one_unit() -> None:
    payload = {
        'digest': (
            'Authorization: Digest username="bob", realm="prod", '
            'response="DIGEST_SECRET"'
        ),
        'aws': (
            'Authorization: AWS4-HMAC-SHA256 '
            'Credential=ACCESS_SECRET/20260830, SignedHeaders=host, Signature=SIG_SECRET'
        ),
        'diagnostics': 'path=src/app.py status=failed reason_code=AUTH_DENIED',
    }

    rendered = repr(bounded_event_data(payload))

    for secret in (
        'bob',
        'prod',
        'DIGEST_SECRET',
        'ACCESS_SECRET',
        '20260830',
        'SIG_SECRET',
    ):
        assert secret not in rendered
    assert 'Authorization: Digest [REDACTED]' in rendered
    assert 'Authorization: AWS4-HMAC-SHA256 [REDACTED]' in rendered
    for diagnostic in ('src/app.py', 'status=failed', 'AUTH_DENIED'):
        assert diagnostic in rendered


def test_bounded_event_data_preserves_innocent_credential_words() -> None:
    message = 'tokenization secretariat passwordless Bearerish api-keyword'

    projected = bounded_event_data({'message': message})

    assert projected['message'] == message


def test_runtime_event_uses_canonical_event_data_sanitization() -> None:
    event = RuntimeEvent.from_fields(
        'warning',
        RunCorrelation.fresh(),
        {
            'message': 'authorization: Bearer EVENT_SECRET',
            'path': 'src/app.py',
            'reason_code': 'AUTH_DENIED',
        },
    )

    serialized = event.to_legacy_dict()

    assert 'EVENT_SECRET' not in repr(serialized)
    assert serialized['data']['path'] == 'src/app.py'
    assert serialized['data']['reason_code'] == 'AUTH_DENIED'


def test_checkpoint_round_trip_preserves_root_task_identity() -> None:
    state = AgentState(root_task_id="root-1")
    checkpoint = state.to_checkpoint_dict()
    restored = AgentState()

    restored.from_checkpoint_dict(checkpoint)

    assert checkpoint["root_task_id"] == "root-1"
    assert restored.root_task_id == "root-1"


def test_snapshot_is_final_fact_owner_and_is_created_once() -> None:
    failed_result = ToolResult(
        invocation_id="inv-1",
        status=ToolStatus.FAILED,
        error=ToolError("TASK_AUTHORITY_DENIED", "denied"),
        executed=False,
    )
    state = _state(failed_result)
    correlation = RunCorrelation.fresh()
    metrics = project_run_metrics([])
    calls: list[bool] = []
    orchestrator = SimpleNamespace(
        agent_state=state,
        _run_correlation=correlation,
        _task_failed=False,
        _cancelled=False,
        _last_failure_code=None,
        _last_failure_layer=None,
    )

    snapshot = build_canonical_run_snapshot(
        orchestrator,
        "failed",
        error="denied",
        metrics=metrics,
        record_metric=calls.append,
    )
    second = build_canonical_run_snapshot(
        orchestrator,
        "succeeded",
        metrics=project_run_metrics(
            [{"type": "model_call", "total_tokens": 999, "token_usage_complete": True}]
        ),
        record_metric=calls.append,
    )

    assert second is snapshot
    assert calls == [False]
    assert snapshot.status == "failed"
    assert snapshot.correlation == correlation
    assert snapshot.metrics is metrics
    assert snapshot.failure_fact is not None
    assert snapshot.failure_fact.code == "TASK_AUTHORITY_DENIED"
    assert isinstance(snapshot.failure_fact, FailureFact)
    with pytest.raises(FrozenInstanceError):
        snapshot.status = "succeeded"  # type: ignore[misc]


def test_receipt_and_report_project_snapshot_facts_without_recomputing_them() -> None:
    correlation = RunCorrelation.fresh()
    snapshot = build_canonical_run_snapshot(
        SimpleNamespace(
            agent_state=_state(),
            _run_correlation=correlation,
            _task_failed=False,
            _cancelled=False,
        ),
        "succeeded",
        metrics=project_run_metrics(
            [{"type": "model_call", "total_tokens": 7, "token_usage_complete": True}]
        ),
    )
    state = _state()
    state.terminal_disposition = "fail"
    receipt = build_run_receipt(".", state, "failed", "changed", snapshot=snapshot)
    report = TaskReportBuilder({}).build_report(
        state,
        [],
        "done",
        snapshot=snapshot,
        receipt=receipt,
    )

    assert receipt["status"] == snapshot.status
    assert receipt["operational_outcome"] == snapshot.operational_outcome.to_dict()
    assert receipt["metrics"] == snapshot.metrics.to_dict()
    assert report["status"] == snapshot.status
    assert report["metrics"] == snapshot.metrics.to_dict()
    assert report["operational_outcome"] == snapshot.operational_outcome.to_dict()
    assert report["run_id"] == correlation.run_id
    assert report["root_task_id"] == correlation.root_task_id
    assert report["task_id"] == correlation.task_id
    assert report["report_id"] not in {
        correlation.run_id,
        correlation.root_task_id,
        correlation.task_id,
    }


def test_wave4_architecture_checker_passes_and_rejects_adversarial_ownership() -> None:
    assert run_checks() == []
    assert any(item.startswith("W4-S1:") for item in check_source(
        "from uuid import uuid4\ndef bad(): return uuid4().hex",
        "agent/evaluation/adversarial.py",
    ))
    assert any(item.startswith("W4-S2:") for item in check_source(
        "def bad(state): state.events.append({'type': 'x', 'data': {}})",
        "agent/orchestration/adversarial.py",
    ))
    assert any(item.startswith("W4-S3:") for item in check_source(
        "def bad(entries): return next(item['run_id'] for item in entries)",
        "agent/reporting/adversarial.py",
    ))
    assert any(item.startswith("W4-S4:") for item in check_source(
        "from agent.runtime.operational_outcome import normalize_terminal_status\ndef bad(state): return normalize_terminal_status(explicit_status='succeeded')",
        "agent/reporting/adversarial.py",
    ))
    assert any(item.startswith("W4-S5:") for item in check_source(
        "from agent.reporting.metrics import project_run_metrics\ndef bad(entries): return project_run_metrics(entries)",
        "agent/evaluation/adversarial.py",
    ))
    assert any(item.startswith("W4-S6:") for item in check_source(
        "class TaskReportBuilder:\n    def _generate_task_id(self): return 'x'",
        "agent/reporting/adversarial.py",
    ))


def test_agent_run_result_success_is_snapshot_projection() -> None:
    correlation = RunCorrelation.fresh()
    snapshot = build_canonical_run_snapshot(
        SimpleNamespace(
            agent_state=_state(),
            _run_correlation=correlation,
            _task_failed=False,
            _cancelled=False,
        ),
        "succeeded",
        metrics=project_run_metrics([]),
    )
    result = AgentRunResult(
        status="succeeded",
        answer="done",
        workspace=".",
        snapshot=snapshot,
    )

    assert result.success is True
    assert result.ok is True
