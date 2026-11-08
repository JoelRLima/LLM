import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_agent.application import test_queue_worker
from llm_agent.application.test_queue_service import QueueExecutionRequest
from llm_agent.interfaces.test_queue_output import safe_text
from llm_agent.process.detached import launch_detached


def test_detached_launch_uses_checked_identity_and_explicit_authority(tmp_path, monkeypatch):
    calls = []
    config = tmp_path / "config.json"
    config.write_text("{}")
    service = SimpleNamespace(root=tmp_path, check=lambda **kwargs: {
        "runner_active": False, "configuration": {"execution_identity": "identity", "resume_compatible": True}})
    monkeypatch.setattr(test_queue_worker, "launch_detached", lambda argv, **kwargs: calls.append((argv, kwargs)) or 123)
    request = QueueExecutionRequest(config, live_authorized=True)
    worker = test_queue_worker.start_queue_worker(service, request)
    assert worker["status"] == "starting"
    assert worker["pid"] == 123
    argv = calls[0][0]
    assert "--live" in argv
    assert argv[argv.index("--expected-identity") + 1] == "identity"
    assert "--detach" not in argv
    assert "--yes" not in argv
    with pytest.raises(ValueError, match="mudou"):
        test_queue_worker.start_queue_worker(service, replace(request, expected_identity="stale"))
    assert len(calls) == 1


@pytest.mark.parametrize("authorized", [False, None, 1, "yes"])
def test_no_worker_can_start_without_exact_live_authorization(tmp_path, monkeypatch, authorized):
    monkeypatch.setattr(test_queue_worker, "launch_detached", lambda *a, **k: pytest.fail("unauthorized process"))
    service = SimpleNamespace(check=lambda **kwargs: pytest.fail("unauthorized preflight"))
    with pytest.raises(PermissionError):
        test_queue_worker.start_queue_worker(service, QueueExecutionRequest(tmp_path / "missing", live_authorized=authorized))


def test_detached_process_has_no_stdin_and_saves_output(tmp_path):
    import os

    marker = tmp_path / "finished.txt"
    log = tmp_path / "worker.log"
    program = "import pathlib,sys; assert sys.stdin.read() == ''; print('safe-output'); pathlib.Path(sys.argv[1]).write_text('done')"
    pid = launch_detached([sys.executable, "-c", program, str(marker)], cwd=tmp_path, output=log, env=os.environ)
    assert pid > 0
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert marker.read_text() == "done"
    assert "safe-output" in log.read_text()


def test_queue_diagnostics_are_redacted_before_worker_log_capture():
    rendered = safe_text('nested={"api_key": "private worker credential"} password=private-secret \x1b')
    assert "private worker credential" not in rendered
    assert "private-secret" not in rendered
    assert "\\u001b" in rendered


def test_worker_survives_launcher_process_exit(tmp_path):
    marker = tmp_path / "after-parent-exit.txt"
    program = "import pathlib,sys,time; time.sleep(1); pathlib.Path(sys.argv[1]).write_text('finished')"
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        "import os,sys\nfrom pathlib import Path\n"
        "from llm_agent.process.detached import launch_detached\n"
        f"launch_detached([sys.executable, '-c', {program!r}, {str(marker)!r}], "
        f"cwd=Path({str(tmp_path)!r}), output=Path({str(tmp_path / 'detached.log')!r}), env=os.environ)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3] / "src")
    parent = subprocess.run([sys.executable, str(launcher)], env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=5)
    assert parent.returncode == 0, parent.stderr.decode(errors="replace")
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert marker.read_text() == "finished"
