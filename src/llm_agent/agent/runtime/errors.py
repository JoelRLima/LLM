"""Runtime-owned exception contracts shared across execution layers."""


class ToolNotFoundError(Exception):
    """Raised when a requested tool is absent from the active registry."""


__all__ = ["ToolNotFoundError"]
