import json
import shutil
from pathlib import Path

from agent.application import AgentApplication
from agent.approval import AutoApprove
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.workspace_extensions_service import WorkspaceExtensionService
from tests.support.offline_scenarios import OfflineLegacyGateway


def test_demo_extension_example_runs_via_stdio(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[3] / "examples" / "extensions" / "demo_extension"
    extension_dir = tmp_path / "external-extension"
    workspace = tmp_path / "workspace"
    extension_dir.mkdir()
    workspace.mkdir()
    shutil.copytree(source, extension_dir, dirs_exist_ok=True)

    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    manifest_path = extension_dir / "manifest.json"
    catalog.add(manifest_path)
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    extensions = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
    extensions.enable("demo.extension")
    extensions.grant("demo.extension", "read")
    extensions.grant("demo.extension", "process")

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        approval_policy=AutoApprove(),
        task_authority_capabilities=["read", "process"],
        configure_logging=False,
    ) as application:
        adapter = application.tool_registry._descriptors_cache["demo_tool"][0]
        assert Path(adapter.manifest.entrypoint[1]) == extension_dir / "demo_extension.py"
        result = application.tool_invocation_gateway.run(
            "demo_tool", {"text": "hello"}
        )

    assert result.status.value == "succeeded"
    assert result.executed is True
    assert result.data == {"echo": "hello"}
    assert result.message is not None and "hello" in result.message
    assert extension_dir != workspace
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["entrypoint"] == [
        "${python}",
        "${extension_dir}/demo_extension.py",
    ]
