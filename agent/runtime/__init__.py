"""Contratos de runtime independentes do orquestrador."""

from agent.runtime.context import (
    Artifact,
    RuntimeLimits,
    TaskExecutionContext,
    TaskResult,
    TaskStatus,
)
from agent.runtime.hardware import HardwareProfile, resolve_hardware_profile

__all__ = [
    "Artifact",
    "HardwareProfile",
    "RuntimeLimits",
    "TaskExecutionContext",
    "TaskResult",
    "TaskStatus",
    "resolve_hardware_profile",
]
