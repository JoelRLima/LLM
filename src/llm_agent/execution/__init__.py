"""Platform execution primitives without Agent authority semantics."""

from .command import (
    CommandExecutionError,
    CommandExecutor,
    CommandRequest,
    CommandResult,
)

__all__ = [
    "CommandExecutionError",
    "CommandExecutor",
    "CommandRequest",
    "CommandResult",
]
