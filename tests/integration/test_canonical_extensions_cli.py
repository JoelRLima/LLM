from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

import pytest

from agent.application import AgentApplication
from agent.approval import AutoApprove
from agent.interfaces.cli import app as cli
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.tools.contracts import ToolStatus
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.workspace_extensions_service import WorkspaceExtensionService
from tests.support.offline_scenarios import OfflineLegacyGateway


def _manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "id": "demo.extension",
                "version": "1.0.0",
                "protocol_version": "1.0",
                "transport": "stdio",
                "entrypoint": ["python", "demo.py"],
                "timeout_seconds": 5,
                "tools": [
                    {
                        "name": "demo_tool",
                        "description": "demo",
                        "schema": {},
                        "capabilities": ["read", "process"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _run_cli(args: list[str], capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    assert cli.main(args) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    return cast(dict[str, object], json.loads(captured.out))


def _run_cli_failure(args: list[str], capsys: pytest.CaptureFixture[str]) -> int:
    code = cli.main(args)
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["status"] == "failed"
    assert payload["success"] is False
    return code


def test_canonical_extension_cli_uses_modern_catalog_and_workspace_services(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)
    common = ["--home", str(home), "--workspace", str(workspace), "--json"]

    registered = _run_cli(
        ["extensions", "register", str(manifest), *common], capsys
    )
    assert registered["extension_id"] == "demo.extension"
    catalog_path = home / "data" / "extensions" / "catalog.json"
    assert catalog_path.is_file()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert catalog["extensions"]["demo.extension"]["manifest_path"] == manifest.as_posix()
    assert not (home / "data" / "extensions" / "registry.json").exists()

    _run_cli(["extensions", "enable", "demo.extension", *common], capsys)
    granted = _run_cli(
        ["extensions", "grant", "demo.extension", "read", *common], capsys
    )
    assert granted["grants"] == ["read"]
    granted = _run_cli(
        ["extensions", "grant", "demo.extension", "process", *common], capsys
    )
    assert granted["grants"] == ["process", "read"]

    inspected = _run_cli(["extensions", "inspect", *common], capsys)
    assert inspected["extensions"][0]["activation_status"] == "ready"  # type: ignore[index]

    listed = _run_cli(["extensions", "list", *common], capsys)
    extension = listed["extensions"][0]  # type: ignore[index]
    assert extension["workspace"]["enabled"] is True  # type: ignore[index]
    assert extension["workspace"]["grants"] == ["process", "read"]  # type: ignore[index]

    _run_cli(
        ["extensions", "revoke", "demo.extension", "process", *common], capsys
    )
    inspected = _run_cli(["extensions", "inspect", *common], capsys)
    assert inspected["extensions"][0]["activation_status"] == "blocked"  # type: ignore[index]
    _run_cli(["extensions", "disable", "demo.extension", *common], capsys)
    inspected = _run_cli(["extensions", "inspect", *common], capsys)
    assert inspected["extensions"][0]["activation_status"] == "disabled"  # type: ignore[index]


def test_cli_rejects_relative_manifest_without_interpreting_cwd(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _manifest(tmp_path / "manifest.json")
    monkeypatch.chdir(tmp_path)

    code = _run_cli_failure(
        [
            "extensions",
            "register",
            "manifest.json",
            "--home",
            str(home),
            "--workspace",
            str(workspace),
            "--json",
        ],
        capsys,
    )

    assert code != 0
    assert not (home / "data" / "extensions" / "catalog.json").exists()


def test_cli_rejects_home_and_dotdot_without_silent_normalization(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)
    common = ["--home", str(home), "--workspace", str(workspace), "--json"]

    expanded = False

    original_expanduser = Path.expanduser

    def track_expanduser(path: Path) -> Path:
        nonlocal expanded
        if str(path).startswith("~"):
            expanded = True
        return original_expanduser(path)

    monkeypatch.setattr(Path, "expanduser", track_expanduser)
    assert _run_cli_failure(
        ["extensions", "register", "~/manifest.json", *common], capsys
    ) != 0
    assert expanded is False
    lexical_dotdot = tmp_path / "nested" / ".." / "manifest.json"
    assert _run_cli_failure(
        ["extensions", "register", str(lexical_dotdot), *common], capsys
    ) != 0
    assert not (home / "data" / "extensions" / "catalog.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink privilege varies on Windows")
def test_cli_preserves_absolute_symlink_manifest_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real_dir = tmp_path / "real"
    alias_dir = tmp_path / "alias"
    real_dir.mkdir()
    real_manifest = real_dir / "manifest.json"
    _manifest(real_manifest)
    alias_dir.symlink_to(real_dir, target_is_directory=True)
    alias_manifest = alias_dir / "manifest.json"

    _run_cli(
        [
            "extensions",
            "register",
            str(alias_manifest),
            "--home",
            str(home),
            "--workspace",
            str(workspace),
            "--json",
        ],
        capsys,
    )

    catalog = json.loads(
        (home / "data" / "extensions" / "catalog.json").read_text(encoding="utf-8")
    )
    assert catalog["extensions"]["demo.extension"]["manifest_path"] == alias_manifest.as_posix()


def test_product_task_authority_is_bound_to_current_application_snapshot(
    tmp_path: Path,
) -> None:
    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        task_authority_capabilities=["read"],
        configure_logging=False,
    ) as application:
        assert application.task_authority is not None
        assert application.application_authority is not None
        assert application.task_authority.allowed_capabilities == frozenset({"read"})
        assert application.task_authority.policy_source == "cli.task_authority"
        assert application.task_authority.runtime_identity == application.application_authority.runtime_identity


def test_product_authority_and_yes_remain_separate_before_real_stdio_effect(
    tmp_path: Path,
) -> None:
    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    extension_dir = tmp_path / "extension"
    extension_dir.mkdir()
    marker = tmp_path / "spawned.txt"
    (extension_dir / "tool.py").write_text(
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('spawned', encoding='utf-8')\n"
        "payload = json.loads(sys.stdin.read())\n"
        "print(json.dumps({'invocation_id': payload['invocation_id'], 'status': 'succeeded', 'message': 'ok'}), flush=True)\n",
        encoding="utf-8",
    )
    manifest = extension_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "id": "demo.extension",
                "version": "1.0.0",
                "protocol_version": "1.0",
                "transport": "stdio",
                "entrypoint": ["${python}", "${extension_dir}/tool.py"],
                "timeout_seconds": 5,
                "tools": [{"name": "demo_tool", "schema": {}, "capabilities": ["read", "process"]}],
            }
        ),
        encoding="utf-8",
    )
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    service = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
    service.enable("demo.extension")
    service.grant("demo.extension", "read")
    service.grant("demo.extension", "process")

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        approval_policy=AutoApprove(),
        configure_logging=False,
    ) as application:
        denied = application.tool_invocation_gateway.run("demo_tool", {})
        assert denied.status is ToolStatus.PERMISSION_DENIED
        assert denied.error is not None
        assert denied.error.code == "TASK_AUTHORITY_MISSING"
    assert not marker.exists()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        approval_policy=AutoApprove(),
        task_authority_capabilities=["read"],
        configure_logging=False,
    ) as application:
        denied = application.tool_invocation_gateway.run("demo_tool", {})
        assert denied.status is ToolStatus.PERMISSION_DENIED
        assert denied.error is not None
        assert denied.error.code == "TASK_AUTHORITY_DENIED"
    assert not marker.exists()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        task_authority_capabilities=["read", "process"],
        configure_logging=False,
    ) as application:
        blocked = application.tool_invocation_gateway.run("demo_tool", {})
        assert blocked.status is ToolStatus.BLOCKED
        assert blocked.error is not None
        assert blocked.error.code == "APPROVAL_REQUIRED"
    assert not marker.exists()

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=OfflineLegacyGateway("unused"),
        approval_policy=AutoApprove(),
        task_authority_capabilities=["read", "process"],
        configure_logging=False,
    ) as application:
        succeeded = application.tool_invocation_gateway.run("demo_tool", {})
        assert succeeded.status is ToolStatus.SUCCEEDED
    assert marker.read_text(encoding="utf-8") == "spawned"
