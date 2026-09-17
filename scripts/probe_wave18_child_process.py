"""Focused diagnostic for the W18 candidate-stage child-process probes.

This is intentionally separate from the final installed-product acceptance
gate.  It executes the same payload/runtime argv used by ``install.ps1`` with
closed stdin and bounded timeouts, and records only bounded output tails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

TAIL_LIMIT = 2000
PROBE_TIMEOUT_SECONDS = 20


class ProbeFailure(AssertionError):
    """Raised when a focused child-process diagnostic cannot run safely."""


def _tail(value: str) -> str:
    return value[-TAIL_LIMIT:]


def _safe_text(value: str) -> str:
    """Redact only values following common credential-like labels."""

    import re

    return re.sub(
        r"(?i)((?:--?|)(?:token|password|secret|api[-_]?key|authorization)\s*[=:]?\s+)(?:\"[^\"]*\"|\S+)",
        r"\1<redacted>",
        value,
    )


def _safe_argv(argv: list[str]) -> list[str]:
    safe: list[str] = []
    redact_next = False
    for argument in argv:
        if redact_next:
            safe.append("<redacted>")
            redact_next = False
            continue
        if argument.casefold().rstrip("=") in {
            "--token",
            "-token",
            "--password",
            "-password",
            "--secret",
            "-secret",
            "--api-key",
            "-api-key",
            "--authorization",
            "-authorization",
        }:
            safe.append(argument)
            redact_next = True
            continue
        safe.append(_safe_text(argument))
    return safe


def _powershell() -> str:
    powershell = shutil.which("powershell.exe")
    if powershell:
        return powershell
    fallback = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    if fallback.is_file():
        return str(fallback)
    raise ProbeFailure("Windows PowerShell 5.1 executable was not found")


def _ps_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_child(name: str, argv: list[str], cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    started = time.monotonic()
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=PROBE_TIMEOUT_SECONDS)
        return {
            "name": name,
            "executable": argv[0],
            "argv": _safe_argv(argv),
            "cwd": str(cwd),
            "stdin": "DEVNULL",
            "timeout_seconds": PROBE_TIMEOUT_SECONDS,
            "exit_state": "exited",
            "exit_code": process.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": _safe_text(_tail(stdout)),
            "stderr_tail": _safe_text(_tail(stderr)),
        }
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        return {
            "name": name,
            "executable": argv[0],
            "argv": _safe_argv(argv),
            "cwd": str(cwd),
            "stdin": "DEVNULL",
            "timeout_seconds": PROBE_TIMEOUT_SECONDS,
            "exit_state": "timeout",
            "exit_code": process.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": _safe_text(_tail(stdout)),
            "stderr_tail": _safe_text(_tail(stderr)),
        }


def _clean_environment() -> dict[str, str]:
    env = dict(os.environ)
    checkout = str(Path.cwd()).casefold()
    for key in list(env):
        value = env[key]
        upper = key.upper()
        if upper in {
            "PYTHONHOME",
            "PYTHONPATH",
            "VIRTUAL_ENV",
            "UV_PROJECT_ENVIRONMENT",
            "UV_PYTHON",
            "UV_TOOL_BIN_DIR",
        } or upper.startswith("W18_") or ".venv" in value.casefold() or checkout in value.casefold():
            env.pop(key, None)
    system_root_value = env.get("SystemRoot") or env.get("SYSTEMROOT") or env.get("windir")
    if not system_root_value:
        raise ProbeFailure("Windows SystemRoot environment variable is missing")
    system_root = Path(system_root_value)
    env["PATH"] = ";".join(
        str(path) for path in (system_root / "System32", system_root, system_root / "System32" / "Wbem")
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _payload_snapshot(payload_root: Path) -> dict[str, tuple[str, int, str]]:
    snapshot: dict[str, tuple[str, int, str]] = {}
    for path in payload_root.rglob("*"):
        relative = path.relative_to(payload_root).as_posix()
        if path.is_symlink():
            raise ProbeFailure(f"payload contains a symbolic link: {path}")
        if path.is_dir():
            snapshot[relative] = ("directory", 0, "")
            continue
        if not path.is_file():
            raise ProbeFailure(f"payload contains a non-regular member: {path}")
        snapshot[relative] = (
            "file",
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return snapshot


def _assert_no_volatile_bytecode(payload_root: Path) -> None:
    for path in payload_root.rglob("*"):
        if path.name.casefold() == "__pycache__" and path.is_dir():
            raise ProbeFailure(f"volatile bytecode cache is present in candidate: {path}")
        if path.is_file() and path.suffix.casefold() == ".pyc":
            raise ProbeFailure(f"volatile bytecode is present in candidate: {path}")


def _origin_code() -> str:
    return (
        "import agent,importlib.metadata,json,pathlib,platform,sys; "
        "print(json.dumps({\"agent\":str(pathlib.Path(agent.__file__).resolve()),"
        "\"exe\":str(pathlib.Path(sys.executable).resolve()),"
        "\"agent_version\":agent.__version__,"
        "\"distribution_version\":importlib.metadata.version(\"local-llm-agent\"),"
        "\"python\":platform.python_version(),\"path\":list(sys.path)}))"
    )


def _run_child_wait_before_drain(
    name: str, argv: list[str], cwd: Path, env: dict[str, str]
) -> dict[str, Any]:
    """Reproduce the old deadlock-prone wait-before-drain ordering."""

    started = time.monotonic()
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        process.wait(timeout=PROBE_TIMEOUT_SECONDS)
        stdout, stderr = process.communicate()
        state = "exited"
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        state = "timeout-before-drain"
    return {
        "name": name,
        "executable": argv[0],
        "argv": _safe_argv(argv),
        "cwd": str(cwd),
        "stdin": "DEVNULL",
        "timeout_seconds": PROBE_TIMEOUT_SECONDS,
        "exit_state": state,
        "exit_code": process.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": _safe_text(_tail(stdout)),
        "stderr_tail": _safe_text(_tail(stderr)),
    }


def _payload_calls(payload_root: Path, work_root: Path) -> list[tuple[str, list[str], Path]]:
    runtime = payload_root / "runtime" / "python.exe"
    shim = payload_root / "app" / "launcher.py"
    launcher = payload_root / "bin" / "llm-agent.cmd"
    home = work_root / "home"
    cwd = work_root / "outside cwd"
    system_root = Path(os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows")
    comspec = system_root / "System32" / "cmd.exe"
    return [
        ("candidate-stage / runtime --version", [str(runtime), str(shim), "--version"], cwd),
        ("candidate-stage / runtime --help", [str(runtime), str(shim), "--help"], cwd),
        (
            "candidate-stage / runtime config init",
            [str(runtime), str(shim), "config", "init", "--home", str(home)],
            cwd,
        ),
        (
            "candidate-stage / runtime doctor offline",
            [
                str(runtime),
                str(shim),
                "doctor",
                "--json",
                "--home",
                str(home),
                "--workspace",
                str(cwd),
            ],
            cwd,
        ),
        ("candidate-stage / runtime import-origin", [str(runtime), "-c", _origin_code()], cwd),
        (
            "candidate-stage / candidate launcher --version",
            [str(comspec), "/d", "/s", "/c", "call", str(launcher), "--version"],
            cwd,
        ),
        (
            "candidate-stage / candidate launcher --help",
            [str(comspec), "/d", "/s", "/c", "call", str(launcher), "--help"],
            cwd,
        ),
    ]


def _run_payload(payload_root: Path, *, legacy_wait_before_drain: bool = False) -> list[dict[str, Any]]:
    runtime = payload_root / "runtime" / "python.exe"
    shim = payload_root / "app" / "launcher.py"
    candidate_launcher = payload_root / "bin" / "llm-agent.cmd"
    for required in (runtime, shim, candidate_launcher):
        if not required.is_file():
            raise ProbeFailure(f"payload member is missing: {required}")
    with tempfile.TemporaryDirectory(prefix="w18-child-probe-") as raw:
        root = Path(raw)
        cwd = root / "outside cwd"
        home = root / "home"
        cwd.mkdir()
        home.mkdir()
        env = _clean_environment()
        calls = _payload_calls(payload_root, root)
        runner = _run_child_wait_before_drain if legacy_wait_before_drain else _run_child
        results = [runner(name, argv, call_cwd, env) for name, argv, call_cwd in calls]
        return results


def _process_exists(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist.exe", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return f'"{pid}"' in result.stdout


def _probe_production_helper(installer: Path, payload_root: Path) -> dict[str, Any]:
    """Exercise the production helper with normal and timed-out children."""

    text = installer.read_text(encoding="utf-8")
    marker = "function Assert-ProcessSuccess {"
    if text.count(marker) != 1:
        raise ProbeFailure("production helper insertion marker is not unique")
    runtime = payload_root / "runtime" / "python.exe"
    normal_code = (
        "import sys; data=sys.stdin.read(); print('W18_NORMAL_STDOUT=' + repr(data), flush=True); "
        "print('W18_NORMAL_STDERR', file=sys.stderr, flush=True)"
    )
    timeout_code = (
        "import os,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "print('W18_TIMEOUT_PARENT_PID=' + str(os.getpid()), flush=True); "
        "print('W18_TIMEOUT_CHILD_PID=' + str(child.pid), flush=True); "
        "print('W18_TIMEOUT_STDOUT_TAIL', flush=True); "
        "print('W18_TIMEOUT_STDERR_TAIL', file=sys.stderr, flush=True); time.sleep(30)"
    )
    with tempfile.TemporaryDirectory(prefix="w18-child-helper-script-") as raw:
        workspace = Path(raw)
        cwd = workspace / "outside cwd"
        cwd.mkdir()
        injected = f'''$probePath = $env:SystemRoot + "\\System32"
$normal = Invoke-ExplicitProcess -FileName {_ps_literal(runtime)} -Arguments @("-c", {_ps_literal(normal_code)}) -WorkingDirectory {_ps_literal(cwd)} -PersistentPath $probePath -StepName "focused normal child" -TimeoutMilliseconds 10000
if ([int]$normal.exit_code -ne 0 -or $normal.stdout_tail -notmatch "W18_NORMAL_STDOUT=''" -or $normal.stderr_tail -notmatch "W18_NORMAL_STDERR") {{ throw "normal child probe failed" }}
Write-Output "W18_NORMAL_CHILD_PASS"
try {{
    Invoke-ExplicitProcess -FileName {_ps_literal(runtime)} -Arguments @("-c", {_ps_literal(timeout_code)}) -WorkingDirectory {_ps_literal(cwd)} -PersistentPath $probePath -StepName "focused timeout child" -TimeoutMilliseconds 1000 | Out-Null
    throw "timeout child unexpectedly completed"
}}
catch {{
    $message = $_.Exception.Message
    Write-Output "W18_TIMEOUT_MESSAGE_BEGIN"
    Write-Output $message
    Write-Output "W18_TIMEOUT_MESSAGE_END"
    foreach ($required in @("child step: focused timeout child", "executable:", "arguments:", "cwd:", "timeout_seconds: 1", "exit_state: timeout", "stdout_tail:", "stderr_tail:", "termination:")) {{
        if ($message.IndexOf($required, [StringComparison]::Ordinal) -lt 0) {{ throw "timeout diagnostic missing: $required" }}
    }}
    Write-Output "W18_TIMEOUT_DIAGNOSTIC_PASS"
}}
return
'''
        script = workspace / "probe-child-helper.ps1"
        script.write_text(text.replace(marker, injected + marker, 1), encoding="utf-8", newline="\n")
        environment = _clean_environment()
        result = subprocess.run(
            [
                _powershell(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-BundleRoot",
                str(installer.parent),
            ],
            cwd=str(cwd),
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    combined = result.stdout + "\n" + result.stderr
    if result.returncode != 0 or "W18_NORMAL_CHILD_PASS" not in result.stdout or "W18_TIMEOUT_DIAGNOSTIC_PASS" not in result.stdout:
        raise ProbeFailure(f"production child helper probe failed: {combined[-5000:]}")
    pids = [int(value) for value in re.findall(r"W18_TIMEOUT_(?:PARENT|CHILD)_PID=(\d+)", combined)]
    if len(pids) != 2:
        raise ProbeFailure(f"timeout probe did not report both process ids: {combined[-3000:]}")
    residual = [pid for pid in pids if _process_exists(pid)]
    if residual:
        raise ProbeFailure(f"timed-out child process tree survived: {residual}")
    return {
        "status": "passed",
        "normal": "captured stdout/stderr and observed closed stdin",
        "timeout": "diagnostic-and-tree-termination",
        "reported_pids": pids,
        "residual_pids": residual,
        "diagnostic_tail": combined[-3000:],
    }


def _probe_embedded_runtime_helper(installer: Path, payload_root: Path) -> dict[str, Any]:
    """Run the production Test-EmbeddedRuntime function in isolation."""

    text = installer.read_text(encoding="utf-8")
    marker = "$lease = $null\ntry {\n"
    if text.count(marker) != 1:
        raise ProbeFailure("embedded-runtime helper insertion marker is not unique")
    with tempfile.TemporaryDirectory(prefix="w18-embedded-runtime-helper-") as raw:
        workspace = Path(raw)
        script = workspace / "probe-embedded-runtime.ps1"
        injected = f'''$script:SourceCheckout = $env:SystemRoot + "\\w18-source-checkout-not-present"
$probePath = $env:SystemRoot + "\\System32"
$result = Test-EmbeddedRuntime {_ps_literal(payload_root)} $probePath
if ($result.version -ne "llm-agent 0.2.0rc1" -or $result.doctor_offline_ready -ne $true -or $result.python -ne "3.12.14") {{ throw "embedded runtime helper probe failed" }}
Write-Output "W18_EMBEDDED_RUNTIME_HELPER_PASS"
return
'''
        script.write_text(text.replace(marker, injected + marker, 1), encoding="utf-8", newline="\n")
        environment = _clean_environment()
        result = subprocess.run(
            [
                _powershell(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-BundleRoot",
                str(installer.parent),
            ],
            cwd=str(workspace),
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )
    if result.returncode != 0 or "W18_EMBEDDED_RUNTIME_HELPER_PASS" not in result.stdout:
        combined = result.stdout + "\n" + result.stderr
        raise ProbeFailure(f"production Test-EmbeddedRuntime probe failed: {combined[-5000:]}")
    return {"status": "passed", "scope": "Test-EmbeddedRuntime candidate acceptance"}


def _probe_installer_timeout_rollback(bundle_root: Path) -> dict[str, Any]:
    """Inject a child timeout after extraction and prove clean rollback."""

    installer = bundle_root / "install.ps1"
    text = installer.read_text(encoding="utf-8")
    marker = "        Test-EmbeddedRuntime $candidateStage $persistentBefore | Out-Null\n"
    if text.count(marker) != 1:
        raise ProbeFailure("candidate-stage timeout insertion marker is not unique")
    timeout_code = (
        "import os,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "print('W18_ROLLBACK_PARENT_PID=' + str(os.getpid()), flush=True); "
        "print('W18_ROLLBACK_CHILD_PID=' + str(child.pid), flush=True); "
        "print('W18_ROLLBACK_STDOUT_TAIL', flush=True); "
        "print('W18_ROLLBACK_STDERR_TAIL', file=sys.stderr, flush=True); time.sleep(30)"
    )
    injected = (
        f"        Invoke-ExplicitProcess -FileName (Join-Path $candidateStage \"runtime\\python.exe\") "
        f"-Arguments @(\"-c\", {_ps_literal(timeout_code)}) -WorkingDirectory $candidateStage "
        "-PersistentPath $persistentBefore -StepName \"candidate-stage / synthetic timeout\" "
        "-TimeoutMilliseconds 1000 | Out-Null\n"
    )
    with tempfile.TemporaryDirectory(prefix="w18-child-rollback-") as raw:
        workspace = Path(raw)
        script = workspace / "install-timeout.ps1"
        script.write_text(text.replace(marker, injected, 1), encoding="utf-8", newline="\n")
        local_appdata = workspace / "local appdata"
        local_appdata.mkdir()
        cwd = workspace / "outside cwd"
        cwd.mkdir()
        environment = _clean_environment()
        environment["LOCALAPPDATA"] = str(local_appdata)
        command = [
            _powershell(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-BundleRoot",
            str(bundle_root),
        ]
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=240)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            stdout, stderr = process.communicate()
            raise ProbeFailure(
                f"installer timeout rollback probe exceeded 240 seconds: {stdout[-2000:]} {stderr[-2000:]}"
            ) from exc
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        install_root = local_appdata / "local-llm-agent" / "install"
        root_present_after = install_root.exists()
        combined = result.stdout + "\n" + result.stderr
        pids = [int(value) for value in re.findall(r"W18_ROLLBACK_(?:PARENT|CHILD)_PID=(\d+)", combined)]
        residual = [pid for pid in pids if _process_exists(pid)]
    required = (
        "candidate-stage / synthetic timeout",
        "arguments:",
        "cwd:",
        "timeout_seconds: 1",
        "exit_state: timeout",
        "stdout_tail:",
        "stderr_tail:",
    )
    if result.returncode == 0 or any(token not in combined for token in required):
        raise ProbeFailure(f"installer timeout rollback did not preserve diagnostics: {combined[-6000:]}")
    if len(pids) != 2:
        raise ProbeFailure(f"installer timeout did not report both process ids: {combined[-4000:]}")
    if root_present_after:
        raise ProbeFailure(f"child-timeout rollback left install root: {install_root}")
    if residual:
        raise ProbeFailure(f"child-timeout rollback left residual processes: {residual}")
    return {
        "status": "passed",
        "rollback": "root-absent",
        "reported_pids": pids,
        "residual_pids": residual,
        "diagnostic_tail": combined[-3000:],
    }


def run_probe(
    payload_root: Path,
    *,
    installer: Path | None = None,
    bundle_root: Path | None = None,
    legacy_wait_before_drain: bool = False,
) -> dict[str, Any]:
    if os.name != "nt":
        return {"status": "skipped", "reason": "Windows-only focused probe"}
    payload_root = payload_root.resolve()
    _assert_no_volatile_bytecode(payload_root)
    before = _payload_snapshot(payload_root)
    results = _run_payload(payload_root, legacy_wait_before_drain=legacy_wait_before_drain)
    after = _payload_snapshot(payload_root)
    if before != after:
        changed = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
        raise ProbeFailure(f"candidate changed during focused runtime calls: {changed[:20]}")
    _assert_no_volatile_bytecode(payload_root)
    failures = [result for result in results if result["exit_state"] != "exited" or result["exit_code"] != 0]
    doctor = next((result for result in results if result["name"].endswith("runtime doctor offline")), None)
    if doctor is not None and (
        "offline_ready" not in doctor["stdout_tail"] or "not_checked" not in doctor["stdout_tail"]
    ):
        failures.append({"name": "candidate-stage / runtime doctor offline", "reason": "not offline/no-network evidence"})
    result: dict[str, Any] = {
        "status": "passed" if not failures else "failed",
        "calls": results,
        "failures": failures,
        "candidate_immutable": True,
        "candidate_files": len(after),
        "bytecode_policy": "PYTHONDONTWRITEBYTECODE=1",
    }
    if installer is not None and not legacy_wait_before_drain:
        result["production_helper"] = _probe_production_helper(installer.resolve(), payload_root.resolve())
        result["embedded_runtime_helper"] = _probe_embedded_runtime_helper(installer.resolve(), payload_root.resolve())
    if bundle_root is not None and not legacy_wait_before_drain:
        result["installer_timeout_rollback"] = _probe_installer_timeout_rollback(bundle_root.resolve())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload-dir", type=Path, required=True, help="extracted self-contained payload root")
    parser.add_argument("--installer", type=Path, help="current installer script for the focused helper probe")
    parser.add_argument("--bundle-dir", type=Path, help="extracted release bundle for timeout rollback probe")
    parser.add_argument("--summary-json", type=Path, help="write bounded diagnostic evidence")
    parser.add_argument(
        "--legacy-wait-before-drain",
        action="store_true",
        help="reproduce the pre-corrective wait-before-drain ordering",
    )
    args = parser.parse_args(argv)
    try:
        result = run_probe(
            args.payload_dir,
            installer=args.installer,
            bundle_root=args.bundle_dir,
            legacy_wait_before_drain=args.legacy_wait_before_drain,
        )
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)}
        print(f"W18 child-process diagnostic failed: {exc}")
        if args.summary_json:
            args.summary_json.parent.mkdir(parents=True, exist_ok=True)
            args.summary_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 1
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("W18_CHILD_PROCESS_DIAGNOSTIC=" + result["status"].upper())
    return 0 if result["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
