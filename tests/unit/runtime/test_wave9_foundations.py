from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from agent.observability import (
    DiagnosticRecord,
    DiagnosticSeverity,
    ObservabilityMode,
    ObservationEnvelope,
    ObservationSource,
    redact_observation_value,
)
from agent.runtime.correlation import RunCorrelation
from agent.runtime.events import RuntimeEvent
from scripts import check_wave9_architecture as architecture_checker


def test_public_observability_modes_are_exactly_four() -> None:
    assert tuple(item.name for item in ObservabilityMode) == (
        "NORMAL",
        "VERBOSE",
        "DEBUG",
        "TRACE",
    )
    assert ObservabilityMode.parse("TRACE") is ObservabilityMode.TRACE
    assert ObservabilityMode.parse("normal") is ObservabilityMode.NORMAL
    with pytest.raises(ValueError):
        ObservabilityMode.parse("off")


def test_diagnostic_record_is_bounded_frozen_and_deterministic() -> None:
    record = DiagnosticRecord(
        category="pipeline-health",
        severity="WARNING",
        timestamp="2026-09-02T12:00:00+00:00",
        run_id="run-1",
        data={"z": 1, "nested": {"password": "secret", "a": "ok"}},
        summary="Authorization: Bearer top-secret",
    )
    assert record.kind == "pipeline_health"
    assert record.severity is DiagnosticSeverity.WARNING
    assert record.data["nested"]["password"] == "[REDACTED]"
    assert "top-secret" not in record.to_json()
    assert record.to_json() == record.to_json()
    with pytest.raises(TypeError):
        record.data["new"] = "value"  # type: ignore[index]
    with pytest.raises((FrozenInstanceError, AttributeError)):
        record.message = "changed"  # type: ignore[misc]


def test_trace_level_does_not_capture_reasoning_or_credentials() -> None:
    record = DiagnosticRecord(
        kind="transport",
        severity=DiagnosticSeverity.DEBUG,
        minimum_mode=ObservabilityMode.TRACE,
        data={
            "chain_of_thought": "private reasoning",
            "prompt": "unrestricted prompt",
            "headers": {"Authorization": "Bearer token"},
            "nested": [{"api_key": "key"}],
            "token_count": 3,
        },
    )
    encoded = record.to_json()
    assert "private reasoning" not in encoded
    assert "unrestricted prompt" not in encoded
    assert "Bearer token" not in encoded
    assert '"api_key":"key"' not in encoded
    assert "token_count" in encoded


def test_envelope_keeps_semantic_event_and_diagnostic_sources_distinct() -> None:
    correlation = RunCorrelation(run_id="run-1", root_task_id="root-1", task_id="root-1")
    event = RuntimeEvent.from_fields("task_node_started", correlation, {"timestamp": "not-used"})
    diagnostic = DiagnosticRecord(kind="pipeline_health", run_id="run-1", timestamp="2026-09-02T12:00:00Z")
    event_envelope = ObservationEnvelope.runtime_event(event, 1)
    diagnostic_envelope = ObservationEnvelope.diagnostic(diagnostic, 2)
    assert event_envelope.source is ObservationSource.RUNTIME_EVENT
    assert diagnostic_envelope.source is ObservationSource.DIAGNOSTIC
    assert event_envelope.sequence == 1
    assert diagnostic_envelope.sequence == 2
    assert event_envelope.to_dict()["payload"]["type"] == "task_node_started"
    assert diagnostic_envelope.to_dict()["payload"]["type"] == "diagnostic"
    assert json.loads(diagnostic_envelope.to_json())["source"] == "diagnostic"


def test_redaction_is_recursive_and_rejects_implicit_arbitrary_objects() -> None:
    safe = redact_observation_value({"items": [{"access_token": "secret"}], "count": 2})
    assert safe["items"][0]["access_token"] == "[REDACTED]"

    class Arbitrary:
        pass

    with pytest.raises(TypeError):
        redact_observation_value({"value": Arbitrary()})


def test_foundation_checker_rejects_ui_imports_in_neutral_packages(tmp_path) -> None:
    package = tmp_path / "agent" / "presentation"
    package.mkdir(parents=True)
    (package / "rogue.py").write_text(
        "from rich.console import Console\nfrom agent.interfaces.cli import app\n",
        encoding="utf-8",
    )
    findings = architecture_checker.check_architecture(tmp_path)
    assert {item.rule_id for item in findings} >= {"W9-S1", "W9-S2"}


def test_foundation_checker_rejects_domain_mode_branching(tmp_path) -> None:
    package = tmp_path / "agent" / "planning"
    package.mkdir(parents=True)
    (package / "rogue.py").write_text(
        "from agent.observability.modes import ObservabilityMode\n"
        "def decide(mode):\n"
        "    if mode is ObservabilityMode.TRACE:\n"
        "        return 'trace'\n"
        "    return 'normal'\n",
        encoding="utf-8",
    )
    findings = architecture_checker.check_architecture(tmp_path)
    assert any(item.rule_id == "W9-S3" for item in findings)
