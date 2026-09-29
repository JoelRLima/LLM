import json
import os
from copy import deepcopy
from typing import Any, Dict, cast

from llm_agent.agent.runtime import paths
from llm_agent.agent.runtime.config_repository import packaged_config_defaults
from llm_agent.agent.runtime.config_validation import (
    ConfigValidator,
    validate_limits,
    validate_model_profiles,
    validate_root,
    validate_sections,
)
from llm_agent.workspace.paths import WorkspacePaths


def _load_packaged_defaults() -> Dict[str, Any]:
    """Read the one authored default source used by runtime configuration."""

    return packaged_config_defaults()


# Keep the legacy module-level names as projections for callers that still
# import them; the packaged resource is the authored source.
_PACKAGED_DEFAULTS = _load_packaged_defaults()
AUTHORED_CONFIG_DEFAULTS = deepcopy(_PACKAGED_DEFAULTS)
DEFAULT_PROMPT = str(_PACKAGED_DEFAULTS["default_system_prompt"])
DEFAULT_VALIDATION = deepcopy(_PACKAGED_DEFAULTS["validation"])
DEFAULT_CODE_POLICY = deepcopy(_PACKAGED_DEFAULTS["code_policy"])
DEFAULT_COST_WATCHDOG = {
    key: _PACKAGED_DEFAULTS[key]
    for key in (
        "max_task_steps", "max_task_tokens", "max_task_tool_calls",
        "max_task_wall_seconds", "max_repeated_no_progress",
        "max_consecutive_same_error", "max_reasoning_turns",
        "max_no_progress_plateau",
    )
}
def _runtime_path_defaults(
    workspace_paths: WorkspacePaths | None = None,
) -> tuple[str, str]:
    if workspace_paths is not None:
        return str(workspace_paths.checkpoint_file), str(workspace_paths.reports_dir)
    return cast(tuple[str, str], paths.legacy_config_path_defaults())


def _runtime_defaults(
    workspace_paths: WorkspacePaths | None = None,
) -> Dict[str, Any]:
    """Project authored defaults with paths resolved for one runtime boundary."""

    checkpoint_file, reports_dir = _runtime_path_defaults(workspace_paths)
    defaults = deepcopy(_PACKAGED_DEFAULTS)
    defaults["checkpoint_file"] = checkpoint_file
    task_report = deepcopy(defaults["task_report"])
    task_report.setdefault("output_dir", reports_dir)
    defaults["task_report"] = task_report
    return defaults


# Public legacy projections retain their historical no-argument behavior.  The
# packaged authored defaults above remain free of machine-specific paths.
DEFAULT_TASK_REPORT = deepcopy(_runtime_defaults()["task_report"])
DEFAULT_CONFIG = _runtime_defaults()


def carregar_config(
    caminho: str = "config.json",
    *,
    workspace_paths: WorkspacePaths | None = None,
) -> Dict[str, Any]:
    """Carrega e normaliza a configuração pública da aplicação."""
    from llm_agent.agent.runtime.logging import logger

    defaults = _runtime_defaults(workspace_paths)

    if not os.path.exists(caminho):
        logger.error("O arquivo '%s' não foi encontrado!", caminho)
        raise FileNotFoundError(f"O arquivo '{caminho}' não foi encontrado!")
    with open(caminho, "r", encoding="utf-8") as source:
        config: Dict[str, Any] = json.load(source)

    validator = ConfigValidator(config, logger)
    validate_root(validator, defaults)
    validate_model_profiles(validator)
    validate_limits(validator, DEFAULT_COST_WATCHDOG)
    validate_sections(
        validator,
        DEFAULT_VALIDATION,
        DEFAULT_CODE_POLICY,
        defaults["task_report"],
    )
    return config
