import pytest

from agent.cancellation import CancellationToken
from agent.runtime.context import RuntimeLimits, TaskExecutionContext
from agent.runtime.hardware import LOW_VRAM_8GB, resolve_hardware_profile


class FakeGateway:
    provider_name = "fake"
    capabilities = None


class RecordingSink:
    def __init__(self):
        self.events = []

    def emit(self, event_type, data):
        self.events.append((event_type, data))


class RecordingMetrics:
    def __init__(self):
        self.metrics = []

    def record(self, metric):
        self.metrics.append(metric)


def test_low_vram_profile_serializes_model_calls():
    profile = resolve_hardware_profile({"hardware_profile": "low_vram_8gb"})

    assert profile == LOW_VRAM_8GB
    assert profile.max_model_concurrency == 1
    assert profile.semantic_memory_default is False


def test_child_context_is_correlated_and_keeps_limits():
    sink = RecordingSink()
    parent = TaskExecutionContext(
        model_gateway=FakeGateway(),
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_model_concurrency=1),
        event_sink=sink,
        permissions=frozenset({"read"}),
    )

    child = parent.child("analysis")
    child.emit("started")

    assert child.parent_task_id == parent.task_id
    assert child.task_id != parent.task_id
    assert child.permissions == frozenset({"read"})
    assert sink.events[0][1]["node_id"] == "analysis"
    assert child.model_gate is parent.model_gate
    assert child.process_gate is parent.process_gate


def test_model_budget_and_metrics_are_shared_and_correlated():
    metrics = RecordingMetrics()
    parent = TaskExecutionContext(
        model_gateway=FakeGateway(),
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_model_calls=2),
        metrics_sink=metrics,
    )
    child = parent.child("generation")

    assert parent.consume_model_call() == 1
    assert child.consume_model_call() == 2
    with pytest.raises(RuntimeError, match="Orçamento"):
        child.consume_model_call()

    child.record_metric("model_call", {"tokens": 3})
    assert metrics.metrics[0]["parent_task_id"] == parent.task_id
    assert metrics.metrics[0]["node_id"] == "generation"
