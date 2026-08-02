import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.skills.git import GitSkill


def test_git_skill_executes_in_injected_workspace(tmp_path, monkeypatch):
    captured = {}
    sentinel = tmp_path.parent / "git-trace.txt"
    sentinel.write_text("preservar\n", encoding="utf-8")
    monkeypatch.setenv("GIT_TRACE", str(sentinel))
    monkeypatch.setenv("GIT_TRACE2_PERF", str(sentinel))

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="clean\n", stderr="")

    monkeypatch.setattr("agent.skills.git.subprocess.run", fake_run)
    skill = GitSkill(base_dir=tmp_path)

    result = skill.execute({"command": "status"})

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
    assert captured["timeout"] == 20
    assert Path(captured["cwd"]) == tmp_path.resolve()
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "GIT_TRACE" not in environment
    assert "GIT_TRACE2_PERF" not in environment
    assert sentinel.read_text(encoding="utf-8") == "preservar\n"


@pytest.mark.parametrize(
    ("command", "arguments"),
    [
        ("diff", ["--no-index", "inside.txt", "{sentinel}"]),
        ("diff", ["--pathspec-from-file={sentinel}"]),
        ("log", ["-O{sentinel}"]),
    ],
)
def test_git_skill_rejects_external_reads_without_exposing_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    arguments: list[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "inside.txt").write_text("inside\n", encoding="utf-8")
    sentinel = tmp_path / "external-secret.txt"
    secret = "SEGREDO-GIT-NAO-EXPOR"
    sentinel.write_text(secret, encoding="utf-8")

    def forbidden_run(*args, **kwargs):
        raise AssertionError(f"git não deveria executar: {args!r} {kwargs!r}")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    rendered = [part.format(sentinel=sentinel) for part in arguments]

    result = GitSkill(base_dir=workspace).execute(
        {"command": command, "args": shlex.join(rendered)}
    )

    assert result["ok"] is False
    assert "workspace" in result["error"].casefold()
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert sentinel.read_text(encoding="utf-8") == secret


def test_git_skill_rejects_external_output_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = workspace / "first.txt"
    second = workspace / "second.txt"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    sentinel = tmp_path / "external-secret.txt"
    sentinel.write_text("preservar\n", encoding="utf-8")

    def forbidden_run(*args, **kwargs):
        raise AssertionError(f"git não deveria executar: {args!r} {kwargs!r}")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    extra = shlex.join(
        [
            "--no-index",
            f"--output={sentinel}",
            str(first),
            str(second),
        ]
    )

    result = GitSkill(base_dir=workspace).execute(
        {"command": "diff", "args": extra}
    )

    assert result["ok"] is False
    assert "somente leitura" in result["error"]
    assert sentinel.read_text(encoding="utf-8") == "preservar\n"


def test_git_skill_accepts_inside_paths_and_disables_extension_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = GitSkill(base_dir=tmp_path).execute(
        {
            "command": "diff",
            "args": shlex.join(["--no-index", "first.txt", "second.txt"]),
        }
    )

    assert result["ok"] is True
    assert captured["command"] == [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-index",
        "first.txt",
        "second.txt",
    ]
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["HOME"] == str(tmp_path.resolve())
