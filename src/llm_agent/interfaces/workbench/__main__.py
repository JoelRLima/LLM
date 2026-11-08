"""Launch explicitly against a selected workspace and the existing configuration."""

from __future__ import annotations

import argparse
import tkinter as tk
from pathlib import Path

from llm_agent.application.context import AppPaths, WorkspaceContext
from llm_agent.application.task_execution import TaskExecutionStart
from llm_agent.interfaces.workbench.session import WorkbenchSession
from llm_agent.interfaces.workbench.window import WorkbenchWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM Agent Workbench técnico (fatia experimental)")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--profile")
    args = parser.parse_args()
    workspace = WorkspaceContext.create(args.workspace)
    start = TaskExecutionStart(workspace.root, AppPaths.discover(app_home=args.home),
                               config_path=args.config, profile=args.profile)
    root = tk.Tk()
    WorkbenchWindow(root, WorkbenchSession(start))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
