import json
import os
import subprocess
import sys
from pathlib import Path


def _run_executor_in_bounded_subprocess(child_code: str, timeout: float) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    source_path = str(repo_root / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, environment.get("PYTHONPATH", "")) if part
    )
    driver = f"""
import json
import sys
from llm_agent.execution.command import CommandExecutor, CommandRequest
request = CommandRequest(
    executable=sys.executable,
    argv=("-c", {child_code!r}),
    stdin=b"x" * (1024 * 1024),
    timeout_seconds={timeout!r},
    stdout_limit=2 * 1024 * 1024,
)
result = CommandExecutor().execute(request)
print(json.dumps({{
    "return_code": result.return_code,
    "timed_out": result.timed_out,
    "stdout_size": len(result.stdout),
    "stdout_truncated": result.stdout_truncated,
}}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", driver],
        check=True,
        capture_output=True,
        text=True,
        timeout=8,
        env=environment,
    )
    return json.loads(completed.stdout)


def test_large_stdin_and_output_are_drained_concurrently() -> None:
    child = (
        "import sys; "
        "sys.stdout.buffer.write(b'o' * (1024 * 1024)); "
        "sys.stdout.flush(); "
        "data = sys.stdin.buffer.read(); "
        "sys.stdout.buffer.write(str(len(data)).encode())"
    )

    result = _run_executor_in_bounded_subprocess(child, timeout=5)

    assert result == {
        "return_code": 0,
        "timed_out": False,
        "stdout_size": 1024 * 1024 + len(str(1024 * 1024)),
        "stdout_truncated": False,
    }


def test_stdin_writer_does_not_bypass_process_timeout() -> None:
    child = (
        "import sys, time; "
        "sys.stdout.buffer.write(b'o' * (1024 * 1024)); "
        "sys.stdout.flush(); "
        "time.sleep(10)"
    )

    result = _run_executor_in_bounded_subprocess(child, timeout=0.2)

    assert result["timed_out"] is True
    assert result["stdout_size"] == 1024 * 1024
