import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.approval import AutoApprove, RequireExplicitApproval
from agent.skills.shell import ShellSkill, _is_command_allowed, _split_command


def test_shell_allows_read_only_validation_commands():
    assert _is_command_allowed(_split_command("pytest -q"))
    assert _is_command_allowed(_split_command("git diff --stat"))


def test_shell_blocks_arbitrary_runtimes_and_mutating_git():
    assert not _is_command_allowed(_split_command("python -c print(1)"))
    assert not _is_command_allowed(_split_command("node script.js"))
    assert not _is_command_allowed(_split_command("pip install package"))
    assert not _is_command_allowed(_split_command("git commit -m test"))


def _forbid_subprocess(*args, **kwargs):
    raise AssertionError(f"subprocess não deveria executar: {args!r} {kwargs!r}")


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "diff", "--no-index", "inside.py", "{sentinel}"],
        ["pytest", "{sentinel}"],
        ["pytest", "@{sentinel}"],
        ["ruff", "check", "{sentinel}"],
        ["ruff", "check", "--config={sentinel}", "."],
        ["mypy", "--config-file", "{sentinel}", "."],
        ["type", "{sentinel}"],
    ],
)
def test_shell_rejects_external_reads_without_exposing_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "inside.py").write_text("value = 1\n", encoding="utf-8")
    sentinel = tmp_path / "outside-secret.txt"
    secret = "SEGREDO-NAO-EXPOR"
    sentinel.write_text(secret, encoding="utf-8")
    rendered = [part.format(sentinel=sentinel) for part in argv]
    monkeypatch.setattr(subprocess, "run", _forbid_subprocess)

    result = ShellSkill(
        base_dir=workspace,
        approval_policy=AutoApprove(),
    ).execute({"command": shlex.join(rendered)})

    assert result["ok"] is False
    assert "workspace" in result["error"].casefold()
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert sentinel.read_text(encoding="utf-8") == secret


@pytest.mark.parametrize(
    "argv",
    [
        ["tree", "-o", "{sentinel}", "."],
        ["tree", "-o{sentinel}", "."],
        ["ruff", "format", "{sentinel}"],
        ["pytest", "--basetemp={sentinel}", "."],
        [
            "git",
            "diff",
            "--no-index",
            "--output={sentinel}",
            "first.txt",
            "second.txt",
        ],
    ],
)
def test_shell_rejects_external_mutation_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "first.txt").write_text("first\n", encoding="utf-8")
    (workspace / "second.txt").write_text("second\n", encoding="utf-8")
    sentinel = tmp_path / "outside-secret.txt"
    sentinel.write_text("preservar\n", encoding="utf-8")
    rendered = [part.format(sentinel=sentinel) for part in argv]
    monkeypatch.setattr(subprocess, "run", _forbid_subprocess)

    result = ShellSkill(
        base_dir=workspace,
        approval_policy=AutoApprove(),
    ).execute({"command": shlex.join(rendered)})

    assert result["ok"] is False
    assert sentinel.read_text(encoding="utf-8") == "preservar\n"


def test_shell_rejects_symlink_that_resolves_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sentinel = tmp_path / "external-secret.txt"
    sentinel.write_text("segredo por symlink\n", encoding="utf-8")
    link = workspace / "linked-secret.txt"
    try:
        link.symlink_to(sentinel)
    except OSError as exc:
        pytest.skip(f"symlink indisponível: {exc}")
    monkeypatch.setattr(subprocess, "run", _forbid_subprocess)

    result = ShellSkill(base_dir=workspace).execute(
        {"command": "type linked-secret.txt"}
    )

    assert result["ok"] is False
    assert "workspace" in result["error"].casefold()
    assert "segredo por symlink" not in json.dumps(result, ensure_ascii=False)
    assert sentinel.read_text(encoding="utf-8") == "segredo por symlink\n"


def test_shell_runs_normal_read_only_command_with_confined_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="clean\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path.parent))
    sentinel = tmp_path.parent / "git-trace-sentinel.txt"
    sentinel.write_text("preservar\n", encoding="utf-8")
    monkeypatch.setenv("GIT_TRACE", str(sentinel))
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(sentinel))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.pager")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(sentinel))
    skill = ShellSkill(base_dir=tmp_path)

    result = skill.execute({"command": "git status"})

    assert result["ok"] is True
    assert captured["command"] == [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "status",
    ]
    assert captured["shell"] is False
    assert captured["stdin"] is subprocess.DEVNULL
    assert Path(captured["cwd"]) == tmp_path.resolve()
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["HOME"] == str(tmp_path.resolve())
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "PYTHONPATH" not in environment
    assert "GIT_TRACE" not in environment
    assert "GIT_TRACE2_EVENT" not in environment
    assert "GIT_CONFIG_COUNT" not in environment
    assert "GIT_CONFIG_KEY_0" not in environment
    assert "GIT_CONFIG_VALUE_0" not in environment
    assert sentinel.read_text(encoding="utf-8") == "preservar\n"


@pytest.mark.parametrize("command", ["pytest -q", "ruff check .", "mypy ."])
def test_shell_requires_approval_for_workspace_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    monkeypatch.setattr(subprocess, "run", _forbid_subprocess)
    skill = ShellSkill(
        base_dir=tmp_path,
        approval_policy=RequireExplicitApproval(),
    )

    result = skill.execute({"command": command})

    assert result["status"] == "blocked"
    assert result["error"] == "confirmation_required"


def test_shell_runs_approved_tests_without_cache_or_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="1 passed\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    skill = ShellSkill(base_dir=tmp_path, approval_policy=AutoApprove())

    result = skill.execute({"command": "pytest -q tests"})

    assert result["ok"] is True
    assert captured["command"] == [
        "pytest",
        "-q",
        "tests",
        "-p",
        "no:cacheprovider",
    ]
    assert captured["stdin"] is subprocess.DEVNULL


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ruff check .", ["ruff", "check", ".", "--no-cache"]),
        ("mypy .", ["mypy", ".", "--no-incremental"]),
    ],
)
def test_shell_runs_approved_workspace_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    expected: list[str],
) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["command"] = argv
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    skill = ShellSkill(base_dir=tmp_path, approval_policy=AutoApprove())

    result = skill.execute({"command": command})

    assert result["ok"] is True
    assert captured["command"] == expected
