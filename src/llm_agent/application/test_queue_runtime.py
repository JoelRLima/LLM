"""Test-queue adapter to the canonical task use cases and bounded process executor."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from llm_agent.agent.llm.model_profile import resolve_model_profile
from llm_agent.agent.runtime.config_environment import ENV_BINDINGS
from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application.context import AppPaths
from llm_agent.application.task_execution import (
    TaskExecutionStart,
    close_task_execution,
    execute_submission,
    observe_task_settlement,
    start_headless_task,
)
from llm_agent.application.test_queue import Cancellation
from llm_agent.application.test_queue_manifest import TestCase, digest, read_document
from llm_agent.execution.command import CommandExecutor, CommandRequest
from llm_agent.storage.json_persistence import write_json_atomic

CAPS = {"max_model_calls": 20, "max_task_steps": 30, "max_task_tokens": 200000,
        "max_task_tool_calls": 30, "max_task_wall_seconds": 300}


def prepare_config(path: Path, profile: str | None) -> dict[str, Any]:
    overrides = {"default_model_profile": profile} if profile else None
    config: dict[str, Any] = ConfigRepository(config_path=path).load(overrides=overrides).to_dict()
    for key, cap in CAPS.items():
        value = config.get(key)
        config[key] = min(value, cap) if type(value) is int and value > 0 else cap
    return config


def configuration_preview(path: Path, profile: str | None, *, automatic_approval: bool,
                          case_seconds: int, previous_identity: str | None) -> dict[str, Any]:
    """Resolve authored settings and resume compatibility without creating a gateway."""
    if type(case_seconds) is not int or not 1 <= case_seconds <= 3600:
        raise ValueError("case_seconds must be 1..3600")
    config = prepare_config(path, profile)
    model = resolve_model_profile(config)
    identity = runtime_identity(config, automatic_approval=automatic_approval, case_seconds=case_seconds)
    return {"profile": model.name, "provider": model.provider, "model": model.model,
            "automatic_approval": automatic_approval, "case_timeout_seconds": case_seconds,
            "task_budgets": {key: config[key] for key in CAPS},
            "execution_identity": identity,
            "resume_compatible": previous_identity is None or previous_identity == identity}


def runtime_identity(config: dict[str, Any], *, automatic_approval: bool, case_seconds: int) -> str:
    package = Path(__file__).resolve().parents[1]
    source = hashlib.sha256()
    for path in sorted(package.rglob("*")):
        if path.suffix in {".py", ".json"} and path.is_file() and "__pycache__" not in path.parts:
            source.update(path.relative_to(package).as_posix().encode())
            source.update(hashlib.sha256(path.read_bytes()).digest())
    return digest({"runtime": source.hexdigest(), "config": config, "automatic_approval": automatic_approval,
                   "case_seconds": case_seconds})


class RuntimeExecutor:
    def __init__(self, config: dict[str, Any], *, automatic_approval: bool = False,
                 expected_identity: str | None = None, case_seconds: int = 0) -> None:
        self.config, self.automatic_approval = config, automatic_approval
        self.case_seconds = case_seconds
        # The service may pass the identity it just checked and persisted. Each
        # case still hashes the runtime before and after execution against it.
        self._identity = (expected_identity if expected_identity is not None else
                          runtime_identity(config, automatic_approval=automatic_approval,
                                           case_seconds=case_seconds))

    def __call__(self, case: TestCase, workspace: Path, cancellation: Cancellation, seconds: int) -> dict[str, Any]:
        if runtime_identity(self.config, automatic_approval=self.automatic_approval,
                            case_seconds=self.case_seconds) != self._identity:
            raise RuntimeError("Runtime/configuration changed during test queue")
        result_path = workspace.parent / "execution.json"
        package_parent = str(Path(__file__).resolve().parents[2])
        bootstrap = "import sys; sys.path.insert(0,sys.argv[1]); from llm_agent.application.test_queue_runtime import worker; worker()"
        payload = {"workspace": str(workspace), "result": str(result_path), "prompt": case.prompt,
                   "config": self.config, "automatic_approval": self.automatic_approval}
        # Environment configuration was already materialized once by prepare_config.
        environment = {key: value for key, value in os.environ.items() if key not in ENV_BINDINGS and key not in {"PYTHONPATH", "PYTHONHOME"}}
        result = CommandExecutor().execute(CommandRequest(
            sys.executable, ("-I", "-c", bootstrap, package_parent), cwd=workspace.parent,
            environment=environment, stdin=json.dumps(payload, allow_nan=False).encode(),
            timeout_seconds=seconds, cancellation=cancellation))
        (workspace.parent / "stdout.log").write_bytes(result.stdout)
        (workspace.parent / "stderr.log").write_bytes(result.stderr)
        process = {"return_code": result.return_code, "duration_seconds": result.duration_seconds,
                   "stdout_truncated": result.stdout_truncated, "stderr_truncated": result.stderr_truncated}
        if result.cancelled or result.timed_out:
            return {"status": "interrupted" if result.cancelled else "timeout", "success": False, "process": process}
        if result.return_code != 0 or not result_path.is_file():
            return {"status": "error", "success": False, "process": process}
        observation = read_document(result_path)
        if runtime_identity(self.config, automatic_approval=self.automatic_approval,
                            case_seconds=self.case_seconds) != self._identity:
            return {"status": "error", "success": False, "error": "Runtime changed during case", "process": process}
        return {**observation, "process": process}


def worker() -> None:
    """Internal subprocess entry: one fresh runtime and application home per case."""
    payload = json.load(sys.stdin)
    workspace = Path(payload["workspace"])
    paths = AppPaths.discover(app_home=workspace.parent / "home")
    config_path = workspace.parent / "runtime-config.json"
    write_json_atomic(config_path, payload["config"])
    runtime = start_headless_task(TaskExecutionStart(workspace, paths, config_path=config_path,
                                                   configure_logging=False),
                                  automatic_approval=payload["automatic_approval"])
    try:
        settlement = execute_submission(runtime, payload["prompt"], entry="agent")
        observation = observe_task_settlement(settlement, channel="interactive")
        result = {"status": observation["status"], "success": observation["success"],
                  "answer": observation["answer"][:16000], "error": observation.get("error"),
                  "report_path": observation.get("report_path"),
                  "receipt": observation.get("receipt"), "run_id": observation.get("run_id")}
        write_json_atomic(payload["result"], result)
    finally:
        close_task_execution(runtime)
