"""One conversion path from runtime configuration to operational limits."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.llm.model_profile import resolve_model_profile
from agent.runtime.hardware import resolve_hardware_profile


def _packaged_defaults() -> Mapping[str, Any]:
    # Import lazily so the typed runtime modules do not create a configuration
    # import cycle during package bootstrap.
    from agent.runtime.config import DEFAULT_CONFIG

    return DEFAULT_CONFIG


def _positive(value: Any, fallback: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(fallback))


def runtime_limit_values(config: Mapping[str, Any] | None = None) -> dict[str, int]:
    """Materialize all limits without callers inventing fallback literals."""

    supplied = dict(config) if isinstance(config, Mapping) else None
    packaged = _packaged_defaults()
    source = dict(packaged) if supplied is None else supplied
    hardware = resolve_hardware_profile(source)

    profile_output = resolve_model_profile(source).max_output_tokens

    def configured(name: str, fallback: int) -> int:
        return _positive(source.get(name, fallback), fallback)

    return {
        "max_model_concurrency": configured(
            "max_model_concurrency", hardware.max_model_concurrency
        ),
        "max_io_concurrency": configured(
            "max_io_concurrency", hardware.max_io_concurrency
        ),
        "max_process_concurrency": configured(
            "max_process_concurrency", hardware.max_process_concurrency
        ),
        "max_steps": configured(
            "max_task_steps", int(packaged["max_task_steps"])
        ),
        "max_model_calls": configured(
            "max_model_calls", int(packaged["max_model_calls"])
        ),
        "max_task_tool_calls": configured(
            "max_task_tool_calls", int(packaged["max_task_tool_calls"])
        ),
        "max_task_tokens": configured(
            "max_task_tokens", int(packaged["max_task_tokens"])
        ),
        "max_task_wall_seconds": configured(
            "max_task_wall_seconds", int(packaged["max_task_wall_seconds"])
        ),
        "max_repeated_no_progress": configured(
            "max_repeated_no_progress", int(packaged["max_repeated_no_progress"])
        ),
        "max_consecutive_same_error": configured(
            "max_consecutive_same_error", int(packaged["max_consecutive_same_error"])
        ),
        "max_reasoning_turns": configured(
            "max_reasoning_turns", int(packaged["max_reasoning_turns"])
        ),
        "max_output_tokens": configured(
            "max_output_tokens",
            int(profile_output or hardware.default_output_tokens),
        ),
        "max_repair_attempts": configured(
            "max_repair_attempts", hardware.max_repair_attempts
        ),
    }


def default_runtime_limit_values() -> dict[str, int]:
    return runtime_limit_values(None)


def default_runtime_limit(name: str) -> int:
    try:
        return default_runtime_limit_values()[name]
    except KeyError as exc:
        raise KeyError(f"unknown runtime limit: {name}") from exc


__all__ = [
    "default_runtime_limit",
    "default_runtime_limit_values",
    "runtime_limit_values",
]
