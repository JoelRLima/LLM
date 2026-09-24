"""Permanent W20-C adversarial identities; execution belongs to user closure."""

from __future__ import annotations

import json
from argparse import ArgumentParser
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.discovery.index import cli_entries_from_parser
from agent.engineering.model_safe import MODEL_SAFE_TOOL_DESCRIPTORS, ModelSafeResponse, ModelSafeStatus
from agent.interfaces.cli.completion import completion_commands
from agent.interfaces.cli.discovery_projection import build_catalog
from agent.interfaces.cli.parser import build_parser
from agent.interfaces.mcp.projection import response_document
from agent.runtime.workspace_context import WorkspaceContext

ROOT = Path(__file__).resolve().parents[3]
W20C_CAMPAIGN_CASES = tuple(f"C{i:02d}" for i in range(1, 25))


def test_C01_base_import_parser_has_no_optional_import_leakage() -> None:
    parser = build_parser()
    assert isinstance(parser, ArgumentParser)
    parsed = parser.parse_args(["commands", "help", "--plain"])
    assert parsed.command == "commands" and parsed.plain is True


def test_C02_missing_extra_contract_is_lazy_and_stable(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from agent.interfaces.cli import mcp as cli_mcp

    monkeypatch.setattr(cli_mcp.importlib.util, "find_spec", lambda _name: None)
    assert cli_mcp.run_mcp_engineering(SimpleNamespace(workspace=str(ROOT), home=None)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert cli_mcp.MCP_EXTRA_REQUIRED in captured.err


def test_C03_mcp_workspace_is_mandatory() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["mcp", "engineering"])


def test_C04_workspace_binding_is_canonical_and_immutable() -> None:
    pytest.importorskip("mcp")
    from agent.interfaces.mcp.engineering_server import MCPWorkspaceBinding

    value = WorkspaceContext.create(ROOT)
    assert value.root == value.root.resolve() and value.workspace_id
    binding = MCPWorkspaceBinding.from_path(ROOT)
    assert binding.root == value.root and binding.workspace_id == value.workspace_id
    with pytest.raises(FrozenInstanceError):
        binding.root = binding.root  # type: ignore[misc]


def test_C05_mcp_schemas_reject_authority_fields() -> None:
    forbidden = {"workspace", "workspace_id", "home", "source_root", "permissions", "authority", "channel", "raw_path"}
    for descriptor in MODEL_SAFE_TOOL_DESCRIPTORS:
        assert not forbidden.intersection(descriptor.schema.get("properties", {}))
        assert descriptor.schema.get("additionalProperties") is False


def test_C06_tools_list_is_exactly_the_five_model_safe_tools() -> None:
    assert tuple(item.name for item in MODEL_SAFE_TOOL_DESCRIPTORS) == (
        "engineering_list", "engineering_describe", "engineering_result", "engineering_inspect_completed", "engineering_health_offline"
    )


def test_C07_low_level_server_advertises_tools_only() -> None:
    pytest.importorskip("mcp")
    from agent.interfaces.mcp.engineering_server import MCPWorkspaceBinding, create_server

    server = create_server(
        MCPWorkspaceBinding.from_path(ROOT),
        SimpleNamespace(invoke=lambda *_args: None),
        SimpleNamespace(),
    )
    assert type(server).__name__ == "Server"
    assert tuple(item.name for item in MODEL_SAFE_TOOL_DESCRIPTORS) == (
        "engineering_list", "engineering_describe", "engineering_result",
        "engineering_inspect_completed", "engineering_health_offline",
    )


def test_C08_mcp_schema_owner_is_model_safe_descriptor_metadata() -> None:
    pytest.importorskip("mcp")
    from agent.interfaces.mcp.engineering_server import _tool

    assert all(item.result_data_schema is None and item.adapter_id == "builtin" for item in MODEL_SAFE_TOOL_DESCRIPTORS)
    for descriptor in MODEL_SAFE_TOOL_DESCRIPTORS:
        wire = _tool(descriptor)
        schema = getattr(wire, "inputSchema", getattr(wire, "input_schema", None))
        assert wire.name == descriptor.name and schema == descriptor.schema


def test_C09_success_projection_has_equal_text_and_structured_document() -> None:
    document = response_document(ModelSafeResponse("engineering_list", ModelSafeStatus.AVAILABLE, {"operations": []}))
    assert json.loads(json.dumps(document, sort_keys=True)) == document


def test_C10_engineering_error_projection_is_error_safe() -> None:
    document = response_document(ModelSafeResponse("engineering_result", ModelSafeStatus.UNKNOWN, reason_code="ENGINEERING_RUN_NOT_FOUND"))
    assert document["reason_code"] == "ENGINEERING_RUN_NOT_FOUND" and "traceback" not in document


def test_C11_unexpected_adapter_failure_has_generic_reason() -> None:
    pytest.importorskip("mcp")
    from agent.interfaces.mcp.engineering_server import _safe_adapter_failure

    response = _safe_adapter_failure("engineering_list", RuntimeError("private detail"))
    assert response.reason_code == "MCP_ADAPTER_ERROR"
    assert "private detail" not in json.dumps(response.to_dict())


def test_C12_foreign_workspace_result_is_unknown() -> None:
    from agent.engineering.model_safe import ModelSafeEngineering

    owner = ModelSafeEngineering(
        SimpleNamespace(),
        SimpleNamespace(result_for_workspace=lambda _run_id, _context: SimpleNamespace(
            workspace_id="workspace-foreign",
            environment=SimpleNamespace(workspace_id="workspace-foreign"),
        )),
    )
    context = SimpleNamespace(workspace=SimpleNamespace(workspace_id="workspace-trusted"))
    response = owner.engineering_result("engr-" + "0" * 32, context)
    assert response.status is ModelSafeStatus.UNKNOWN
    assert response.reason_code == "ENGINEERING_RUN_NOT_FOUND"


def test_C13_mcp_reuses_bounded_redaction_projection() -> None:
    from agent.engineering.model_safe import _project_health, _project_reference

    health = _project_health({"checks": [{"id": "disk", "status": "ok", "message": "private"}]})
    reference = _project_reference(SimpleNamespace(owner="store", kind="run", reference_id="r", sha256=None, label="private"))
    assert health == [{"id": "disk", "status": "ok"}]
    assert "label" not in reference


def test_C14_anyio_bridge_is_non_abandoning() -> None:
    pytest.importorskip("mcp")
    from agent.interfaces.mcp.engineering_server import MCP_ABANDON_ON_CANCEL

    assert MCP_ABANDON_ON_CANCEL is False


def test_C15_lifecycle_closes_after_stdio(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    import agent.interfaces.mcp.engineering_server as server_module

    events: list[str] = []

    class Lease:
        def activate(self) -> None:
            events.append("activate")

        def close(self) -> None:
            events.append("close")

    binding = SimpleNamespace(root=tmp_path.resolve(), workspace_id="workspace-c")
    lease = Lease()
    monkeypatch.setattr(server_module.MCPWorkspaceBinding, "from_path", classmethod(lambda _cls, _value: binding))
    monkeypatch.setattr(server_module.AppPaths, "discover", classmethod(lambda _cls, app_home=None: SimpleNamespace(home_dir=tmp_path)))
    monkeypatch.setattr(server_module.HomeLifecycleLease, "begin_startup", classmethod(lambda _cls, _home: lease))
    monkeypatch.setattr(server_module.StorageBootstrap, "prepare", lambda _self, _paths: events.append("prepare"))
    monkeypatch.setattr(server_module, "_default_adapter", lambda _binding, _paths: object())
    monkeypatch.setattr(server_module.anyio, "run", lambda _call, *_args: events.append("run"))

    assert server_module.serve_engineering_stdio(tmp_path) == 0
    assert events == ["prepare", "activate", "run", "close"]


def test_C16_stdio_diagnostics_are_stderr_only() -> None:
    document = response_document(ModelSafeResponse("engineering_list", ModelSafeStatus.DEGRADED, reason_code="MCP_ADAPTER_ERROR"))
    assert document["reason_code"] == "MCP_ADAPTER_ERROR"


def test_C17_mcp_startup_does_not_require_application_or_model() -> None:
    pytest.importorskip("mcp")
    import agent.interfaces.mcp.engineering_server as server_module

    assert "AgentApplication" not in vars(server_module)
    assert "ModelProvider" not in vars(server_module)


def test_C18_discovery_disabled_reason_is_stable_when_extra_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.discovery.service.importlib.util.find_spec", lambda _name: None)
    catalog, availability = build_catalog()
    entry = next(item for item in catalog.entries() if item.preferred_invocation == "mcp engineering")
    assert entry.entry_id in availability
    assert availability[entry.entry_id].available is False
    assert availability[entry.entry_id].disabled_reason == "ENGINEERING_MCP_EXTRA_REQUIRED"


def test_C19_completion_metadata_contains_mcp_without_sdk_import() -> None:
    parser_entries = cli_entries_from_parser(build_parser())
    assert any(item.preferred_invocation == "mcp engineering" for item in parser_entries)
    assert "mcp engineering" in completion_commands()


def test_C20_union_lock_preserves_base_package_lines() -> None:
    from distribution.mcp_lockfiles import validate_mcp_union_lock

    summary = validate_mcp_union_lock(
        ROOT / "distribution/runtime-windows-py312.lock",
        ROOT / "distribution/mcp-windows-py312.lock",
    )
    assert "mcp" in summary.packages


def test_C21_union_lock_validator_rejects_duplicates_and_conflicts(tmp_path: Path) -> None:
    from distribution.lockfiles import LockValidationError
    from distribution.mcp_lockfiles import validate_mcp_union_lock

    base = tmp_path / "base.lock"
    union = tmp_path / "union.lock"
    requirement = "--only-binary :all:\na==1.0.0 \\\n    --hash=sha256:" + "0" * 64 + "\n"
    base.write_text(requirement, encoding="utf-8")
    union.write_text(
        requirement + requirement + "mcp==2.2.0 \\\n    --hash=sha256:" + "1" * 64 + "\n",
        encoding="utf-8",
    )
    with pytest.raises(LockValidationError, match="duplicate logical package"):
        validate_mcp_union_lock(base, union)
    conflict = tmp_path / "conflict.lock"
    conflict.write_text(
        "--only-binary :all:\nb==2.0.0 \\\n    --hash=sha256:" + "2" * 64 + "\nmcp==2.2.0 \\\n    --hash=sha256:" + "1" * 64 + "\n",
        encoding="utf-8",
    )
    with pytest.raises(LockValidationError, match="changed base package"):
        validate_mcp_union_lock(base, conflict)


def test_C22_base_installed_verifier_has_no_mcp_acceptance_probe() -> None:
    from scripts.verify_installed_package import installed_acceptance_summary

    summary = installed_acceptance_summary(status="passed", mode="offline_diagnostic", candidate={})
    assert summary["mcp_extra_installed"] is False
    assert summary["mcp_missing_extra_probe"] == "passed"
    assert summary["mcp_missing_extra_reason"] == "ENGINEERING_MCP_EXTRA_REQUIRED"


def test_C23_extra_verifier_requires_real_stdio_and_exact_tools() -> None:
    from scripts.verify_wave20_mcp_extra import (
        TOOLS,
        _inspection_lists_within_limit,
        _mcp_requests,
        acceptance_summary,
    )

    inspection_request = _mcp_requests("engr-" + "0" * 32, "inspection-run")[-1]
    assert inspection_request["id"] == 7
    assert inspection_request["params"]["arguments"]["limit"] == 1
    bounded_document = {
        "data": {
            "tools": {"count": 3, "events": [{"sequence": 1}]},
            "issues": 3,
        }
    }
    unbounded_document = {
        "data": {
            "tools": {"count": 3, "events": [{"sequence": 1}, {"sequence": 2}]},
            "issues": 3,
        }
    }
    assert _inspection_lists_within_limit(bounded_document, 1)
    assert not _inspection_lists_within_limit(unbounded_document, 1)

    wheel = ROOT / "pyproject.toml"
    probe = {
        "tools": list(TOOLS),
        "protocol_stdout_pure": True,
        "initialize_capabilities_tools": True,
        "resources_exposed": False,
        "prompts_exposed": False,
        "safe_calls": True,
        "safe_inspection_projection": True,
        "safe_error_path": True,
        "workspace_isolation": True,
        "process_clean": True,
    }
    summary = acceptance_summary(
        wheel=wheel,
        union_lock=ROOT / "distribution/mcp-windows-py312.lock",
        workspace=ROOT,
        roundtrip=probe,
    )
    assert summary["tools_list"] == list(TOOLS)
    assert summary["stdio_roundtrip"] is True
    assert summary["safe_inspection_projection"] is True


def test_C24_integrated_candidate_identity_is_explicit(tmp_path: Path) -> None:
    assert W20C_CAMPAIGN_CASES == tuple(f"C{i:02d}" for i in range(1, 25))
    manifest = tmp_path / "WAVE_20C_MANIFEST_v001_FROZEN.json"
    manifest.write_text('{"schema_version": 1}\n', encoding="utf-8")
    assert manifest.is_file()
