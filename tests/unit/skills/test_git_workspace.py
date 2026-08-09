import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.approval import AutoApprove
from agent.skills import git as git_module
from agent.skills.git import GitSkill
from agent.skills.process_environment import confined_process_environment
from agent.skills.process_safety import resolve_trusted_executable
from agent.skills.shell import ShellSkill


def _signed_repo_with_verifier(tmp_path: Path) -> tuple[Path, Path]:
    git = shutil.which("git")
    ssh_keygen = shutil.which("ssh-keygen")
    if git is None or ssh_keygen is None:
        pytest.skip("Git e ssh-keygen sao necessarios para o probe de assinatura")
    repo = tmp_path / "signed-repo"
    repo.mkdir()

    def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [git, *args], cwd=repo, text=True, capture_output=True, check=check
        )

    run_git("init", "-q")
    run_git("config", "user.name", "B4 Test")
    run_git("config", "user.email", "b4@example.invalid")
    key = tmp_path / "signing-key"
    subprocess.run(
        [ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        capture_output=True,
    )
    allowed_signers = tmp_path / "allowed-signers"
    public_key = (tmp_path / "signing-key.pub").read_text(encoding="utf-8").strip()
    allowed_signers.write_text(
        f"b4@example.invalid {public_key}\n", encoding="utf-8"
    )
    run_git("config", "gpg.format", "ssh")
    run_git("config", "user.signingKey", str(key))
    run_git("config", "gpg.ssh.allowedSignersFile", str(allowed_signers))
    (repo / "tracked.txt").write_text("signed\n", encoding="utf-8")
    run_git("add", "tracked.txt")
    run_git("commit", "-q", "-S", "-m", "signed")

    sentinel = tmp_path / "verifier-ran"
    verifier = tmp_path / ("verifier.cmd" if os.name == "nt" else "verifier")
    if os.name == "nt":
        verifier.write_text(
            f'@echo hit>"{sentinel}"\r\n@exit /b 1\r\n', encoding="utf-8"
        )
    else:
        verifier.write_text(
            f'#!/bin/sh\nprintf hit > "{sentinel}"\nexit 1\n', encoding="utf-8"
        )
        verifier.chmod(0o755)
    run_git("config", "gpg.ssh.program", verifier.as_posix())
    run_git("config", "log.showSignature", "false")
    return repo, sentinel


def test_git_log_forces_safe_format_against_workspace_config(tmp_path: Path) -> None:
    repo, sentinel = _signed_repo_with_verifier(tmp_path)
    git = shutil.which("git")
    assert git is not None
    subprocess.run(
        [git, "config", "format.pretty", "format:%G?"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    unsafe = subprocess.run(
        [git, "log", "-1"], cwd=repo, check=False, capture_output=True, text=True
    )
    assert sentinel.exists()
    sentinel.unlink()

    git_result = GitSkill(base_dir=repo).execute({"command": "log", "args": "-1"})
    shell_result = ShellSkill(base_dir=repo, approval_policy=AutoApprove()).execute(
        {"command": "git log -1"}
    )

    assert unsafe.returncode == 0
    assert git_result["ok"] is True
    assert shell_result["ok"] is True
    assert "commit " in str(git_result["data"])
    assert "commit " in str(shell_result["data"])
    assert not sentinel.exists()


@pytest.mark.parametrize("arguments", ["--pretty=attack", "--pretty attack"])
def test_git_log_rejects_workspace_pretty_alias(
    tmp_path: Path, arguments: str
) -> None:
    repo, sentinel = _signed_repo_with_verifier(tmp_path)
    git = shutil.which("git")
    assert git is not None
    subprocess.run(
        [git, "config", "pretty.attack", "format:%G?"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    git_result = GitSkill(base_dir=repo).execute({"command": "log", "args": arguments})
    shell_result = ShellSkill(base_dir=repo, approval_policy=AutoApprove()).execute(
        {"command": f"git log {arguments}"}
    )

    assert git_result["ok"] is False
    assert shell_result["ok"] is False
    assert not sentinel.exists()


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
    assert Path(captured["command"][0]).is_absolute()
    assert not Path(captured["command"][0]).resolve().is_relative_to(tmp_path.resolve())
    assert captured["command"][1:] == [
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "log.showSignature=false",
        "--no-pager",
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
    assert Path(captured["command"][0]).is_absolute()
    assert not Path(captured["command"][0]).resolve().is_relative_to(tmp_path.resolve())
    assert captured["command"][1:] == [
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "log.showSignature=false",
        "--no-pager",
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


@pytest.mark.parametrize(
    "arguments",
    [
        "--show-signature",
        *[
            f"{option}=format:{marker}"
            for option in ("--pretty", "--format")
            for marker in ("%G?", "%GS", "%GK", "%GF", "%GP", "%GT", "%GG")
        ],
    ],
)
def test_git_skill_rejects_signature_verification_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: str,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("git nao deveria executar")))
    result = GitSkill(base_dir=tmp_path).execute({"command": "log", "args": arguments})
    assert result["ok"] is False
    assert "assinatura" in result["error"].casefold()


def test_git_skill_rejects_workspace_shadow_with_real_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sentinel = workspace / "fake-git-ran"
    if os.name == "nt":
        fake = workspace / "git.cmd"
        fake.write_text(f'@echo fake > "{sentinel}"\n@exit /b 42\n', encoding="utf-8")
    else:
        fake = workspace / "git"
        fake.write_text(f"#!/bin/sh\nprintf fake > {shlex.quote(str(sentinel))}\nexit 42\n", encoding="utf-8")
        fake.chmod(0o755)

    def test_environment(current_workspace):
        environment = confined_process_environment(current_workspace)
        environment["PATH"] = os.pathsep.join((str(workspace), environment["PATH"]))
        return environment

    monkeypatch.setattr(git_module, "confined_process_environment", test_environment)
    skill = GitSkill(base_dir=workspace)
    trusted = resolve_trusted_executable("git", test_environment(skill.workspace), workspace)
    if trusted is None:
        pytest.skip("git confiavel nao disponivel no host")
    captured: dict[str, object] = {}
    original_run = git_module.subprocess.run

    def capture_run(command, **kwargs):
        captured["command"] = command
        return original_run(command, **kwargs)

    monkeypatch.setattr(git_module.subprocess, "run", capture_run)
    result = skill.execute({"command": "status"})

    command = captured["command"]
    assert Path(command[0]).is_absolute()
    assert not Path(command[0]).resolve().is_relative_to(workspace.resolve())
    assert not sentinel.exists()
    assert result["error"] != "Exit code 42"
