from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from llm_agent.agent.engineering.contracts import (
    EngineeringCaller,
    EngineeringErrorV1,
    EngineeringQueryStatus,
    EngineeringWorkspaceContext,
    SourceRepositoryContext,
)
from llm_agent.agent.engineering.registry import production_registry
from llm_agent.application.actions import engineering as actions
from llm_agent.application.context import AppPaths


def test_operation_ids_and_list_describe_match_canonical_views(tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home")
    workspace = EngineeringWorkspaceContext("sample-workspace", tmp_path)
    candidate = "a" * 40 + ":" + "b" * 64 + ":" + "c" * 64
    source = SourceRepositoryContext(tmp_path, candidate)
    context = actions._context(
        paths,
        interactive=True,
        workspace_id=workspace.workspace_id,
        workspace_root=workspace.root,
        source_root=source.root,
        source_identity=source.candidate_identity,
    )
    registry = production_registry()
    ids = tuple(item.operation_id for item in registry.descriptors())

    assert actions.engineering_operation_ids() == ids
    listed = actions.list_engineering_operations(
        paths,
        interactive=True,
        workspace_id=workspace.workspace_id,
        workspace_root=workspace.root,
        source_root=source.root,
        source_identity=source.candidate_identity,
    )
    assert [item["operation_id"] for item in listed.payload["operations"]] == list(ids)
    assert listed.payload["operations"] == [item.to_dict() for item in actions._runtime().service.list_operations(context)]

    described = actions.describe_engineering_operation("health.offline", paths, interactive=True, workspace_id=workspace.workspace_id, workspace_root=workspace.root)
    canonical = registry.view(registry.get("health.offline"), context)
    assert described.payload == canonical.to_dict()
    missing = actions.describe_engineering_operation("missing.operation", paths, interactive=False)
    assert missing.payload["code"] == "ENGINEERING_OPERATION_NOT_FOUND"
    assert missing.exit_code == 2


def test_history_and_result_actions_keep_canonical_empty_and_missing_behavior(tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home")
    history = actions.query_engineering_history(paths, interactive=False)
    assert history.payload == {"schema_version": 1, "runs": []}
    assert history.exit_code == 0

    result = actions.query_engineering_result("engr-" + "0" * 32, paths, interactive=False)
    assert result.payload["code"] == "ENGINEERING_RUN_NOT_FOUND"
    assert result.payload["query_status"] == "blocked"
    assert result.exit_code == 2


def test_run_constructs_private_context_request_and_keeps_one_service(monkeypatch, tmp_path: Path) -> None:
    calls: list[object] = []

    class Service:
        def preflight(self, request, context, **kwargs):
            calls.append(("preflight", request, context))
            return object()

        def run(self, request, context):
            calls.append(("run", request, context))
            return EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, "ENGINEERING_OPERATION_NOT_FOUND")

    service = Service()
    runtime = SimpleNamespace(registry=object(), store=object(), service=service)
    monkeypatch.setattr(actions, "_runtime", lambda: runtime)
    guard_calls: list[str] = []

    fault = {"schema_version": 1, "steps": [{"call_index": 1, "effect": "timeout"}]}
    outcome = actions.run_engineering(
        "health.offline",
        {"fault": fault},
        AppPaths.discover(app_home=tmp_path / "home"),
        interactive=False,
        allow_fault_injection=True,
        workspace_id="sample-workspace",
        workspace_root=tmp_path,
        source_root=tmp_path,
        source_identity="a" * 40 + ":" + "b" * 64 + ":" + "c" * 64,
        run_guard=lambda execute: (guard_calls.append("entered"), execute())[1],
    )

    assert outcome.payload["code"] == "ENGINEERING_OPERATION_NOT_FOUND"
    assert outcome.exit_code == 2
    assert [item[0] for item in calls] == ["preflight", "run"]
    request = calls[0][1]
    context = calls[0][2]
    assert request.parameters["fault"] == fault
    assert context.fault_plan is None
    assert context.caller is EngineeringCaller.AUTOMATION_HEADLESS
    assert context.workspace.workspace_id == "sample-workspace"
    assert context.source_repository.candidate_identity.startswith("a" * 40)
    assert guard_calls == ["entered"]
    assert calls[1][1] is request and calls[1][2] is context


def test_explicit_consent_denial_skips_startup_guard(tmp_path: Path) -> None:
    called = False

    def guard(_execute):
        nonlocal called
        called = True
        raise AssertionError("denied action must not enter startup lifecycle")

    outcome = actions.run_engineering(
        "acceptance.installed-package",
        {},
        AppPaths.discover(app_home=tmp_path / "home"),
        interactive=True,
        denied_permission="external",
        run_guard=guard,
    )
    assert outcome.payload["code"] == "ENGINEERING_EXTERNAL_NOT_AUTHORIZED"
    assert outcome.exit_code == 2
    assert not called
