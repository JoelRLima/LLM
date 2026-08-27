import json
import os
from copy import deepcopy
from typing import Any, Dict

from agent.runtime import paths
from agent.runtime.config_repository import packaged_config_defaults
from agent.runtime.config_validation import (
    ConfigValidator,
    validate_limits,
    validate_model_profiles,
    validate_root,
    validate_sections,
)


def _load_packaged_defaults() -> Dict[str, Any]:
    """Read the one authored default source used by runtime configuration."""

    return packaged_config_defaults()


# Keep the legacy module-level names as projections for callers that still
# import them; the packaged resource is the authored source.
_PACKAGED_DEFAULTS = _load_packaged_defaults()
DEFAULT_PROMPT = str(_PACKAGED_DEFAULTS["default_system_prompt"])
DEFAULT_VALIDATION = deepcopy(_PACKAGED_DEFAULTS["validation"])
DEFAULT_TASK_REPORT = deepcopy(_PACKAGED_DEFAULTS["task_report"])
DEFAULT_TASK_REPORT.setdefault("output_dir", paths.REPORTS_DIR)
DEFAULT_CODE_POLICY = deepcopy(_PACKAGED_DEFAULTS["code_policy"])
DEFAULT_COST_WATCHDOG = {
    key: _PACKAGED_DEFAULTS[key]
    for key in (
        "max_task_steps", "max_task_tokens", "max_task_tool_calls",
        "max_task_wall_seconds", "max_repeated_no_progress",
        "max_consecutive_same_error", "max_reasoning_turns",
    )
}
DEFAULT_CONFIG = deepcopy(_PACKAGED_DEFAULTS)
DEFAULT_CONFIG.setdefault("checkpoint_file", paths.CHECKPOINT_FILE)
DEFAULT_CONFIG["task_report"] = deepcopy(DEFAULT_TASK_REPORT)


def carregar_config(caminho: str = "config.json") -> Dict[str, Any]:
    """Carrega e normaliza a configuração pública da aplicação."""
    from agent.runtime.logging import logger

    if not os.path.exists(caminho):
        logger.error("O arquivo '%s' não foi encontrado!", caminho)
        raise FileNotFoundError(f"O arquivo '{caminho}' não foi encontrado!")
    with open(caminho, "r", encoding="utf-8") as source:
        config: Dict[str, Any] = json.load(source)

    validator = ConfigValidator(config, logger)
    validate_root(validator, DEFAULT_CONFIG)
    validate_model_profiles(validator)
    validate_limits(validator, DEFAULT_COST_WATCHDOG)
    validate_sections(
        validator,
        DEFAULT_VALIDATION,
        DEFAULT_CODE_POLICY,
        DEFAULT_TASK_REPORT,
    )
    return config
