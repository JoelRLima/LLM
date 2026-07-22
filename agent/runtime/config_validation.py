from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Tuple, Type, Union

from agent.runtime.hardware import HARDWARE_PROFILES, LOW_VRAM_8GB

ExpectedType = Union[Type[Any], Tuple[Type[Any], ...]]


@dataclass
class ConfigValidator:
    config: MutableMapping[str, Any]
    logger: Any

    def value(
        self,
        key: str,
        expected: ExpectedType,
        *,
        fallback: Any,
        minimum: int | float | None = None,
        maximum: int | float | None = None,
        target: MutableMapping[str, Any] | None = None,
        prefix: str = "",
    ) -> None:
        destination = target if target is not None else self.config
        name = f"{prefix}{key}"
        current = destination.get(key)
        allowed = expected if isinstance(expected, tuple) else (expected,)
        invalid_bool = isinstance(current, bool) and bool not in allowed
        if current is None or invalid_bool or not isinstance(current, expected):
            destination[key] = fallback
            self.logger.warning("'%s' inválido ou ausente. Usando valor padrão: %s", name, fallback)
            return
        if minimum is not None and current < minimum:
            destination[key] = fallback
            self.logger.warning("'%s' muito baixo. Usando %s.", name, fallback)
        elif maximum is not None and current > maximum:
            destination[key] = fallback
            self.logger.warning("'%s' muito alto. Usando %s.", name, fallback)

    def section(self, name: str) -> MutableMapping[str, Any]:
        raw = self.config.get(name)
        if isinstance(raw, dict):
            return raw
        if name in self.config:
            self.logger.warning("'%s' deve ser um objeto. Usando valores padrão.", name)
        section: MutableMapping[str, Any] = {}
        self.config[name] = section
        return section


def validate_root(validator: ConfigValidator, defaults: Mapping[str, Any]) -> None:
    specs: tuple[tuple[str, ExpectedType, int | float | None, int | float | None], ...] = (
        ("api_url", str, None, None), ("model", str, None, None),
        ("temperature", (int, float), 0.0, 2.0), ("max_tokens", int, 1, None),
        ("timeout", (int, float), 1, None), ("hardware_profile", str, None, None),
        ("max_model_concurrency", int, 1, None), ("max_io_concurrency", int, 1, None),
        ("max_process_concurrency", int, 1, None), ("max_model_calls", int, 1, None),
        ("default_system_prompt", str, None, None), ("checkpoint_file", str, None, None),
        ("ENABLE_GBNF", bool, None, None), ("auto_confirm", bool, None, None),
        ("resume_retry_failed", bool, None, None), ("resume_retry_skipped", bool, None, None),
    )
    for key, expected, minimum, maximum in specs:
        validator.value(key, expected, fallback=defaults[key], minimum=minimum, maximum=maximum)
    if validator.config["hardware_profile"] not in HARDWARE_PROFILES:
        validator.logger.warning("'hardware_profile' desconhecido. Usando %s.", defaults["hardware_profile"])
        validator.config["hardware_profile"] = defaults["hardware_profile"]


def validate_limits(validator: ConfigValidator, defaults: Mapping[str, Any]) -> None:
    for key, fallback in defaults.items():
        validator.value(key, int, fallback=fallback, minimum=1)


def _normalize_capabilities(capabilities: MutableMapping[str, Any], name: str, logger: Any) -> None:
    for key in ("streaming", "reasoning", "token_counting", "tool_calls"):
        if key in capabilities and not isinstance(capabilities[key], bool):
            logger.warning("'model_profiles.%s.capabilities.%s' deve ser booleano.", name, key)
            capabilities[key] = False
    if capabilities.get("structured_output") not in {None, "json_schema", "gbnf", "json_prompt"}:
        logger.warning("Saída estruturada inválida no perfil '%s'; usando json_prompt.", name)
        capabilities["structured_output"] = "json_prompt"


def _normalize_profile_strings(profile: MutableMapping[str, Any], name: str, logger: Any) -> None:
    for key, fallback in (("provider", "openai_compatible"), ("model", "default")):
        if key in profile and not isinstance(profile[key], str):
            logger.warning("'model_profiles.%s.%s' deve ser string. Usando %s.", name, key, fallback)
            profile[key] = fallback
    for key in ("base_url", "api_url"):
        if key in profile and not isinstance(profile[key], str):
            logger.warning("'model_profiles.%s.%s' deve ser string; removendo.", name, key)
            profile.pop(key)


def _normalize_profile_numbers(profile: MutableMapping[str, Any], name: str, logger: Any) -> None:
    numeric = {
        "temperature": (0.2, 0.0, 2.0),
        "max_tokens": (LOW_VRAM_8GB.default_output_tokens, 1.0, None),
        "timeout": (300, 1.0, None),
    }
    for key, (fallback, minimum, maximum) in numeric.items():
        value = profile.get(key)
        in_range = False
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric_value = float(value)
            in_range = numeric_value >= minimum and (
                maximum is None or numeric_value <= maximum
            )
        if value is not None and not in_range:
            logger.warning("'model_profiles.%s.%s' inválido. Usando %s.", name, key, fallback)
            profile[key] = fallback


def _normalize_profile(name: str, raw: Mapping[str, Any], logger: Any) -> dict[str, Any]:
    profile = dict(raw)
    _normalize_profile_strings(profile, name, logger)
    _normalize_profile_numbers(profile, name, logger)
    capabilities = profile.get("capabilities")
    if capabilities is not None and not isinstance(capabilities, dict):
        logger.warning("'model_profiles.%s.capabilities' deve ser objeto; usando vazio.", name)
        capabilities = {}
        profile["capabilities"] = capabilities
    if isinstance(capabilities, dict):
        _normalize_capabilities(capabilities, name, logger)
    if "provider_options" in profile and not isinstance(profile["provider_options"], dict):
        logger.warning("'model_profiles.%s.provider_options' deve ser objeto; usando vazio.", name)
        profile["provider_options"] = {}
    return profile


def validate_model_profiles(validator: ConfigValidator) -> None:
    profiles = validator.config.get("model_profiles")
    default_name = validator.config.get("default_model_profile")
    if profiles is not None and not isinstance(profiles, dict):
        validator.logger.warning("'model_profiles' deve ser um objeto. Usando configuração legada.")
        profiles = {}
    if default_name is not None and not isinstance(default_name, str):
        validator.logger.warning("'default_model_profile' deve ser string. Usando configuração legada.")
        validator.config.pop("default_model_profile", None)
        default_name = None
    normalized: dict[str, Any] = {}
    if isinstance(profiles, dict):
        for name, raw in profiles.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(raw, dict):
                validator.logger.warning("Perfil de modelo inválido ignorado: %r.", name)
                continue
            normalized[name] = _normalize_profile(name, raw, validator.logger)
        validator.config["model_profiles"] = normalized
    if default_name and default_name not in normalized:
        validator.logger.warning("Perfil de modelo '%s' não existe; usando configuração legada.", default_name)
        validator.config.pop("default_model_profile", None)


def validate_sections(
    validator: ConfigValidator,
    validation_defaults: Mapping[str, Any],
    code_defaults: Mapping[str, Any],
    report_defaults: Mapping[str, Any],
) -> None:
    validation = validator.section("validation")
    for key, expected in (("enabled", bool), ("ruff", bool), ("mypy", bool), ("pytest", bool), ("pytest_dir", str), ("fail_triggers_replan", bool)):
        validator.value(key, expected, fallback=validation_defaults[key], target=validation, prefix="validation.")
    policy = validator.section("code_policy")
    validator.value("auto_apply_min_confidence", (int, float), fallback=code_defaults["auto_apply_min_confidence"], minimum=0.0, maximum=1.0, target=policy, prefix="code_policy.")
    validator.value("max_auto_files", int, fallback=code_defaults["max_auto_files"], minimum=1, target=policy, prefix="code_policy.")
    validator.value("require_target_alignment", bool, fallback=code_defaults["require_target_alignment"], target=policy, prefix="code_policy.")
    report = validator.section("task_report")
    for key, expected in (("enabled", bool), ("format", str), ("output_dir", str)):
        validator.value(key, expected, fallback=report_defaults[key], target=report, prefix="task_report.")
    if report["format"] not in ("json", "markdown"):
        validator.logger.warning("'task_report.format' inválido. Usando %s.", report_defaults["format"])
        report["format"] = report_defaults["format"]
