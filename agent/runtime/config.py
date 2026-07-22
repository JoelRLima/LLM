import json
import os
from typing import Any, Dict

from agent.runtime import paths
from agent.runtime.config_validation import (
    ConfigValidator,
    validate_limits,
    validate_model_profiles,
    validate_root,
    validate_sections,
)
from agent.runtime.hardware import LOW_VRAM_8GB

DEFAULT_PROMPT = (
    "You are a helpful assistant. Always think and reason in English. "
    "Your final response must be in Portuguese (Brazil), natural and fluent. "
    "Do not mention the language switch."
)
DEFAULT_VALIDATION: Dict[str, Any] = {
    "enabled": True, "ruff": False, "mypy": False, "pytest": False,
    "pytest_dir": "tests/", "fail_triggers_replan": False,
}
DEFAULT_TASK_REPORT: Dict[str, Any] = {
    "enabled": True, "format": "json", "output_dir": paths.REPORTS_DIR,
}
DEFAULT_CODE_POLICY: Dict[str, Any] = {
    "auto_apply_min_confidence": 0.85,
    "max_auto_files": 2,
    "require_target_alignment": True,
}
DEFAULT_COST_WATCHDOG: Dict[str, Any] = {
    "max_task_steps": 30,
    "max_task_tokens": 200000,
    "max_task_tool_calls": 60,
    "max_task_wall_seconds": 1800,
    "max_repeated_no_progress": 3,
    "max_consecutive_same_error": 3,
}
DEFAULT_CONFIG: Dict[str, Any] = {
    "api_url": "http://127.0.0.1:8080/v1/chat/completions",
    "model": "default",
    "temperature": 0.6,
    "max_tokens": 4096,
    "timeout": 300,
    "hardware_profile": LOW_VRAM_8GB.name,
    "max_model_concurrency": LOW_VRAM_8GB.max_model_concurrency,
    "max_io_concurrency": LOW_VRAM_8GB.max_io_concurrency,
    "max_process_concurrency": LOW_VRAM_8GB.max_process_concurrency,
    "max_model_calls": 20,
    "default_system_prompt": DEFAULT_PROMPT,
    "validation": DEFAULT_VALIDATION,
    "checkpoint_file": paths.CHECKPOINT_FILE,
    "task_report": DEFAULT_TASK_REPORT,
    "code_policy": DEFAULT_CODE_POLICY,
    "ENABLE_GBNF": True,
    "auto_confirm": False,
    "resume_retry_failed": False,
    "resume_retry_skipped": False,
    **DEFAULT_COST_WATCHDOG,
}


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
