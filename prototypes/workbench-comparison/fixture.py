"""Shared deterministic Agent session used by the Tk and pywebview candidates."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from llm_agent.agent.application import AgentApplication  # noqa: E402
from llm_agent.agent.runtime.config_repository import ConfigRepository  # noqa: E402
from llm_agent.agent.tools.authority import OperationalMode  # noqa: E402
from llm_agent.application.task_execution import TaskExecutionStart, _retain_runtime  # noqa: E402
from llm_agent.interfaces.workbench import session as session_module  # noqa: E402
from llm_agent.interfaces.workbench.session import WorkbenchSession  # noqa: E402
from llm_agent.workspace.paths import AppPaths  # noqa: E402
from tests.integration.test_agent_application_evaluation import JourneyGateway  # noqa: E402

OBJECTIVE = "CAP_READ: leia notes.txt e informe CAP_READ_EVIDENCE."


def create_session() -> tuple[WorkbenchSession, Path, Path]:
    """Wire the real Application/Agent/reporting path to an offline gateway."""
    root = Path(tempfile.mkdtemp(prefix="workbench-comparison-"))
    workspace = root / "agent-workbench"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("CAP_READ_EVIDENCE\n", encoding="utf-8")
    paths = AppPaths.discover(root / "home", env={})
    ConfigRepository(paths).initialize()
    gateway = JourneyGateway(OBJECTIVE)

    def start(request: Any, approval: Any) -> Any:
        from llm_agent.application.approval import _bind_approval_presenter

        return _retain_runtime(
            AgentApplication.create(
                paths=request.app_paths,
                workspace=request.workspace,
                gateway=gateway,
                approval_policy=_bind_approval_presenter(approval),
                operational_mode=OperationalMode.READ_ONLY,
                configure_logging=False,
            )
        )

    session_module.start_interactive_session = start
    session = WorkbenchSession(TaskExecutionStart(workspace, paths))
    return session, workspace, root
