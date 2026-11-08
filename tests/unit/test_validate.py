from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import validate


def test_profiles_select_expected_gates_in_order() -> None:
    assert [gate.label for gate in validate.selected_groups("static")[0].gates] == [
        "Ruff",
        "Repository quality policy",
        "Mypy Linux",
        "Mypy Windows",
        "Git diff check",
    ]
    assert [group.name for group in validate.selected_groups("all")] == ["static", "tests", "installed"]


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown validation profile"):
        validate.selected_groups("typo")


def test_failure_propagates_and_later_gates_continue() -> None:
    calls: list[tuple[tuple[str, ...], Path]] = []

    def execute(command: tuple[str, ...], *, cwd: Path, check: bool) -> subprocess.CompletedProcess[object]:
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 1 if len(calls) == 1 else 0)

    messages: list[str] = []
    assert validate.run_profile("static", repo=Path("repo-root"), execute=execute, emit=messages.append) == 1
    assert len(calls) == 5
    assert all(cwd == Path("repo-root") for _, cwd in calls)
    assert "4/5 gates passed" in messages[-1]


def test_oserror_is_reported_as_failure_and_execution_continues() -> None:
    calls = 0

    def execute(command: tuple[str, ...], *, cwd: Path, check: bool) -> subprocess.CompletedProcess[object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("unavailable")
        return subprocess.CompletedProcess(command, 0)

    messages: list[str] = []
    assert validate.run_profile("all", execute=execute, emit=messages.append) == 1
    assert calls == 7
    assert "code 127" in messages[1]
    assert "6/7 gates passed" in messages[-1]


def test_plan_prints_commands_without_running() -> None:
    def unexpected(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        raise AssertionError("plan must not execute processes")

    messages: list[str] = []
    assert validate.run_profile("installed", plan=True, execute=unexpected, emit=messages.append) == 0
    assert len(messages) == 1
    assert "verify_installed_package.py" in messages[0]


def test_default_working_directory_is_repository_root() -> None:
    cwd_values: list[Path] = []

    def execute(command: tuple[str, ...], *, cwd: Path, check: bool) -> subprocess.CompletedProcess[object]:
        cwd_values.append(cwd)
        return subprocess.CompletedProcess(command, 0)

    assert validate.run_profile("tests", execute=execute, emit=lambda _: None) == 0
    assert cwd_values == [validate.ROOT]


def test_all_success_runs_seven_gates() -> None:
    calls: list[tuple[str, ...]] = []

    def execute(command: tuple[str, ...], *, cwd: Path, check: bool) -> subprocess.CompletedProcess[object]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    assert validate.run_profile("all", execute=execute, emit=lambda _: None) == 0
    assert len(calls) == 7


def test_cli_plan_works_from_another_directory() -> None:
    script = validate.ROOT / "scripts" / "validate.py"
    result = subprocess.run(
        [sys.executable, str(script), "all", "--plan"],
        cwd=script.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "[static] Ruff:" in result.stdout
    assert "[tests] Pytest:" in result.stdout
    assert "[installed] Installed package:" in result.stdout
