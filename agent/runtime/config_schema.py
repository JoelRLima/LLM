"""Strict, side-effect-free validation for standalone configuration v1."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from agent.runtime.config_errors import ConfigError, ConfigVersionError
from agent.runtime.hardware import HARDWARE_PROFILES

SCHEMA_VERSION = 1
Validator = Callable[[Any], bool]


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_int_at_least(minimum: int) -> Validator:
    return lambda value: isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_number_between(minimum: float, maximum: float | None = None) -> Validator:
    def validate(value: Any) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return value >= minimum and (maximum is None or value <= maximum)

    return validate


def _is_string(value: Any) -> bool:
    return isinstance(value, str)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


ROOT_FIELDS: dict[str, Validator] = {
    "api_url": _is_non_empty_string,
    "model": _is_non_empty_string,
    "temperature": _is_number_between(0.0, 2.0),
    "max_tokens": _is_int_at_least(1),
    "agent_max_tokens": lambda value: value is None or _is_int_at_least(1)(value),
    "timeout": _is_number_between(1.0),
    "hardware_profile": lambda value: isinstance(value, str) and value in HARDWARE_PROFILES,
    "semantic_memory_enabled": _is_bool,
    "semantic_memory_model": _is_non_empty_string,
    "max_model_concurrency": _is_int_at_least(1),
    "max_io_concurrency": _is_int_at_least(1),
    "max_process_concurrency": _is_int_at_least(1),
    "max_model_calls": _is_int_at_least(1),
    "max_task_steps": _is_int_at_least(1),
    "max_task_tokens": _is_int_at_least(1),
    "max_task_tool_calls": _is_int_at_least(1),
    "max_task_wall_seconds": _is_int_at_least(1),
    "max_reasoning_turns": _is_int_at_least(1),
    "max_repeated_no_progress": _is_int_at_least(1),
    "max_consecutive_same_error": _is_int_at_least(1),
    "default_system_prompt": _is_string,
    "ENABLE_GBNF": _is_bool,
    "auto_confirm": _is_bool,
    "resume_retry_failed": _is_bool,
    "resume_retry_skipped": _is_bool,
    "default_model_profile": _is_non_empty_string,
}

SECTION_FIELDS: dict[str, dict[str, Validator]] = {
    "validation": {
        "enabled": _is_bool,
        "ruff": _is_bool,
        "mypy": _is_bool,
        "pytest": _is_bool,
        "pytest_dir": _is_non_empty_string,
        "fail_triggers_replan": _is_bool,
    },
    "code_policy": {
        "auto_apply_min_confidence": _is_number_between(0.0, 1.0),
        "max_auto_files": _is_int_at_least(1),
        "require_target_alignment": _is_bool,
    },
    "task_report": {
        "enabled": _is_bool,
        "format": lambda value: value in {"json", "markdown"},
    },
}

PROFILE_FIELDS: dict[str, Validator] = {
    "provider": _is_non_empty_string,
    "model": _is_non_empty_string,
    "base_url": _is_non_empty_string,
    "api_url": _is_non_empty_string,
    "temperature": _is_number_between(0.0, 2.0),
    "max_tokens": _is_int_at_least(1),
    "timeout": _is_number_between(1.0),
}

CAPABILITY_FIELDS: dict[str, Validator] = {
    "streaming": _is_bool,
    "structured_output": lambda value: value in {"json_schema", "gbnf", "json_prompt"},
    "reasoning": _is_bool,
    "token_counting": _is_bool,
    "tool_calls": _is_bool,
}


def _fail(path: str, message: str) -> None:
    raise ConfigError(f"Configuração inválida em '{path}': {message}.")


def _validate_fields(
    data: Mapping[str, Any],
    fields: Mapping[str, Validator],
    path: str,
    *,
    require_all: bool,
    optional_keys: frozenset[str] = frozenset(),
) -> None:
    unknown = sorted(set(data) - set(fields))
    if unknown:
        _fail(path, "campos desconhecidos: " + ", ".join(unknown))
    if require_all:
        missing = sorted(set(fields) - set(data) - optional_keys)
        if missing:
            _fail(path, "campos ausentes: " + ", ".join(missing))
    for key, value in data.items():
        if not fields[key](value):
            _fail(f"{path}.{key}", f"valor ou tipo não aceito ({value!r})")


def _validate_sections(document: Mapping[str, Any], *, require_all: bool) -> None:
    for name, fields in SECTION_FIELDS.items():
        if name not in document:
            if require_all:
                _fail("$", f"seção ausente: {name}")
            continue
        section = document[name]
        if not isinstance(section, Mapping):
            _fail(name, "deve ser um objeto")
        _validate_fields(
            section,
            fields,
            name,
            require_all=require_all,
        )


def _validate_capabilities(
    capabilities: Any,
    profile_path: str,
) -> None:
    if not isinstance(capabilities, Mapping):
        _fail(f"{profile_path}.capabilities", "deve ser um objeto")
    _validate_fields(
        capabilities,
        CAPABILITY_FIELDS,
        f"{profile_path}.capabilities",
        require_all=False,
    )


def _validate_profile_fields(profile: Mapping[str, Any], path: str) -> None:
    allowed = set(PROFILE_FIELDS) | {"capabilities", "provider_options"}
    unknown = sorted(set(profile) - allowed)
    if unknown:
        _fail(path, "campos desconhecidos: " + ", ".join(unknown))
    for key, value in profile.items():
        validator = PROFILE_FIELDS.get(key)
        if validator is not None and not validator(value):
            _fail(f"{path}.{key}", f"valor ou tipo não aceito ({value!r})")


def _validate_profile_options(profile: Mapping[str, Any], path: str) -> None:
    if "capabilities" in profile:
        _validate_capabilities(profile["capabilities"], path)
    if "provider_options" in profile and not isinstance(
        profile["provider_options"], Mapping
    ):
        _fail(f"{path}.provider_options", "deve ser um objeto")


def _validate_profile(
    name: Any,
    raw_profile: Any,
    *,
    require_all: bool,
) -> None:
    path = f"model_profiles.{name}"
    if not isinstance(name, str) or not name.strip() or not isinstance(raw_profile, Mapping):
        _fail(path, "nome e perfil devem ser válidos")
    _validate_profile_fields(raw_profile, path)
    _validate_profile_options(raw_profile, path)
    if require_all:
        missing = sorted({"provider", "model"} - set(raw_profile))
        if missing:
            _fail(path, "campos ausentes: " + ", ".join(missing))


def _validate_profiles(document: Mapping[str, Any], *, require_all: bool) -> None:
    raw_profiles = document.get("model_profiles")
    if raw_profiles is None:
        if require_all:
            _fail("$", "seção ausente: model_profiles")
        return
    if not isinstance(raw_profiles, Mapping):
        _fail("model_profiles", "deve ser um objeto")
    for name, raw_profile in raw_profiles.items():
        _validate_profile(name, raw_profile, require_all=require_all)


def validate_config_document(
    document: Any,
    *,
    require_version: bool,
    require_complete: bool,
) -> None:
    """Validate one config layer without coercion or silent fallback."""

    if not isinstance(document, Mapping):
        _fail("$", "a raiz deve ser um objeto")
    version = document.get("schema_version")
    if require_version and version is None:
        raise ConfigVersionError("'schema_version' é obrigatório.")
    if version is not None:
        if isinstance(version, bool) or not isinstance(version, int):
            raise ConfigVersionError("'schema_version' deve ser inteiro.")
        if version != SCHEMA_VERSION:
            direction = "futura" if version > SCHEMA_VERSION else "antiga"
            raise ConfigVersionError(
                f"Versão {version} é {direction}; esta aplicação aceita {SCHEMA_VERSION}."
            )

    structural = {"schema_version", "model_profiles", *SECTION_FIELDS}
    root_values = {
        key: value
        for key, value in document.items()
        if key not in structural
    }
    _validate_fields(
        root_values,
        ROOT_FIELDS,
        "$",
        require_all=require_complete,
        optional_keys=frozenset({"agent_max_tokens"}),
    )
    _validate_sections(document, require_all=require_complete)
    _validate_profiles(document, require_all=require_complete)
    if require_complete:
        default_profile = document["default_model_profile"]
        profiles = document["model_profiles"]
        if default_profile not in profiles:
            _fail(
                "default_model_profile",
                f"perfil inexistente: {default_profile}",
            )
