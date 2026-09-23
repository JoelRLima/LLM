"""Installed MCP-extra acceptance owner for W20-C.

The verifier is intentionally separate from the base wheel verifier. It is only
invoked by the user's final closure block in an environment where the optional
extra and reviewed union lock are installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from distribution.mcp_lockfiles import mcp_union_lock_sha256, validate_mcp_union_lock  # noqa: E402

TOOLS = ("engineering_list", "engineering_describe", "engineering_result", "engineering_inspect_completed", "engineering_health_offline")
FOREIGN_RUN_SEED = """\
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

from agent.engineering.contracts import EngineeringCaller, EngineeringExecutionContext, EngineeringRequest, EngineeringTerminalStatus, EngineeringWorkspaceContext
from agent.engineering.policy import EngineeringPreflight, preflight
from agent.engineering.registry import EngineeringTerminalIntent, production_registry
from agent.engineering.store import EngineeringRunStore
from agent.runtime.paths import AppPaths
from agent.runtime.storage_bootstrap import StorageBootstrap
from agent.runtime.workspace_context import WorkspaceContext

paths = AppPaths.discover(Path(sys.argv[1]))
StorageBootstrap().prepare(paths)
workspace = WorkspaceContext.create(Path(sys.argv[2]))
context = EngineeringExecutionContext(
    EngineeringCaller.AUTOMATION_HEADLESS,
    paths,
    EngineeringWorkspaceContext(workspace.workspace_id, workspace.root),
    None,
    frozenset(),
    lambda: datetime.now(timezone.utc),
    time.monotonic,
)
admission = preflight(EngineeringRequest("health.offline", {}), context, production_registry(), backend_available=True)
if not isinstance(admission, EngineeringPreflight):
    raise RuntimeError("foreign run admission failed")
store = EngineeringRunStore()
active = store.begin(admission, context)
result = store.finish(
    active,
    admission,
    EngineeringTerminalIntent(EngineeringTerminalStatus.SUCCEEDED, (), {"status": "ok"}, ()),
    context,
)
if result.run_id != active.run_id or result.workspace_id != workspace.workspace_id:
    raise RuntimeError("foreign run verification failed")
print(result.run_id)
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _hash_bound_install_input(union_lock: Path, wheel: Path, destination: Path) -> str:
    resolved_wheel = wheel.resolve(strict=True)
    wheel_sha256 = _sha256(resolved_wheel)
    lock_text = union_lock.read_text(encoding="utf-8").rstrip()
    wheel_requirement = (
        f"local-llm-agent @ {resolved_wheel.as_uri()} \\\n"
        f"    --hash=sha256:{wheel_sha256}\n"
    )
    destination.write_text(f"{lock_text}\n\n{wheel_requirement}", encoding="utf-8")
    return wheel_sha256


def _seed_foreign_run(python: Path, app_home: Path, foreign_workspace: Path, script: Path) -> str:
    script.write_text(FOREIGN_RUN_SEED, encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONHOME", "PYTHONPATH"}}
    completed = subprocess.run(
        (str(python), str(script), str(app_home), str(foreign_workspace)),
        cwd=foreign_workspace,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    run_id = completed.stdout.strip()
    if not re.fullmatch(r"engr-[0-9a-f]{32}", run_id):
        raise RuntimeError("foreign run seed returned an invalid identity")
    return run_id


def _field(value: Mapping[str, Any], wire_name: str, python_name: str, default: Any = None) -> Any:
    return value[wire_name] if wire_name in value else value.get(python_name, default)


def _safe_projection(item: Mapping[str, Any], *, expect_error: bool) -> tuple[bool, dict[str, Any] | None]:
    result = item.get("result", {})
    if not isinstance(result, Mapping):
        return False, None
    content = result.get("content", [])
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], Mapping):
        return False, None
    try:
        text_document = json.loads(str(content[0].get("text", "")))
    except json.JSONDecodeError:
        return False, None
    structured = _field(result, "structuredContent", "structured_content")
    is_error = _field(result, "isError", "is_error", False)
    valid = isinstance(text_document, dict) and text_document == structured and is_error is expect_error
    return valid, text_document if isinstance(text_document, dict) else None


def _mcp_requests(foreign_run_id: str) -> list[dict[str, Any]]:
    return [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "w20c", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "engineering_list", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "engineering_describe", "arguments": {"operation_id": "health.offline"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "engineering_health_offline", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "engineering_result", "arguments": {"run_id": foreign_run_id}}},
    ]


def _read_mcp_response(process: subprocess.Popen[str], expected_id: int, payloads: list[dict[str, Any]]) -> None:
    if process.stdout is None:
        raise RuntimeError("MCP stdout pipe was not created")
    while True:
        line = process.stdout.readline()
        if not line:
            raise RuntimeError(f"MCP stdio closed before response {expected_id}")
        response = json.loads(line)
        if not isinstance(response, dict):
            raise RuntimeError("MCP stdio emitted a non-object response")
        payloads.append(response)
        if response.get("id") == expected_id:
            return


def _collect_mcp_payloads(
    process: subprocess.Popen[str],
    requests: list[dict[str, Any]],
    *,
    timeout: float,
) -> tuple[list[dict[str, Any]], int | None]:
    if process.stdin is None:
        raise RuntimeError("MCP stdin pipe was not created")
    payloads: list[dict[str, Any]] = []
    for request in requests:
        process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        process.stdin.flush()
        expected_id = request.get("id")
        if expected_id is not None:
            _read_mcp_response(process, int(expected_id), payloads)
    process.stdin.close()
    process.stdin = None
    stdout_tail, _stderr = process.communicate(timeout=timeout)
    for line in stdout_tail.splitlines():
        if line.strip():
            response = json.loads(line)
            if not isinstance(response, dict):
                raise RuntimeError("MCP stdio emitted a non-object response")
            payloads.append(response)
    return payloads, process.returncode


def _start_mcp_process(python: Path, workspace: Path, app_home: Path) -> subprocess.Popen[str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    return subprocess.Popen(
        [
            str(python), "-m", "agent.interfaces.cli.app", "mcp", "engineering",
            "--workspace", str(workspace), "--home", str(app_home),
        ],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=workspace,
        env=environment,
        text=True,
    )


def _roundtrip(
    python: Path,
    workspace: Path,
    app_home: Path,
    foreign_run_id: str,
    foreign_workspace: Path,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    process = _start_mcp_process(python, workspace, app_home)
    payloads, returncode = _collect_mcp_payloads(
        process, _mcp_requests(foreign_run_id), timeout=timeout
    )
    by_id = {item.get("id"): item for item in payloads if isinstance(item, dict) and "id" in item}
    initialize_result = by_id.get(1, {}).get("result", {})
    tools_result = by_id.get(2, {}).get("result", {})
    listed = tools_result.get("tools", [])
    names = [item.get("name") for item in listed if isinstance(item, dict)]

    success_projections = [_safe_projection(by_id.get(number, {}), expect_error=False)[0] for number in (3, 4, 5)]
    error_projection, error_document = _safe_projection(by_id.get(6, {}), expect_error=True)
    serialized_error = json.dumps(error_document, ensure_ascii=False, sort_keys=True) if error_document else ""
    safe_error = (
        error_projection
        and error_document is not None
        and error_document.get("reason_code") == "ENGINEERING_RUN_NOT_FOUND"
        and foreign_run_id not in serialized_error
        and str(foreign_workspace) not in serialized_error
        and "traceback" not in serialized_error.casefold()
        and "exception" not in serialized_error.casefold()
    )
    capabilities = initialize_result.get("capabilities", {})
    return {
        "tools": names,
        "initialize_capabilities_tools": isinstance(capabilities.get("tools"), dict),
        "resources_exposed": "resources" in capabilities,
        "prompts_exposed": "prompts" in capabilities,
        "safe_calls": all(success_projections),
        "safe_error_path": safe_error,
        "workspace_isolation": safe_error,
        "protocol_stdout_pure": all(isinstance(item, dict) for item in payloads),
        "process_clean": returncode == 0,
    }


def acceptance_summary(
    *,
    wheel: Path,
    union_lock: Path,
    workspace: Path,
    roundtrip: Mapping[str, Any],
    wheel_sha256: str | None = None,
) -> dict[str, Any]:
    names = tuple(roundtrip.get("tools", ()))
    return {
        "schema_version": 1,
        "acceptance": names == TOOLS and bool(roundtrip.get("protocol_stdout_pure")),
        "status": "passed" if names == TOOLS and bool(roundtrip.get("protocol_stdout_pure")) else "failed",
        "mcp_version": "2.2.0",
        "union_lock_sha256": mcp_union_lock_sha256(union_lock),
        "application_wheel_sha256": wheel_sha256 or _sha256(wheel),
        "tools_list": list(names),
        "initialize_capabilities_tools": bool(roundtrip.get("initialize_capabilities_tools")),
        "resources_exposed": bool(roundtrip.get("resources_exposed")),
        "prompts_exposed": bool(roundtrip.get("prompts_exposed")),
        "stdio_roundtrip": bool(names) and bool(roundtrip.get("initialize_capabilities_tools")),
        "workspace_isolation_probe": "passed" if roundtrip.get("workspace_isolation") else "failed",
        "protocol_stdout_pure": bool(roundtrip.get("protocol_stdout_pure")),
        "safe_tool_projection": bool(roundtrip.get("safe_calls")),
        "safe_error_path": bool(roundtrip.get("safe_error_path")),
        "process_clean": bool(roundtrip.get("process_clean")),
        "source_repository_unchanged": bool(roundtrip.get("source_repository_unchanged", False)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--union-lock", type=Path, default=ROOT / "distribution/mcp-windows-py312.lock")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--wheelhouse", type=Path)
    args = parser.parse_args(argv)
    before_status = subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=ROOT, text=True)
    validate_mcp_union_lock(ROOT / "distribution/runtime-windows-py312.lock", args.union_lock)
    with tempfile.TemporaryDirectory(prefix="w20c-mcp-acceptance-") as raw:
        temporary_dir = Path(raw)
        venv_dir = temporary_dir / "venv"
        app_home = temporary_dir / "app-home"
        foreign_workspace = temporary_dir / "foreign-workspace"
        foreign_workspace.mkdir()
        install_requirements = temporary_dir / "install-requirements.txt"
        wheel_sha256 = _hash_bound_install_input(args.union_lock, args.wheel, install_requirements)
        venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
        python = _python(venv_dir)
        install = [str(python), "-m", "pip", "install", "--only-binary=:all:", "--require-hashes"]
        if args.wheelhouse is not None:
            install.extend(("--no-index", "--find-links", str(args.wheelhouse)))
        install.extend(("-r", str(install_requirements)))
        subprocess.run(install, check=True, cwd=ROOT)
        installed_mcp = subprocess.check_output(
            [str(python), "-c", "import importlib.metadata; print(importlib.metadata.version('mcp'))"],
            cwd=args.workspace,
            text=True,
            env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        ).strip()
        if installed_mcp != "2.2.0":
            raise RuntimeError(f"unexpected installed mcp version: {installed_mcp}")
        foreign_run_id = _seed_foreign_run(
            python,
            app_home,
            foreign_workspace,
            temporary_dir / "foreign-run-seed.py",
        )
        probe = _roundtrip(python, args.workspace, app_home, foreign_run_id, foreign_workspace)
    probe["source_repository_unchanged"] = before_status == subprocess.check_output(
        ["git", "status", "--porcelain=v1"], cwd=ROOT, text=True
    )
    summary = acceptance_summary(
        wheel=args.wheel,
        union_lock=args.union_lock,
        workspace=args.workspace,
        roundtrip=probe,
        wheel_sha256=wheel_sha256,
    )
    summary["mcp_version"] = installed_mcp
    summary["acceptance"] = all(
        (
            summary["tools_list"] == list(TOOLS),
            summary["stdio_roundtrip"],
            summary["initialize_capabilities_tools"],
            not summary["resources_exposed"],
            not summary["prompts_exposed"],
            summary["safe_tool_projection"],
            summary["safe_error_path"],
            summary["workspace_isolation_probe"] == "passed",
            summary["process_clean"],
            summary["protocol_stdout_pure"],
            summary["source_repository_unchanged"],
        )
    )
    summary["status"] = "passed" if summary["acceptance"] else "failed"
    if args.summary_json:
        args.summary_json.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["acceptance"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
