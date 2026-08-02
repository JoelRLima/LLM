"""Allowlisted environment adapter for configuration v1."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from agent.runtime.config_errors import ConfigError

Parser = Callable[[str, str], Any]


def _parse_string(name: str, value: str) -> str:
    del name
    return value


def _parse_int(name: str, value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"Variável de ambiente '{name}' deve ser inteira.") from exc


def _parse_float(name: str, value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"Variável de ambiente '{name}' deve ser numérica.") from exc


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"Variável de ambiente '{name}' deve ser booleana.")


ENV_BINDINGS: dict[str, tuple[str, Parser]] = {
    "LLM_AGENT_API_URL": ("api_url", _parse_string),
    "LLM_AGENT_MODEL": ("model", _parse_string),
    "LLM_AGENT_TEMPERATURE": ("temperature", _parse_float),
    "LLM_AGENT_MAX_TOKENS": ("max_tokens", _parse_int),
    "LLM_AGENT_TIMEOUT": ("timeout", _parse_float),
    "LLM_AGENT_HARDWARE_PROFILE": ("hardware_profile", _parse_string),
    "LLM_AGENT_MAX_MODEL_CONCURRENCY": ("max_model_concurrency", _parse_int),
    "LLM_AGENT_MAX_IO_CONCURRENCY": ("max_io_concurrency", _parse_int),
    "LLM_AGENT_MAX_PROCESS_CONCURRENCY": ("max_process_concurrency", _parse_int),
    "LLM_AGENT_MAX_MODEL_CALLS": ("max_model_calls", _parse_int),
    "LLM_AGENT_DEFAULT_MODEL_PROFILE": ("default_model_profile", _parse_string),
    "LLM_AGENT_AUTO_CONFIRM": ("auto_confirm", _parse_bool),
    "LLM_AGENT_ENABLE_GBNF": ("ENABLE_GBNF", _parse_bool),
}


def environment_config(environment: Mapping[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for variable, (key, parser) in ENV_BINDINGS.items():
        if variable in environment:
            values[key] = parser(variable, environment[variable])
    return values
