from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.w23_diagnostics import entry, inputs
from scripts.w23_diagnostics.entry import (
    _installed_workflow_path,
    _require_only_installed_workflow_change,
)
from scripts.w23_diagnostics.process import StageError


def test_installed_workflow_is_derived_from_contract_path_keys() -> None:
    files = {
        "README.md": "base-readme",
        ".github/workflows/ci.yml": "base-ci",
        ".github/workflows/candidate-installed-product.yml": "base-workflow",
    }

    assert _installed_workflow_path(files) == ".github/workflows/candidate-installed-product.yml"


@pytest.mark.parametrize(
    "files",
    [
        {"README.md": "base", "scripts/installed-product.yml": "outside"},
        {"README.md": "base", ".github/workflows/nested/installed-product.yml": "nested"},
        {"README.md": "base"},
    ],
)
def test_installed_workflow_rejects_zero_eligible_contract_paths(files: dict[str, str]) -> None:
    with pytest.raises(StageError, match="FAIL"):
        _installed_workflow_path(files)


def test_installed_workflow_rejects_multiple_eligible_contract_paths() -> None:
    files = {
        ".github/workflows/first-installed-product.yml": "one",
        ".github/workflows/second-installed-product.yml": "two",
    }

    with pytest.raises(StageError, match="FAIL"):
        _installed_workflow_path(files)


def test_exact_workflow_change_is_allowed_but_additional_drift_is_rejected() -> None:
    expected = {
        "README.md": "base-readme",
        ".github/workflows/candidate-installed-product.yml": "base-workflow",
    }
    actual = {
        "README.md": "base-readme",
        ".github/workflows/candidate-installed-product.yml": "patched-workflow",
    }

    _require_only_installed_workflow_change(expected, actual)

    actual["README.md"] = "unexpected-change"
    with pytest.raises(StageError, match="FAIL"):
        _require_only_installed_workflow_change(expected, actual)

    actual["unexpected.bin"] = "uncontracted-file"
    with pytest.raises(StageError, match="FAIL"):
        _require_only_installed_workflow_change(expected, actual)


class _RealGitCommands:
    """Run real Git against the temporary repository used by identity tests."""

    def run(self, _label: str, command: list[str], cwd: Path, env: dict[str, str], _seconds: int) -> SimpleNamespace:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
        return SimpleNamespace(status="PASS" if result.returncode == 0 else "FAIL", stdout=result.stdout)


def _execution_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, str, str]:
    root = tmp_path / "tooling"
    assets = root / "quality/w23/diagnostics"
    assets.mkdir(parents=True)
    source_assets = inputs.ASSETS
    for name in ("inputs.json", "r02.patch"):
        (assets / name).write_bytes((source_assets / name).read_bytes())
    source = root / "scripts/w23_diagnostics"
    source.mkdir(parents=True)
    (source / "entry.py").write_bytes(b"candidate execution source\n")

    monkeypatch.setattr(inputs, "TOOL_ROOT", root)
    monkeypatch.setattr(inputs, "ASSETS", assets)
    monkeypatch.setattr(entry, "TOOL_ROOT", root)

    commands = [
        ["git", "init", "--quiet", str(root)],
        ["git", "-C", str(root), "config", "core.autocrlf", "false"],
        ["git", "-C", str(root), "config", "core.hooksPath", str(root / ".git/w23-test-hooks")],
        ["git", "-C", str(root), "config", "commit.gpgsign", "false"],
        ["git", "-C", str(root), "config", "user.name", "W23 disposable test"],
        ["git", "-C", str(root), "config", "user.email", "w23-test@example.invalid"],
        ["git", "-C", str(root), "add", "--all"],
        ["git", "-C", str(root), "commit", "--quiet", "-m", "historical H fixture"],
    ]
    for command in commands:
        subprocess.run(command, cwd=root, check=True, capture_output=True)
    (root / "candidate-only.py").write_bytes(b"successor file absent from historical H\n")
    subprocess.run(["git", "-C", str(root), "add", "candidate-only.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "--quiet", "-m", "successor execution fixture"],
                   check=True, capture_output=True)
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                            capture_output=True, text=True).stdout.strip()
    tree = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], check=True,
                          capture_output=True, text=True).stdout.strip()
    parent = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD^"], check=True,
                            capture_output=True, text=True).stdout.strip()
    return root, commit, tree, parent


def test_tool_identity_binds_execution_pin_without_applying_h_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, commit, tree, parent = _execution_repo(tmp_path, monkeypatch)
    monkeypatch.setenv("W23_EXPECTED_TOOLING_SHA", commit)
    work = tmp_path / "evidence"

    actual = entry.tool_identity(work, _RealGitCommands())  # type: ignore[arg-type]

    assert "candidate-only.py" in actual
    selection = json.loads((work / "publish/selection.json").read_text(encoding="utf-8"))
    assert selection["implementation_sha"] == commit
    assert selection["implementation_tree"] == tree
    assert selection["implementation_parents"] == [parent]
    assert selection["identity_scope"] == "EXECUTION_PIN_ONLY_NO_AUTHORITY"
    assert selection["inventory_file_count"] == len(actual)
    assert "selection" not in selection and "activation" not in selection


@pytest.mark.parametrize("mutation", ["wrong_pin", "tracked_change", "extra_file", "ignored_extra"])
def test_tool_identity_rejects_unpinned_or_dirty_execution_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    root, commit, _tree, _parent = _execution_repo(tmp_path, monkeypatch)
    expected = "0" * 40 if mutation == "wrong_pin" else commit
    monkeypatch.setenv("W23_EXPECTED_TOOLING_SHA", expected)
    if mutation == "tracked_change":
        (root / "candidate-only.py").write_bytes(b"changed after commit\n")
    elif mutation == "extra_file":
        (root / "extra.py").write_bytes(b"untracked extra\n")
    elif mutation == "ignored_extra":
        (root / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", ".gitignore"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "commit", "--quiet", "-m", "ignore fixture"], check=True,
                       capture_output=True)
        expected = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                                  capture_output=True, text=True).stdout.strip()
        monkeypatch.setenv("W23_EXPECTED_TOOLING_SHA", expected)
        (root / "ignored.ignored").write_bytes(b"ignored extra\n")

    with pytest.raises(StageError, match="FAIL"):
        entry.tool_identity(tmp_path / "evidence", _RealGitCommands())  # type: ignore[arg-type]
