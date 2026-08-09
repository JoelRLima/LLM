import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.approval import AutoApprove
from agent.skills import git as git_module
from agent.skills.git import GitSkill
from agent.skills.process_environment import confined_process_environment
from agent.skills.process_safety import local_history_arguments, resolve_trusted_executable
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


@pytest.mark.parametrize(
    "arguments",
    ["-0", "-n0", "--max-count=0", "-n10", "-" + chr(0x0661), "-1001", "-1 -n 2", "-- path.txt"],
)
def test_git_history_count_grammar_rejects_noncanonical_forms(arguments: str) -> None:
    assert local_history_arguments(["git", "log", *shlex.split(arguments)]) is None


@pytest.mark.parametrize("arguments", ["-1", "-n 1", "--max-count 1000", "--max-count=1000"])
def test_git_history_count_grammar_accepts_published_forms(arguments: str) -> None:
    assert local_history_arguments(["git", "log", *shlex.split(arguments)]) is not None


@pytest.mark.parametrize(
    "arguments",
    [
        "-p -1",
        "--patch -1",
        "--remerge-diff -1",
        "--diff-merges=remerge -1",
        "--stat -1",
        "--pretty=oneline",
        "-- HEAD",
    ],
)
def test_model_actionable_git_history_is_positive_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: str
) -> None:
    monkeypatch.setattr(
        "agent.skills.git.run_bounded_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Git nao deveria executar argumento fora da allowlist")
        ),
    )
    monkeypatch.setattr(
        "agent.skills.shell._run_bounded_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Git nao deveria executar argumento fora da allowlist")
        ),
    )

    git_result = GitSkill(base_dir=tmp_path).execute(
        {"command": "log", "args": arguments}
    )
    shell_result = ShellSkill(base_dir=tmp_path, approval_policy=AutoApprove()).execute(
        {"command": f"git log {arguments}"}
    )

    assert git_result["ok"] is False
    assert shell_result["ok"] is False


def test_git_log_remerge_driver_is_rejected_before_execution(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git necessario para o probe de merge driver")
    repo = tmp_path / "merge-repo"
    repo.mkdir()
    sentinel = tmp_path / "merge-driver-ran"

    def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [git, *args], cwd=repo, text=True, capture_output=True, check=check
        )

    run_git("init", "-q")
    run_git("config", "user.name", "Merge Driver Test")
    run_git("config", "user.email", "merge@example.invalid")
    (repo / "value.txt").write_text("base\n", encoding="utf-8")
    run_git("add", "value.txt")
    run_git("commit", "-qm", "base")
    run_git("checkout", "-qb", "side")
    (repo / "value.txt").write_text("side\n", encoding="utf-8")
    run_git("commit", "-qam", "side")
    run_git("checkout", "-qb", "mainline", "HEAD~1")
    (repo / "value.txt").write_text("main\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("value.txt merge=sentinel\n", encoding="utf-8")
    run_git("add", "value.txt", ".gitattributes")
    run_git("commit", "-qm", "mainline")
    helper = tmp_path / "merge_driver.py"
    helper.write_text(
        "import pathlib, shutil, sys\n"
        f"pathlib.Path({str(sentinel)!r}).write_text('hit', encoding='utf-8')\n"
        "shutil.copyfile(sys.argv[1], sys.argv[2])\n",
        encoding="utf-8",
    )
    driver = f'"{sys.executable}" "{helper}" %O %A %B %L'
    run_git("config", "merge.sentinel.driver", driver)
    run_git("merge", "--no-ff", "side", "-m", "merge")
    sentinel.unlink(missing_ok=True)

    unsafe = run_git("log", "--remerge-diff", "-1", check=False)
    assert unsafe.returncode == 0
    assert sentinel.exists()
    sentinel.unlink()

    git_result = GitSkill(base_dir=repo).execute(
        {"command": "log", "args": "--remerge-diff -1"}
    )
    shell_result = ShellSkill(base_dir=repo, approval_policy=AutoApprove()).execute(
        {"command": "git log --remerge-diff -1"}
    )
    assert git_result["ok"] is False
    assert shell_result["ok"] is False
    assert not sentinel.exists()


@pytest.mark.skipif(os.name == "nt", reason="remote helper probe uses POSIX executable lookup")
def test_git_log_disables_promisor_lazy_fetch_and_remote_helper(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git necessario para o probe de promisor")
    repo = tmp_path / "promisor-repo"
    repo.mkdir()
    sentinel = tmp_path / "remote-helper-ran"
    helper_dir = tmp_path / "helpers"
    helper_dir.mkdir()
    helper = helper_dir / "git-remote-sentinel"
    helper.write_text(
        f"#!/bin/sh\nprintf hit > {shlex.quote(str(sentinel))}\nexit 1\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join((str(helper_dir), env.get("PATH", "")))

    def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [git, *args], cwd=repo, text=True, capture_output=True, check=check, env=env
        )

    run_git("init", "-q")
    run_git("config", "user.name", "Promisor Test")
    run_git("config", "user.email", "promisor@example.invalid")
    (repo / "payload.txt").write_text("payload\n", encoding="utf-8")
    run_git("add", "payload.txt")
    run_git("commit", "-qm", "payload")
    blob = run_git("rev-parse", "HEAD:payload.txt").stdout.strip()
    blob_path = repo / ".git" / "objects" / blob[:2] / blob[2:]
    assert blob_path.exists()
    blob_path.unlink()
    run_git("remote", "add", "origin", "sentinel::origin")
    run_git("config", "remote.origin.promisor", "true")
    run_git("config", "remote.origin.partialclonefilter", "blob:none")

    unsafe = run_git("log", "-p", "-1", check=False)
    assert unsafe.returncode != 0
    assert sentinel.exists()
    sentinel.unlink()

    safe = subprocess.run(
        [git, "--no-lazy-fetch", "log", "-p", "-1"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert safe.returncode != 0
    assert not sentinel.exists()

    result = GitSkill(base_dir=repo).execute({"command": "log", "args": "-p -1"})
    assert result["ok"] is False
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

    monkeypatch.setattr("agent.skills.git.run_bounded_process", fake_run)
    skill = GitSkill(base_dir=tmp_path)

    result = skill.execute({"command": "log"})

    assert result["ok"] is True
    assert captured["command"][1:] == [
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "log.showSignature=false",
        "-c",
        "log.diffMerges=off",
        "--no-lazy-fetch",
        "--no-pager",
        "log",
        "--no-patch",
        "--pretty=medium",
    ]
    assert captured["timeout"] == 20
    assert Path(captured["workspace"]) == tmp_path.resolve()
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
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
    if command == "log":
        assert "max-count" in result["error"].casefold()
    else:
        assert "log" in result["error"].casefold()
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
    assert "log" in result["error"].casefold()
    assert sentinel.read_text(encoding="utf-8") == "preservar\n"


def test_git_skill_accepts_log_and_disables_extension_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent.skills.git.run_bounded_process", fake_run)
    result = GitSkill(base_dir=tmp_path).execute(
        {
            "command": "log",
            "args": "-1",
        }
    )

    assert result["ok"] is True
    assert captured["command"][1:] == [
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "log.showSignature=false",
        "-c",
        "log.diffMerges=off",
        "--no-lazy-fetch",
        "--no-pager",
        "log",
        "--no-patch",
        "--pretty=medium",
        "--max-count",
        "1",
    ]
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["HOME"] == str(tmp_path.resolve())


def test_git_skill_reuses_bounded_runner_with_cancellation_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent.skills.git.run_bounded_process", fake_run)
    token = object()
    event = object()
    result = GitSkill(base_dir=tmp_path).execute_with_context(
        {"command": "log", "args": "-1"},
        cancellation_token=token,
        cancellation_event=event,
    )

    assert result["ok"] is True
    assert captured["cancellation_token"] is token
    assert captured["cancellation_event"] is event
    assert captured["timeout"] == 20


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
    assert "max-count" in result["error"].casefold()


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
    result = skill.execute({"command": "log"})

    assert not sentinel.exists()
    assert result["error"] != "Exit code 42"


def test_git_skill_rejects_workspace_indirection_to_external_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git = shutil.which("git")
    if git is None:
        pytest.skip("git confiavel nao disponivel no host")
    subprocess.run([git, "init", "-q"], cwd=workspace, check=True)
    subprocess.run([git, "config", "user.name", "Provenance Test"], cwd=workspace, check=True)
    subprocess.run(
        [git, "config", "user.email", "provenance@example.invalid"],
        cwd=workspace,
        check=True,
    )
    (workspace / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run([git, "add", "tracked.txt"], cwd=workspace, check=True)
    subprocess.run([git, "commit", "-qm", "initial"], cwd=workspace, check=True)
    bin_dir = workspace / "bin"
    bin_dir.mkdir()
    link = bin_dir / ("git.exe" if os.name == "nt" else "git")
    try:
        link.symlink_to(Path(sys.executable))
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    def test_environment(current_workspace):
        environment = confined_process_environment(current_workspace)
        environment["PATH"] = os.pathsep.join((str(bin_dir), str(Path(git).parent)))
        environment.setdefault("PATHEXT", ".COM;.EXE;.BAT;.CMD")
        return environment

    monkeypatch.setattr(git_module, "confined_process_environment", test_environment)
    result = GitSkill(base_dir=workspace).execute({"command": "log"})

    assert result["ok"] is True


@pytest.mark.parametrize(
    ("surface", "invocation"),
    [
        ("git", {"command": "status"}),
        ("git", {"command": "diff"}),
        ("git", {"command": "diff", "args": "--cached"}),
        ("git", {"command": "diff", "args": "--staged tracked.txt"}),
        ("shell", {"command": "git status"}),
        ("shell", {"command": "git diff"}),
        ("shell", {"command": "git diff --cached"}),
        ("shell", {"command": "git diff --staged tracked.txt"}),
    ],
)
def test_model_actionable_git_status_diff_reject_before_content_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    invocation: dict[str, str],
) -> None:
    repo = tmp_path / "filter-repo"
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True)
    sentinel = tmp_path / "filter-ran"
    helper = tmp_path / "filter_helper.py"
    helper.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(sentinel)!r}).write_text('hit', encoding='utf-8')\n"
        "sys.stdout.buffer.write(sys.stdin.buffer.read())\n",
        encoding="utf-8",
    )
    (repo / ".gitattributes").write_text(
        "*.txt filter=sentinel\n", encoding="utf-8"
    )
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n"
        "[filter \"sentinel\"]\n"
        f"\tclean = {shlex.join([sys.executable, str(helper)])}\n"
        f"\tprocess = {shlex.join([sys.executable, str(helper)])}\n"
        f"\tsmudge = {shlex.join([sys.executable, str(helper)])}\n",
        encoding="utf-8",
    )

    def forbidden_process(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"Git should be rejected before execution: {args!r} {kwargs!r}")

    if surface == "git":
        monkeypatch.setattr(git_module.subprocess, "run", forbidden_process)
        result = GitSkill(base_dir=repo).execute(invocation)
    else:
        monkeypatch.setattr("agent.skills.shell._run_bounded_process", forbidden_process)
        result = ShellSkill(base_dir=repo, approval_policy=AutoApprove()).execute(invocation)

    assert result["ok"] is False
    assert "log" in str(result["error"]).casefold()
    assert not sentinel.exists()
