"""Temporary import compatibility for the moved Application owner."""

from llm_agent.application.workspace_recents import list_recent_workspaces as load_recent_workspaces
from llm_agent.application.workspace_recents import remember_recent_workspace

__all__ = ["load_recent_workspaces", "remember_recent_workspace"]
