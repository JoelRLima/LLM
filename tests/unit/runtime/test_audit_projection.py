from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.llm.identity import declared_model_audit_identity
from agent.observability.audit_projection import (
    MAX_AUDIT_MODEL_IDS,
    MAX_AUDIT_TOOLS,
    RunAuditReceipt,
    audit_event_fields,
    build_run_audit_receipt,
    project_approval_disposition,
    project_artifacts,
    project_effect,
    project_model_identity,
    project_required_capabilities,
    project_tool_descriptor,
)
from agent.runtime.context_results import Artifact
from agent.runtime.correlation import RunCorrelation
from agent.tools.authority import ApplicationAuthoritySnapshot, TaskAuthoritySnapshot
from agent.tools.contracts import ToolDescriptor, ToolOriginKind, ToolResult, ToolStatus


def _descriptor(
    name: str = "reader",
    *,
    origin: ToolOriginKind = ToolOriginKind.BUILTIN,
    extension_id: str | None = None,
) -> ToolDescriptor:
    return ToolDescriptor(
        name,
        "safe description",
        capabilities=frozenset({"read"}),
        adapter_id="adapter-v1",
        source_version="2026.09",
        protocol_version="1.2",
        origin_kind=origin,
        extension_id=extension_id,
    )


def _snapshot(status: str = "succeeded", *, failure: object = None) -> SimpleNamespace:
    correlation = RunCorrelation(
        run_id="run-1",
        root_task_id="task-1",
        task_id="task-1",
    )
    outcome = SimpleNamespace(
        mutation_occurred=status == "succeeded",
        validation_status="validated",
        final_state="applied" if status == "succeeded" else None,
    )
    return SimpleNamespace(
        correlation=correlation,
        status=status,
        failure_fact=failure,
        operational_outcome=outcome,
    )


def _orchestrator(events: list[dict[str, object]], metrics: list[dict[str, object]]):
    app_authority = ApplicationAuthoritySnapshot(
        workspace_id="workspace-1",
        snapshot_id="app-snapshot-1",
        policy_version="policy-7",
        provenance="test-bootstrap",
    )
    task_authority = TaskAuthoritySnapshot(
        allowed_capabilities=frozenset({"read", "write"}),
        policy_source="test-task-policy",
        snapshot_id="task-snapshot-1",
        runtime_identity=app_authority.runtime_identity,
    )
    registry = SimpleNamespace(descriptor=lambda name: _descriptor(name))
    profile = SimpleNamespace(
        provider="fixture-provider",
        model="declared-model",
        name="test-profile",
        profile_name="test-profile",
        endpoint_identity="https://fixture.invalid/v1",
        fingerprint="f" * 64,
    )
    gateway = SimpleNamespace(
        provider_name="fixture-provider",
        model="declared-model",
        endpoint_identity="https://fixture.invalid/v1",
    )
    return SimpleNamespace(
        application_authority=app_authority,
        task_authority=task_authority,
        tool_registry=registry,
        agent_state=SimpleNamespace(events=events, tool_history=[]),
        session=SimpleNamespace(
            model_profile=profile,
            gateway=gateway,
            config={},
        ),
        _get_metrics_for_task=lambda: metrics,
    )


def test_declared_model_identity_is_bounded_and_has_no_credential_value() -> None:
    profile = SimpleNamespace(
        provider="fixture-provider",
        model="declared-model",
        name="safe-profile",
        endpoint_identity="https://fixture.invalid/v1",
        fingerprint="a" * 64,
    )
    gateway = SimpleNamespace(provider_name="fixture-provider", model="declared-model")

    identity = project_model_identity(profile, gateway)

    assert identity == {
        "provider": "fixture-provider",
        "declared_model": "declared-model",
        "profile_name": "safe-profile",
        "endpoint_identity": "https://fixture.invalid/v1",
        "model_config_fingerprint": "a" * 64,
    }
    assert "sentinel" not in json.dumps(identity)
    assert declared_model_audit_identity(gateway, profile) == identity


def test_descriptor_identity_covers_builtin_and_extension_without_schema() -> None:
    builtin = project_tool_descriptor(_descriptor())
    extension = project_tool_descriptor(
        _descriptor("writer", origin=ToolOriginKind.EXTENSION, extension_id="demo.extension")
    )

    assert builtin == {
        "name": "reader",
        "origin_kind": "builtin",
        "adapter_id": "adapter-v1",
        "extension_id": None,
        "source_version": "2026.09",
        "protocol_version": "1.2",
    }
    assert extension["origin_kind"] == "extension"
    assert extension["extension_id"] == "demo.extension"
    assert "schema" not in builtin
    assert project_tool_descriptor(None) is None


def test_required_capabilities_are_sorted_bounded_and_approval_vocabulary_is_closed() -> None:
    assert project_required_capabilities(["write", "read", "read"]) == ["read", "write"]
    assert project_required_capabilities(None) is None
    assert [project_approval_disposition(value) for value in (
        "not_required", "approved", "required", "denied", "failed", "unknown"
    )] == ["not_required", "approved", "required", "denied", "failed", "unknown"]
    assert project_approval_disposition("authorized") == "unknown"


def test_effect_projection_reuses_canonical_mutation_evidence(tmp_path: Path) -> None:
    read_result = ToolResult(
        "read-1",
        ToolStatus.SUCCEEDED,
        metadata={"mutation_occurred": False},
    )
    persisted_result = ToolResult(
        "write-1",
        ToolStatus.SUCCEEDED,
        metadata={
            "mutation_attempted": True,
            "mutation_occurred": True,
            "persisted_mutation": True,
            "validation_status": "validated",
            "affected_files": [str(tmp_path / "src" / "main.py")],
        },
    )
    rollback_result = ToolResult(
        "rollback-1",
        ToolStatus.FAILED,
        metadata={
            "mutation_occurred": True,
            "rollback_occurred": True,
            "final_state": "restored",
            "affected_files": ["src/temporary.py"],
        },
    )

    assert project_effect(read_result)["occurred"] is False
    persisted = project_effect(persisted_result, tmp_path)
    assert persisted["persisted"] is True
    assert persisted["affected_files"] == ["src/main.py"]
    rollback = project_effect(rollback_result, tmp_path)
    assert rollback["rollback_occurred"] is True
    assert rollback["final_state"] == "restored"


def test_artifact_projection_excludes_content_and_redacts_external_paths(tmp_path: Path) -> None:
    artifact = Artifact(
        "report",
        path=str(tmp_path / "reports" / "result.json"),
        content="SECRET-CONTENT-SENTINEL",
        metadata={
            "sha256": "a" * 64,
            "output": "RAW-OUTPUT-SENTINEL",
            "format": "json",
        },
    )
    external = Artifact(
        "external",
        path=str(tmp_path.parent / "outside.txt"),
        content="EXTERNAL-CONTENT-SENTINEL",
    )
    result = ToolResult("artifacts-1", ToolStatus.SUCCEEDED, artifacts=(artifact, external))

    projected = project_artifacts(result, tmp_path)
    encoded = json.dumps(projected, ensure_ascii=False)
    assert "SECRET-CONTENT-SENTINEL" not in encoded
    assert "RAW-OUTPUT-SENTINEL" not in encoded
    assert "EXTERNAL-CONTENT-SENTINEL" not in encoded
    assert projected["items"][0]["workspace_relative_path"] == "reports/result.json"
    assert projected["items"][1]["workspace_relative_path"] == {"scope": "external"}
    assert projected["items"][0]["sha256"] == "a" * 64


def test_tool_event_projection_keeps_approval_and_execution_facts_only() -> None:
    result = ToolResult(
        "tool-1",
        ToolStatus.SUCCEEDED,
        data={"raw": "RAW-RESULT-SENTINEL"},
    )
    fields = audit_event_fields(
        "tool_end",
        {
            "descriptor": _descriptor(),
            "required_capabilities": ("write", "read"),
            "approval_disposition": "approved",
        },
        result=result,
    )

    assert fields["descriptor"]["name"] == "reader"
    assert fields["required_capabilities"] == ["read", "write"]
    assert fields["approval_disposition"] == "approved"
    assert "raw" not in json.dumps(fields)
    assert audit_event_fields("tool_denied", None)["executed"] is False


def test_receipt_projects_authority_terminal_observation_and_truthful_truncation() -> None:
    events = [
        {
            "type": "tool_start",
            "data": {
                "tool": f"tool-{index}",
                "descriptor": project_tool_descriptor(_descriptor(f"tool-{index}")),
            },
        }
        for index in range(MAX_AUDIT_TOOLS + 3)
    ]
    metrics = [
        {
            "type": "model_call",
            "metric_type": "model_call",
            "observed_provider_model_id": f"observed-{index}",
        }
        for index in range(MAX_AUDIT_MODEL_IDS + 3)
    ]
    orchestrator = _orchestrator(events, metrics)
    receipt = build_run_audit_receipt(orchestrator, _snapshot())
    value = receipt.to_dict()

    assert value["model"]["observed_identity_complete"] is True
    assert len(value["model"]["observed_provider_model_ids"]) == MAX_AUDIT_MODEL_IDS
    assert len(value["tools"]) == MAX_AUDIT_TOOLS
    assert value["bounds"]["tool_identities_omitted"] == 3
    assert value["bounds"]["observed_model_ids_omitted"] == 3
    assert value["bounds"]["truncated"] is True
    assert value["authority"]["application_snapshot_id"] == "app-snapshot-1"
    assert value["terminal"]["status"] == "succeeded"
    assert value["terminal"]["mutation_occurred"] is True


def test_receipt_failure_uses_failure_fact_and_unknown_descriptor_does_not_fabricate_version() -> None:
    failure = SimpleNamespace(code="TOOL_FAILED", layer=SimpleNamespace(value="tool"))
    orchestrator = _orchestrator(
        [{"type": "tool_denied", "data": {"tool": "missing-tool", "descriptor": None}}],
        [{"type": "model_call", "metric_type": "model_call"}],
    )
    def missing_descriptor(_name: str) -> ToolDescriptor:
        raise KeyError(_name)

    orchestrator.tool_registry = SimpleNamespace(descriptor=missing_descriptor)
    receipt = build_run_audit_receipt(orchestrator, _snapshot("failed", failure=failure)).to_dict()

    assert receipt["terminal"]["failure_code"] == "TOOL_FAILED"
    assert receipt["model"]["observed_identity_complete"] is False
    assert receipt["tools"][0]["name"] == "missing-tool"
    assert receipt["tools"][0]["source_version"] is None
    assert receipt["tools"][0]["protocol_version"] is None


def test_receipt_is_immutable_and_to_dict_is_a_fresh_projection() -> None:
    receipt = build_run_audit_receipt(_orchestrator([], []), _snapshot())

    with pytest.raises(TypeError):
        receipt.model["provider"] = "mutated"  # type: ignore[index]
    first = receipt.to_dict()
    first["model"]["provider"] = "mutated"
    second = receipt.to_dict()
    assert second["model"]["provider"] == "fixture-provider"
    assert isinstance(receipt, RunAuditReceipt)
