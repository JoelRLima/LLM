"""Application-owned path surface for interface composition."""

from llm_agent.filesystem.path_safety import resolve_workspace_path
from llm_agent.filesystem.primitives import inspect_final_path

__all__ = ["inspect_final_path", "resolve_workspace_path"]
