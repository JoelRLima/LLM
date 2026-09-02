from pathlib import Path

import pytest

from agent.code.validation import ValidationReport, ValidationStatus
from agent.workspace import WorkspaceManager


def test_restore_and_rollback_are_scoped_to_injected_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    restore_dir = tmp_path / "state" / "restore"
    target = workspace / "src" / "sample.py"
    target.parent.mkdir(parents=True)
    target.write_text("value = 1\n", encoding="utf-8")
    manager = WorkspaceManager(
        workspace_root=workspace,
        restore_points_dir=restore_dir,
        validation_config={},
    )

    manager.create_restore_point(
        [{"tool": "file_writer", "args": {"file_path": "src/sample.py"}}]
    )
    backup = Path(manager.restore_points[0]["backup"])
    backup.relative_to(restore_dir.resolve())
    assert backup.read_text(encoding="utf-8") == "value = 1\n"
    target.write_text("value = 2\n", encoding="utf-8")
    manager.rollback()

    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert not backup.exists()


def test_workspace_manager_rejects_paths_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = WorkspaceManager(
        workspace_root=workspace,
        restore_points_dir=tmp_path / "restore",
    )

    with pytest.raises(ValueError, match="fora do workspace"):
        manager.create_restore_point(
            [{"tool": "file_writer", "args": {"file_path": "../outside.py"}}]
        )
    with pytest.raises(ValueError, match="fora do workspace"):
        manager.lint_check("../outside.py")


def test_workspace_manager_expands_user_shorthand_before_confinement(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = WorkspaceManager(workspace_root=workspace, restore_points_dir=tmp_path / "restore")
    expanded = tmp_path / "home" / "outside.py"
    original_expanduser = Path.expanduser

    def expanduser(path: Path) -> Path:
        if str(path) == "~/outside.py":
            return expanded
        return original_expanduser(path)

    monkeypatch.setattr(Path, "expanduser", expanduser)

    with pytest.raises(ValueError, match="fora do workspace"):
        manager.resolve_path("~/outside.py")


def test_validation_delegates_to_canonical_service(tmp_path):
    workspace = tmp_path / "workspace"
    source = workspace / "src" / "sample.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n", encoding="utf-8")
    calls = []

    class FakeValidationService:
        def validate(self, project, changed_files, *, include_tests):
            calls.append((project, changed_files, include_tests))
            return ValidationReport(ValidationStatus.PASSED, ())

    manager = WorkspaceManager(
        workspace_root=workspace,
        restore_points_dir=tmp_path / "restore",
        validation_config={"ruff": True},
        validation_service=FakeValidationService(),
    )

    result = manager.lint_check("src/sample.py")

    assert result == ""
    assert calls[0][1] == ["src/sample.py"]
    assert calls[0][2] is False


def test_pytest_target_is_confined_to_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    source = workspace / "sample.py"
    workspace.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    manager = WorkspaceManager(
        workspace_root=workspace,
        restore_points_dir=tmp_path / "restore",
        validation_config={"pytest": True, "pytest_dir": "../outside"},
    )

    with pytest.raises(ValueError, match="fora do workspace"):
        manager.lint_check("sample.py")
