import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent.application import AgentApplication
from agent.approval import AutoApprove
from agent.memory.memory import MemoryLoadError
from agent.planning.step_policies import StepPolicies
from agent.runtime.config_errors import ConfigNotFound
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.instance_lock import InstanceLockError
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.skills import load_skill_registry
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.extension_bootstrap import ApplicationExtensionBootstrap
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.workspace_extensions_service import WorkspaceExtensionService
from tests.support.offline_scenarios import OfflineLegacyGateway


def _initialized_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    return paths


def test_application_runs_trivial_task_with_explicit_workspace(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        result = application.run("oi")

        assert result.success is True
        assert "olá" in result.answer.casefold()
        assert application.workspace.root == workspace.resolve()
        assert application.workspace_paths.state_dir.is_relative_to(paths.state_dir)
        skill_roots = [
            Path(skill.base_dir).resolve()
            for skill in application.orchestrator.skills.values()
            if hasattr(skill, "base_dir")
        ]
        assert skill_roots
        assert set(skill_roots) == {workspace.resolve()}

    assert not (workspace / ".temp_analysis").exists()
    assert application.workspace_paths.memory_db_file.exists()


def test_application_loads_ready_extension_from_catalog_and_workspace(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "extension.json"
    manifest.write_text(
        json.dumps({
            "id": "demo.extension",
            "version": "1.0.0",
            "protocol_version": "1.0",
                "transport": "stdio",
                "entrypoint": ["python", "demo.py"],
                "timeout_seconds": 5,
                "tools": [{"name": "demo_tool", "schema": {}, "capabilities": ["read"]}],
        }),
        encoding="utf-8",
    )
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    workspace_extensions = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
    workspace_extensions.enable("demo.extension")
    workspace_extensions.grant("demo.extension", "read")
    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        assert "demo_tool" in application.tool_registry.names()


def test_extension_aware_bootstrap_does_not_start_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "extension" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "id": "demo.extension",
                "version": "1.0.0",
                "protocol_version": "1.0",
                "transport": "stdio",
                "entrypoint": ["${python}", "${extension_dir}/demo.py"],
                "timeout_seconds": 5,
                "tools": [{"name": "demo_tool", "schema": {}, "capabilities": ["read"]}],
            }
        ),
        encoding="utf-8",
    )
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    workspace_extensions = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
    workspace_extensions.enable("demo.extension")
    workspace_extensions.grant("demo.extension", "read")

    def forbidden(*args, **kwargs):
        raise AssertionError("bootstrap iniciou processo externo")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("os.system", forbidden)
    monkeypatch.setattr("asyncio.create_subprocess_exec", forbidden)
    monkeypatch.setattr("asyncio.create_subprocess_shell", forbidden)
    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        assert "echo" in application.tool_registry.names()
        assert "demo_tool" in application.tool_registry.names()
        adapter = application.tool_registry._descriptors_cache["demo_tool"][0]
        assert adapter.cwd == workspace.resolve()
        assert application.tool_registry.frozen is True


def test_extension_bootstrap_acceptance_drift_replace_and_workspace_isolation(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    manifest = tmp_path / "extension" / "manifest.json"
    manifest.parent.mkdir()

    def write_manifest(path: Path, tool_name: str, version: str) -> None:
        path.write_text(
            json.dumps(
                {
                    "id": "demo.extension",
                    "version": version,
                    "protocol_version": "1.0",
                    "transport": "stdio",
                    "entrypoint": ["${python}", "${extension_dir}/demo.py"],
                    "timeout_seconds": 5,
                    "tools": [{"name": tool_name, "schema": {}, "capabilities": ["read"]}],
                }
            ),
            encoding="utf-8",
        )

    write_manifest(manifest, "demo_tool", "1.0.0")
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    added = catalog.add(manifest)
    workspace_id_a = WorkspaceContext.create(workspace_a).workspace_id
    workspace_id_b = WorkspaceContext.create(workspace_b).workspace_id
    service_a = WorkspaceExtensionService.for_workspace(paths, workspace_id_a, catalog)
    service_a.enable("demo.extension")
    service_a.grant("demo.extension", "read")

    with AgentApplication.create(
        paths=paths,
        workspace=workspace_a,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application_a:
        old_names = application_a.tool_registry.names()
        assert "demo_tool" in old_names
        assert "echo" in old_names

    application_b = ApplicationExtensionBootstrap(
        paths, workspace_id_b, workspace_b
    ).build(BuiltinToolAdapter(load_skill_registry(base_dir=workspace_b)))
    assert "demo_tool" not in application_b.registry.names()
    assert "echo" in application_b.registry.names()

    write_manifest(manifest, "changed_tool", "1.0.1")
    drift = ApplicationExtensionBootstrap(
        paths, workspace_id_a, workspace_a
    ).build(BuiltinToolAdapter(load_skill_registry(base_dir=workspace_a)))
    assert "changed_tool" not in drift.registry.names()
    assert drift.materialization.bindings == ()
    assert application_a.tool_registry.names() == old_names

    replacement = tmp_path / "extension" / "replacement.json"
    write_manifest(replacement, "replacement_tool", "2.0.0")
    catalog.replace(
        "demo.extension",
        replacement,
        expected_fingerprint=added.entry.manifest_sha256,
    )
    replaced = ApplicationExtensionBootstrap(
        paths, workspace_id_a, workspace_a
    ).build(BuiltinToolAdapter(load_skill_registry(base_dir=workspace_a)))
    assert "replacement_tool" in replaced.registry.names()
    assert "demo_tool" not in replaced.registry.names()


def test_resume_restores_persona_and_capabilities(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        assert "web_search" in application.orchestrator.tool_registry.names()
        state = application.orchestrator.agent_state
        state.objective = "pesquise notícias sobre IA"
        state.persona = "researcher"
        state.persona_prompt = "Você é um pesquisador."
        state.set_plan([
            {"tool": "web_search", "args": {"query": "IA"}},
        ])
        application.orchestrator._save_checkpoint()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as resumed_application:
        called: dict[str, object] = {}

        def fake_run_tool(tool_name: str, args, record_result: bool = True):
            called["tool_name"] = tool_name
            called["args"] = args
            called["active_skills"] = list(resumed_application.orchestrator.active_skills)
            called["allowed_capabilities"] = resumed_application.orchestrator.allowed_capabilities
            return {
                "ok": True,
                "done": True,
                "status": "succeeded",
                "data": {"result": "ok"},
            }

        resumed_application.orchestrator.tool_executor.run_tool = fake_run_tool
        result = resumed_application.run(None)

        assert "web_search" in resumed_application.orchestrator.active_skills
        assert "network" in resumed_application.orchestrator.allowed_capabilities
        assert called["tool_name"] == "web_search"
        assert called["args"] == {"query": "IA"}
        assert result is not None


def test_workspaces_do_not_share_state_or_scratch(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    with AgentApplication.create(
        paths=paths,
        workspace=first,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as first_application:
        first_paths = first_application.workspace_paths
    with AgentApplication.create(
        paths=paths,
        workspace=second,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as second_application:
        second_paths = second_application.workspace_paths

    assert first_paths.state_dir != second_paths.state_dir
    assert first_paths.memory_db_file != second_paths.memory_db_file
    assert first_paths.scratch_dir != second_paths.scratch_dir


def test_workspace_config_is_not_loaded_implicitly(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.json").write_text('{"auto_confirm": true}', encoding="utf-8")

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        assert application.config["auto_confirm"] is False


def test_application_requires_initialized_configuration(tmp_path: Path) -> None:
    paths = AppPaths.discover(tmp_path / "home", env={})
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ConfigNotFound):
        AgentApplication.create(
            paths=paths,
            workspace=workspace,
            gateway=OfflineLegacyGateway("unused"),
            configure_logging=False,
        )

    assert not paths.data_dir.exists()
    assert not paths.state_dir.exists()
    assert not paths.log_dir.exists()


def test_application_rejects_corrupt_memory_json_and_releases_bootstrap_lock(
    tmp_path: Path,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = WorkspaceContext.create(workspace)
    workspace_paths = paths.for_workspace(context.workspace_id)
    workspace_paths.ensure_directories()
    workspace_paths.memory_file.write_text('{"notes": ', encoding="utf-8")

    with pytest.raises(MemoryLoadError):
        AgentApplication.create(
            paths=paths,
            workspace=workspace,
            gateway=OfflineLegacyGateway("unused"),
            configure_logging=False,
        )

    assert not workspace_paths.lock_file.exists()
    workspace_paths.memory_file.write_text("{}", encoding="utf-8")
    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ):
        pass


def test_close_is_idempotent(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    )

    application.close()
    application.close()

    with pytest.raises(RuntimeError, match="encerrada"):
        application.run("oi")


def test_close_does_not_persist_memory_twice_after_a_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    )
    memory = application.orchestrator.agent_state.memory
    real_persist = memory.persist_to_file
    calls = 0

    def counted_persist(path=None):
        nonlocal calls
        calls += 1
        return real_persist(path)

    monkeypatch.setattr(memory, "persist_to_file", counted_persist)

    application.run("oi")
    application.close()

    assert calls == 1


def test_same_workspace_state_rejects_second_live_application(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    )
    try:
        with pytest.raises(InstanceLockError, match="em uso"):
            AgentApplication.create(
                paths=paths,
                workspace=workspace,
                gateway=OfflineLegacyGateway("unused"),
                configure_logging=False,
            )
    finally:
        first.close()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ):
        pass


def test_process_stdout_capture_serializes_concurrent_applications(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    roots = (tmp_path / "first", tmp_path / "second")
    for root in roots:
        root.mkdir()
    applications = [
        AgentApplication.create(
            paths=paths,
            workspace=root,
            gateway=OfflineLegacyGateway("unused"),
            configure_logging=False,
        )
        for root in roots
    ]
    guard = threading.Lock()
    active = 0
    maximum_active = 0

    def instrumented_run(_: str) -> str:
        nonlocal active, maximum_active
        with guard:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        with guard:
            active -= 1
        return "ok"

    try:
        for application in applications:
            application.orchestrator.run = instrumented_run
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda application: application.run("objetivo"),
                    applications,
                )
            )
    finally:
        for application in applications:
            application.close()

    assert all(result.success for result in results)
    assert maximum_active == 1


def test_model_planned_file_writer_is_excluded_with_auto_approval_and_no_mutation(
    tmp_path: Path,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gateway = OfflineLegacyGateway("unused")
    gateway.responses = [
        '{"persona":"coder"}',
        '{"plan":[{"tool":"file_writer","args":{"action":"write","file_path":"headless.txt","content":"não aplicar\\n"}}]}',
    ]
    application = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=gateway,
        approval_policy=AutoApprove(),
        configure_logging=False,
    )
    try:
        result = application.run("escreva headless.txt")
        planning_view = application.orchestrator.get_planning_view("linear")

        assert application.orchestrator.current_persona == "coder"
        assert "code_task" in application.orchestrator.active_skills
        assert "file_writer" not in application.orchestrator.active_skills
        assert planning_view is not None
        assert "code_task" in planning_view.presented_names
        assert "file_writer" not in planning_view.presented_names
        assert application.orchestrator.agent_state.tool_history == []
        assert result.status == "blocked"
        assert result.success is False
        assert not (workspace / "headless.txt").exists()
    finally:
        application.close()


def test_unverified_tool_status_reaches_application_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    )
    try:
        def run_unverified(_objective):
            application.orchestrator.agent_state.record_tool_result(
                "code_task",
                {},
                {
                    "ok": False,
                    "done": False,
                    "status": "unverified",
                    "message": "Validação indisponível.",
                },
            )
            return "Validação indisponível."

        monkeypatch.setattr(application.orchestrator, "run", run_unverified)

        result = application.run("modifique e valide")

        assert result.status == "unverified"
        assert result.success is False
    finally:
        application.close()


def test_internal_notes_use_workspace_scratch_not_launch_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    launch_cwd = tmp_path / "launch-cwd"
    workspace.mkdir()
    launch_cwd.mkdir()
    sentinel = launch_cwd / "analysis_notes.md"
    sentinel.write_text("sentinela externa", encoding="utf-8")
    monkeypatch.chdir(launch_cwd)

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        notes_file = application.workspace_paths.scratch_dir / "analysis_notes.md"
        assert application.orchestrator.analysis_notes_file == notes_file

        notes_file.write_text("nota interna antiga", encoding="utf-8")
        application.orchestrator.plan_builder._clear_analysis_notes()

        assert notes_file.read_text(encoding="utf-8") == ""
        assert sentinel.read_text(encoding="utf-8") == "sentinela externa"

        notes_file.write_text("nota interna nova", encoding="utf-8")
        assert application.orchestrator.final_responder._read_notes() == "nota interna nova"
        assert sentinel.read_text(encoding="utf-8") == "sentinela externa"


def test_auto_coder_reads_tests_and_writes_only_inside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    launch_cwd = tmp_path / "launch-cwd"
    workspace.mkdir()
    launch_cwd.mkdir()
    workspace_source = workspace / "sample.py"
    cwd_sentinel = launch_cwd / "sample.py"
    workspace_source.write_text(
        "def target():\n    return 'workspace-original'\n",
        encoding="utf-8",
    )
    sentinel_content = "def sentinel():\n    return 'cwd-sentinel'\n"
    cwd_sentinel.write_text(sentinel_content, encoding="utf-8")
    monkeypatch.chdir(launch_cwd)

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        auto_coder = application.orchestrator.auto_coder
        tested_paths: list[Path] = []

        monkeypatch.setattr(
            auto_coder,
            "generate_tests",
            lambda code, file_path: "assert target() == 'workspace-fixed'",
        )
        monkeypatch.setattr(
            auto_coder,
            "correct_code",
            lambda original, file_path, tests, error: (
                "def target():\n    return 'workspace-fixed'\n"
            ),
        )

        def run_generated_tests(
            file_path: Path,
            code: str,
            test_code: str,
        ) -> tuple[bool, str]:
            del code, test_code
            tested_paths.append(file_path)
            return len(tested_paths) > 1, "falha simulada"

        monkeypatch.setattr(
            auto_coder,
            "_run_generated_tests",
            run_generated_tests,
        )

        assert auto_coder.test_and_correct("sample.py", "corrigir sample.py") is True
        assert tested_paths == [workspace_source.resolve(), workspace_source.resolve()]
        assert (
            workspace_source.read_text(encoding="utf-8")
            == "def target():\n    return 'workspace-fixed'\n"
        )
        assert cwd_sentinel.read_text(encoding="utf-8") == sentinel_content

        with pytest.raises(ValueError, match="fora do workspace"):
            auto_coder.test_and_correct(str(cwd_sentinel), "arquivo externo")


def test_cache_and_summary_hash_the_workspace_file_not_cwd_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _initialized_paths(tmp_path)
    workspace = tmp_path / "workspace"
    launch_cwd = tmp_path / "launch-cwd"
    workspace.mkdir()
    launch_cwd.mkdir()
    workspace_source = workspace / "sample.py"
    cwd_sentinel = launch_cwd / "sample.py"
    workspace_content = "workspace = True\n" * 30
    sentinel_content = "cwd = True\n" * 30
    workspace_source.write_text(workspace_content, encoding="utf-8")
    cwd_sentinel.write_text(sentinel_content, encoding="utf-8")
    workspace_hash = hashlib.sha256(workspace_content.encode("utf-8")).hexdigest()
    monkeypatch.chdir(launch_cwd)

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        configure_logging=False,
    ) as application:
        orchestrator = application.orchestrator
        memory = orchestrator.agent_state.memory.state
        memory.setdefault("file_hashes", {})["sample.py"] = workspace_hash
        memory.setdefault("file_summaries", {})["sample.py"] = "resumo em cache"

        cache_hit, cached = StepPolicies(orchestrator).try_cache(
            "file_reader",
            {"file_path": "sample.py"},
            "sample.py",
        )

        assert cache_hit is True
        assert cached is not None
        assert cached["data"] == "resumo em cache"

        memory["file_hashes"].clear()
        tool_executor = orchestrator.tool_executor
        monkeypatch.setattr(
            tool_executor,
            "summarize_text",
            lambda text, context="": "resumo atualizado",
        )
        tool_executor.maybe_summarize_and_store(
            "file_reader",
            {"file_path": "sample.py"},
            {
                "ok": True,
                "done": True,
                "data": workspace_content,
            },
        )

        assert memory["file_hashes"]["sample.py"] == workspace_hash
        assert cwd_sentinel.read_text(encoding="utf-8") == sentinel_content
