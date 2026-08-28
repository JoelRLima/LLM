from agent.runtime.errors import ToolNotFoundError


class InvalidToolError(Exception):
    """Exception raised when a tool exists but cannot be executed."""


class PlanExecutionError(Exception):
    """Generic plan execution error."""


__all__ = ["InvalidToolError", "PlanExecutionError", "ToolNotFoundError"]
