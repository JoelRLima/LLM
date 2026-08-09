import json
import os
import select
import shlex
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.approval import AutoApprove, RequireExplicitApproval
from agent.runtime.workspace_context import WorkspaceContext
from agent.skills import shell_process as shell_process_module
from agent.skills.process_environment import confined_process_environment
from agent.skills.shell import ShellSkill, _is_command_allowed, _split_command
from agent.skills.shell_process import (
    ShellProcessError,
    _resolve_executable,
    run_bounded_process,
)


def test_shell_allows_only_the_reduced_read_only_surface() -> None:
    for command in ("ruff check .", "git status", "git log", "git diff", "tree ."):
        assert _is_command_allowed(_split_command(command))
    for command in ("pytest -q", "mypy .", "echo hello", "type file.txt", "dir"):
        assert not _is_command_allowed(_split_command(command))


def test_shell_blocks_arbitrary_runtimes_and_mutating_git() -> None:
    for command in ("python -c print(1)", "node script.js", "pip install package", "git commit -m test"):
        assert not _is_command_allowed(_split_command(command))


def _forbid_subprocess(*args, **kwargs):
    raise AssertionError(f"subprocess should not execute: {args!r} {kwargs!r}")


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "diff", "--no-index", "inside.py", "{sentinel}"],
        ["ruff", "check", "{sentinel}"],
        ["tree", "{sentinel}"],
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
    monkeypatch.setattr("agent.skills.shell._run_bounded_process", _forbid_subprocess)

    result = ShellSkill(base_dir=workspace, approval_policy=AutoApprove()).execute(
        {"command": shlex.join(rendered)}
    )

    assert result["ok"] is False
    assert "workspace" in str(result["error"]).casefold()
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert sentinel.read_text(encoding="utf-8") == secret


@pytest.mark.parametrize(
    "command",
    [
        "tree -o outside.txt .",
        "ruff format .",
        "ruff check --fix .",
        "ruff check --output-file outside.txt .",
        "git diff --output outside.txt",
    ],
)
def test_shell_rejects_mutation_or_external_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    monkeypatch.setattr("agent.skills.shell._run_bounded_process", _forbid_subprocess)
    result = ShellSkill(base_dir=tmp_path, approval_policy=AutoApprove()).execute(
        {"command": command}
    )
    assert result["ok"] is False


def test_shell_rejects_symlink_that_resolves_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sentinel = tmp_path / "external-secret.txt"
    sentinel.write_text("secret via symlink\n", encoding="utf-8")
    link = workspace / "linked-secret.txt"
    try:
        link.symlink_to(sentinel)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    monkeypatch.setattr("agent.skills.shell._run_bounded_process", _forbid_subprocess)

    result = ShellSkill(base_dir=workspace).execute({"command": "tree linked-secret.txt"})

    assert result["ok"] is False
    assert "workspace" in result["error"].casefold()
    assert "secret via symlink" not in json.dumps(result, ensure_ascii=False)


def test_shell_runs_normal_git_read_only_command_with_sanitized_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="clean\n", stderr="")

    monkeypatch.setattr("agent.skills.shell._run_bounded_process", fake_run)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path.parent))
    monkeypatch.setenv("GIT_TRACE", str(tmp_path / "trace"))
    skill = ShellSkill(base_dir=tmp_path)

    result = skill.execute({"command": "git status"})

    assert result["ok"] is True
    assert captured["command"] == [
        "git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
        "-c", "log.showSignature=false",
        "--no-pager", "status",
    ]
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["HOME"] == str(tmp_path.resolve())
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "PYTHONPATH" not in environment
    assert "GIT_TRACE" not in environment


def test_shell_requires_approval_for_ruff_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent.skills.shell._run_bounded_process", _forbid_subprocess)
    result = ShellSkill(base_dir=tmp_path, approval_policy=RequireExplicitApproval()).execute(
        {"command": "ruff check ."}
    )
    assert result["status"] == "blocked"
    assert result["error"] == "confirmation_required"


def test_shell_hardens_ruff_into_isolated_read_only_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["command"] = argv
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("agent.skills.shell._run_bounded_process", fake_run)
    result = ShellSkill(base_dir=tmp_path, approval_policy=AutoApprove()).execute(
        {"command": "ruff check ."}
    )

    assert result["ok"] is True
    assert captured["command"] == ["ruff", "check", "--isolated", "--no-cache", "--no-fix", "."]


def test_shell_rejects_explicit_ruff_configuration_even_inside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "ruff.toml").write_text("line-length = 88\n", encoding="utf-8")
    monkeypatch.setattr("agent.skills.shell._run_bounded_process", _forbid_subprocess)
    result = ShellSkill(base_dir=tmp_path, approval_policy=AutoApprove()).execute(
        {"command": "ruff check --config ruff.toml ."}
    )
    assert result["ok"] is False
    assert "configuration" in result["error"].casefold()


def test_shell_does_not_forward_ambient_secret_to_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("AGENT_TEST_SECRET", "SHELL-SECRET-SENTINEL")

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("agent.skills.shell._run_bounded_process", fake_run)
    result = ShellSkill(tmp_path, approval_policy=AutoApprove()).execute(
        {"command": "ruff check ."}
    )
    assert result["ok"] is True
    assert "AGENT_TEST_SECRET" not in captured["environment"]


def test_shell_formats_large_output_with_a_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "agent.skills.shell._run_bounded_process",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="x" * 5000, stderr=""),
    )
    result = ShellSkill(tmp_path, approval_policy=AutoApprove()).execute(
        {"command": "ruff check ."}
    )
    assert result["ok"] is True
    assert "truncado" in result["message"]


@pytest.mark.skipif(os.name != "nt", reason="Windows tokenization only")
def test_windows_tokenization_preserves_backslashes_and_quoted_spaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path.parent / "outside path" / "file.py"
    monkeypatch.setattr("agent.skills.shell._run_bounded_process", _forbid_subprocess)
    for command in (f"ruff check {outside}", f'ruff check "{outside}"'):
        result = ShellSkill(base_dir=tmp_path, approval_policy=AutoApprove()).execute(
            {"command": command}
        )
        assert result["ok"] is False
        assert "workspace" in result["error"].casefold()


def test_control_tokens_are_rejected_before_execution() -> None:
    assert _split_command("git status; tree .") is None
    assert _split_command("ruff check . | more") is None


def _wait_for_path(path: Path, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not path.exists():
        time.sleep(0.01)
    return path.exists()


def _wait_for_pidfd(pidfd: int, timeout_ms: int) -> bool:
    poller = select.poll()
    poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
    return bool(poller.poll(timeout_ms))


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups are unavailable on Windows")
@pytest.mark.parametrize(("mode", "expected_status"), [("timeout", "timed_out"), ("cancel", "cancelled")])
def test_posix_shell_process_terminates_parent_and_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_status: str,
) -> None:
    if not hasattr(os, "pidfd_open"):
        pytest.skip("pidfd_open is required for a race-resistant process assertion")

    parent_pid_path = tmp_path / "parent.pid"
    child_pid_path = tmp_path / "child.pid"
    ready_path = tmp_path / "ready"
    probe_path = tmp_path / "probe"
    late_path = tmp_path / "late"
    child_code = textwrap.dedent(
        f"""
        import os
        import pathlib
        import time

        pathlib.Path({str(child_pid_path)!r}).write_text(str(os.getpid()), encoding="utf-8")
        pathlib.Path({str(ready_path)!r}).write_text("ready", encoding="utf-8")
        while not pathlib.Path({str(probe_path)!r}).exists():
            time.sleep(0.01)
        pathlib.Path({str(late_path)!r}).write_text("late", encoding="utf-8")
        """
    )
    parent_code = textwrap.dedent(
        f"""
        import os
        import pathlib
        import subprocess
        import sys
        import time

        pathlib.Path({str(parent_pid_path)!r}).write_text(str(os.getpid()), encoding="utf-8")
        subprocess.Popen([sys.executable, "-c", {child_code!r}])
        while True:
            time.sleep(1)
        """
    )
    trusted_tools = tmp_path.parent / f"{tmp_path.name}-trusted-tools"
    trusted_tools.mkdir()
    executable = trusted_tools / "ruff"
    executable.write_text(f"#!{sys.executable}\n" + parent_code, encoding="utf-8")
    executable.chmod(0o755)

    def test_environment(workspace: WorkspaceContext) -> dict[str, str]:
        environment = confined_process_environment(workspace)
        environment["PATH"] = str(trusted_tools)
        return environment

    monkeypatch.setattr("agent.skills.shell.confined_process_environment", test_environment)
    skill = ShellSkill(
        workspace=WorkspaceContext.create(tmp_path),
        timeout=3 if mode == "timeout" else 10,
        approval_policy=AutoApprove(),
    )
    cancellation_event = threading.Event()
    result_box: list[dict[str, object]] = []
    worker = threading.Thread(
        target=lambda: result_box.append(
            skill.execute_with_context(
                {"command": "ruff check ."},
                cancellation_event=cancellation_event,
            )
        )
    )
    parent_pidfd: int | None = None
    child_pidfd: int | None = None
    parent_pid: int | None = None
    try:
        worker.start()
        assert _wait_for_path(parent_pid_path)
        assert _wait_for_path(child_pid_path)
        assert _wait_for_path(ready_path)
        parent_pid = int(parent_pid_path.read_text(encoding="utf-8"))
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        parent_pidfd = os.pidfd_open(parent_pid, 0)  # type: ignore[attr-defined]
        child_pidfd = os.pidfd_open(child_pid, 0)  # type: ignore[attr-defined]
        if mode == "cancel":
            cancellation_event.set()
        worker.join(timeout=8)
        assert not worker.is_alive()
        assert result_box[0]["ok"] is False
        assert result_box[0]["status"] == expected_status
        assert _wait_for_pidfd(parent_pidfd, 5000)
        assert _wait_for_pidfd(child_pidfd, 5000)
        probe_path.write_text("probe", encoding="utf-8")
        assert not _wait_for_path(late_path, timeout=0.5)
    finally:
        if worker.is_alive():
            cancellation_event.set()
            if parent_pid is None and parent_pid_path.exists():
                parent_pid = int(parent_pid_path.read_text(encoding="utf-8"))
            if parent_pid is not None:
                try:
                    os.killpg(parent_pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            worker.join(timeout=5)
        for pidfd in (parent_pidfd, child_pidfd):
            if pidfd is not None:
                os.close(pidfd)


@pytest.mark.parametrize("command", ["git", "ruff", "tree"])
def test_executable_resolution_does_not_accept_workspace_shadow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    workspace = WorkspaceContext.create(tmp_path)
    shadow_dir = tmp_path / "bin"
    shadow_dir.mkdir()
    if os.name == "nt":
        shadow = shadow_dir / f"{command}.exe"
        shadow.write_bytes(Path(sys.executable).read_bytes())
    else:
        shadow = shadow_dir / command
        shadow.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        shadow.chmod(0o755)
    environment = confined_process_environment(workspace)
    environment["PATH"] = os.pathsep.join((str(tmp_path), str(shadow_dir), environment["PATH"]))

    resolved = _resolve_executable(command, environment, workspace.root)

    assert resolved is None or not Path(resolved).resolve().is_relative_to(workspace.root)
    if resolved is not None:
        assert Path(resolved).is_absolute()


@pytest.mark.parametrize("command", ["ruff", "git", "tree"])
def test_shell_rejects_workspace_executable_shadow_in_real_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    workspace = tmp_path / "workspace"
    bin_dir = workspace / "bin"
    bin_dir.mkdir(parents=True)
    sentinel = workspace / f"{command}-shadow-ran"
    if os.name == "nt":
        fake = bin_dir / f"{command}.cmd"
        fake.write_text(f'@echo fake > "{sentinel}"\n@exit /b 42\n', encoding="utf-8")
    else:
        fake = bin_dir / command
        fake.write_text(
            f"#!/bin/sh\nprintf fake > {shlex.quote(str(sentinel))}\nexit 42\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
    workspace_context = WorkspaceContext.create(workspace)

    def workspace_only_environment(current_workspace: WorkspaceContext) -> dict[str, str]:
        environment = confined_process_environment(current_workspace)
        environment["PATH"] = str(bin_dir)
        environment.setdefault("PATHEXT", ".COM;.EXE;.BAT;.CMD")
        return environment

    monkeypatch.setattr(
        "agent.skills.shell.confined_process_environment", workspace_only_environment
    )
    skill = ShellSkill(workspace=workspace_context, approval_policy=AutoApprove())
    argument = "ruff check ." if command == "ruff" else f"{command} ." if command == "tree" else "git status"
    result = skill.execute({"command": argument})

    assert result["ok"] is False
    assert not sentinel.exists()


@pytest.mark.parametrize(
    "command",
    [
        "git log --show-signature",
        *[f"git log --pretty=format:{marker}" for marker in ("%G?", "%GS", "%GK", "%GF", "%GP", "%GT", "%GG")],
    ],
)
def test_shell_rejects_signature_verification_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setattr("agent.skills.shell._run_bounded_process", _forbid_subprocess)
    result = ShellSkill(base_dir=tmp_path, approval_policy=AutoApprove()).execute(
        {"command": command}
    )
    assert result["ok"] is False
    assert "assinatura" in result["error"].casefold() or "signature" in result["error"].casefold()


def _assert_process_terminated(process: subprocess.Popen[object]) -> None:
    deadline = time.monotonic() + 5
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert process.poll() is not None


@pytest.mark.parametrize("failure_point", ["readers", "monitor"])
def test_post_popen_exception_terminates_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    workspace = WorkspaceContext.create(tmp_path)
    created: list[subprocess.Popen[object]] = []
    original_popen = shell_process_module.subprocess.Popen

    def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[object]:
        process = original_popen(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(shell_process_module.subprocess, "Popen", capture_popen)
    if failure_point == "readers":
        monkeypatch.setattr(
            shell_process_module,
            "start_readers",
            lambda *_args: (_ for _ in ()).throw(OSError("reader setup sentinel")),
        )
    else:
        monkeypatch.setattr(
            shell_process_module,
            "_monitor_process",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("monitor sentinel")),
        )

    with pytest.raises((OSError, RuntimeError), match="sentinel"):
        run_bounded_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            workspace=workspace.root,
            environment=confined_process_environment(workspace),
            timeout=5,
        )

    assert created
    _assert_process_terminated(created[0])


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups are unavailable on Windows")
def test_posix_shell_parent_exit_terminates_descendant_with_inherited_pipes(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "pidfd_open"):
        pytest.skip("pidfd_open is required for a race-resistant process assertion")
    workspace = WorkspaceContext.create(tmp_path)
    parent_pid_path = tmp_path / "parent.pid"
    child_pid_path = tmp_path / "child.pid"
    ready_path = tmp_path / "child-ready"
    probe_path = tmp_path / "probe"
    late_path = tmp_path / "late"
    trusted_tools = tmp_path.parent / f"{tmp_path.name}-parent-tools"
    trusted_tools.mkdir()
    child_code = textwrap.dedent(
        f"""
        import os
        import pathlib
        import time
        pathlib.Path({str(child_pid_path)!r}).write_text(str(os.getpid()), encoding="utf-8")
        pathlib.Path({str(ready_path)!r}).write_text("ready", encoding="utf-8")
        while not pathlib.Path({str(probe_path)!r}).exists():
            time.sleep(0.01)
        pathlib.Path({str(late_path)!r}).write_text("late", encoding="utf-8")
        """
    )
    parent_code = textwrap.dedent(
        f"""
        import pathlib
        import subprocess
        import sys
        import time
        pathlib.Path({str(parent_pid_path)!r}).write_text(str(__import__('os').getpid()), encoding="utf-8")
        subprocess.Popen([sys.executable, "-c", {child_code!r}])
        while not pathlib.Path({str(ready_path)!r}).exists():
            time.sleep(0.01)
        """
    )
    executable = trusted_tools / "ruff"
    executable.write_text(f"#!{sys.executable}\n" + parent_code, encoding="utf-8")
    executable.chmod(0o755)
    environment = confined_process_environment(workspace)
    environment["PATH"] = str(trusted_tools)
    worker_result: list[object] = []

    def invoke() -> None:
        try:
            worker_result.append(
                run_bounded_process(
                    ["ruff", "check", "."],
                    workspace=workspace.root,
                    environment=environment,
                    timeout=5,
                )
            )
        except BaseException as exc:
            worker_result.append(exc)

    worker = threading.Thread(target=invoke)
    child_pidfd: int | None = None
    try:
        worker.start()
        assert _wait_for_path(child_pid_path)
        child_pidfd = os.pidfd_open(int(child_pid_path.read_text(encoding="utf-8")), 0)  # type: ignore[attr-defined]
        worker.join(timeout=8)
        assert not worker.is_alive()
        assert worker_result
        assert isinstance(worker_result[0], ShellProcessError)
        assert _wait_for_pidfd(child_pidfd, 5000)
        probe_path.write_text("probe", encoding="utf-8")
        assert not _wait_for_path(late_path, timeout=0.5)
    finally:
        if worker.is_alive():
            worker.join(timeout=1)
        if child_pidfd is not None:
            os.close(child_pidfd)


def test_job_assignment_failure_terminates_started_process_before_surface_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceContext.create(tmp_path)
    created = []
    launched_argv = []
    original_popen = shell_process_module.subprocess.Popen

    def capture_popen(*args, **kwargs):
        launched_argv.append(list(args[0]))
        process = original_popen(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(shell_process_module.subprocess, "Popen", capture_popen)
    monkeypatch.setattr(shell_process_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(shell_process_module, "create_windows_job", lambda: object())
    monkeypatch.setattr(shell_process_module, "assign_windows_job", lambda *_args: False)
    monkeypatch.setattr(shell_process_module, "close_windows_job", lambda _job: True)

    def terminate(process, _job, *, process_group_id):
        del process_group_id
        process.terminate()
        process.wait(timeout=5)
        return None

    monkeypatch.setattr(shell_process_module, "terminate_process", terminate)

    with pytest.raises(ShellProcessError, match="associar"):
        run_bounded_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            workspace=workspace.root,
            environment=confined_process_environment(workspace),
            timeout=5,
        )

    assert created
    assert launched_argv
    assert Path(launched_argv[0][0]).resolve() == Path(sys.executable).resolve()
    assert created[0].poll() is not None


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are unavailable on POSIX")
def test_windows_job_assignment_failure_uses_real_tree_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceContext.create(tmp_path)
    created = []
    original_popen = shell_process_module.subprocess.Popen

    def capture_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(shell_process_module.subprocess, "Popen", capture_popen)
    monkeypatch.setattr(shell_process_module, "assign_windows_job", lambda *_args: False)

    with pytest.raises(ShellProcessError, match="associar"):
        run_bounded_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            workspace=workspace.root,
            environment=confined_process_environment(workspace),
            timeout=5,
        )

    assert created
    assert created[0].poll() is not None
