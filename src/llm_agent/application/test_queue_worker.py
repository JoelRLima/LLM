"""Detached queue launch; scheduling, locks, controls and recovery stay canonical."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any

from llm_agent.application.test_queue_service import QueueExecutionRequest, TestQueueService, validate_execution_request
from llm_agent.filesystem.path_safety import assert_no_link_ancestors
from llm_agent.process.detached import launch_detached


def start_queue_worker(service: TestQueueService, request: QueueExecutionRequest) -> dict[str, Any]:
    validate_execution_request(request)
    preflight = service.check(config_path=request.config_path, profile=request.profile,
                              automatic_approval=request.automatic_approval, case_seconds=request.case_seconds)
    if preflight.get("runner_active"):
        raise RuntimeError("Uma execução já possui esta fila.")
    configuration = preflight["configuration"]
    identity = configuration["execution_identity"]
    if configuration.get("resume_compatible") is False or request.expected_identity not in {None, identity}:
        raise ValueError("A configuração ou o código mudou; confira a preparação novamente.")
    argv = [sys.executable, "-m", "llm_agent.interfaces.test_queue", "run", str(service.root),
            "--config", str(request.config_path.resolve()), "--live", "--expected-identity", identity,
            "--case-seconds", str(request.case_seconds), "--total-seconds", str(request.total_seconds)]
    if request.profile:
        argv += ["--profile", request.profile]
    if request.automatic_approval:
        argv += ["--yes"]
    if request.skip_interrupted:
        argv += ["--skip-interrupted"]
    directory = service.root / "workers"
    assert_no_link_ancestors(directory)
    directory.mkdir(exist_ok=True)
    output = directory / f"{uuid.uuid4().hex}.log"
    env = dict(os.environ)
    source = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    pid = launch_detached(argv, cwd=service.root, output=output, env=env)
    return {"status": "starting", "pid": pid, "queue": str(service.root), "log": str(output),
            "execution_identity": identity,
            "message": "Worker solicitado. Consulte status para confirmar a execução; pause/stop continuam disponíveis após fechar a interface."}
