import json
import os
from typing import Any, Dict, Tuple, Union, Type

DEFAULT_PROMPT: str = (
    "You are a helpful assistant. "
    "Always think and reason in English. "
    "Your final response must be in Portuguese (Brazil), natural and fluent. "
    "Do not mention the language switch."
)

DEFAULT_VALIDATION: Dict[str, Any] = {
    "enabled": True,
    "ruff": False,
    "mypy": False,
    "pytest": False,
    "pytest_dir": "tests/",
    "fail_triggers_replan": False
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "api_url": "http://127.0.0.1:8080/v1/chat/completions",
    "model": "default",
    "temperature": 0.6,
    "max_tokens": 4096,
    "timeout": 300,
    "default_system_prompt": DEFAULT_PROMPT,
    "validation": DEFAULT_VALIDATION,
    "checkpoint_file": "agent_checkpoint.json"
}

def carregar_config(caminho: str = "config.json") -> Dict[str, Any]:
    """Carrega e valida o arquivo de configuração, aplicando fallbacks com avisos."""
    from logger import logger
    
    if not os.path.exists(caminho):
        logger.error(f"O arquivo '{caminho}' não foi encontrado!")
        raise FileNotFoundError(f"❌ O arquivo '{caminho}' não foi encontrado!")

    with open(caminho, "r", encoding="utf-8") as f:
        config: Dict[str, Any] = json.load(f)

    def nome_tipo(tipo: Union[Type, Tuple[Type, ...]]) -> str:
        """Retorna uma string legível para o tipo (ex.: 'int', 'int ou float')."""
        if isinstance(tipo, tuple):
            return " ou ".join(t.__name__ for t in tipo)
        return tipo.__name__

    def validar_chave(
        chave: str,
        tipo: Union[Type, Tuple[Type, ...]],
        min_val: Union[int, float, None] = None,
        max_val: Union[int, float, None] = None,
        fallback: Any = None,
        alvo: Union[Dict[str, Any], None] = None,
        prefixo: str = "",
    ) -> None:
        """Valida `chave` dentro de `alvo` (padrão: o dict `config` de nível raiz).
        `prefixo` é usado apenas para deixar as mensagens de log mais claras
        (ex.: 'validation.ruff' em vez de apenas 'ruff')."""
        destino = alvo if alvo is not None else config
        nome_completo = f"{prefixo}{chave}"
        valor = destino.get(chave)
        if valor is None:
            if fallback is not None:
                logger.warning(f"'{nome_completo}' não encontrado. Usando valor padrão: {fallback}")
                destino[chave] = fallback
            return
        if not isinstance(valor, tipo):
            logger.warning(f"'{nome_completo}' deve ser {nome_tipo(tipo)}. Usando valor padrão: {fallback}")
            destino[chave] = fallback
            return
        if min_val is not None and valor < min_val: # type: ignore
            logger.warning(f"'{nome_completo}' muito baixo (mínimo {min_val}). Usando {fallback}.")
            destino[chave] = fallback
        if max_val is not None and valor > max_val: # type: ignore
            logger.warning(f"'{nome_completo}' muito alto (máximo {max_val}). Usando {fallback}.")
            destino[chave] = fallback

    validar_chave("api_url", str, fallback=DEFAULT_CONFIG["api_url"])
    validar_chave("model", str, fallback=DEFAULT_CONFIG["model"])
    validar_chave("temperature", (int, float), min_val=0.0, max_val=2.0, fallback=DEFAULT_CONFIG["temperature"])
    validar_chave("max_tokens", int, min_val=1, fallback=DEFAULT_CONFIG["max_tokens"])
    validar_chave("timeout", (int, float), min_val=1, fallback=DEFAULT_CONFIG["timeout"])
    validar_chave("default_system_prompt", str, fallback=DEFAULT_CONFIG["default_system_prompt"])
    validar_chave("checkpoint_file", str, fallback=DEFAULT_CONFIG["checkpoint_file"])

    # --- Validação da seção "validation" (validação automática pós-modificação) ---
    validacao_raw = config.get("validation")
    if not isinstance(validacao_raw, dict):
        if "validation" in config:
            logger.warning("'validation' deve ser um objeto (dict). Usando valores padrão.")
        validacao_raw = {}
        config["validation"] = validacao_raw

    validar_chave("enabled", bool, fallback=DEFAULT_VALIDATION["enabled"], alvo=validacao_raw, prefixo="validation.")
    validar_chave("ruff", bool, fallback=DEFAULT_VALIDATION["ruff"], alvo=validacao_raw, prefixo="validation.")
    validar_chave("mypy", bool, fallback=DEFAULT_VALIDATION["mypy"], alvo=validacao_raw, prefixo="validation.")
    validar_chave("pytest", bool, fallback=DEFAULT_VALIDATION["pytest"], alvo=validacao_raw, prefixo="validation.")
    validar_chave("pytest_dir", str, fallback=DEFAULT_VALIDATION["pytest_dir"], alvo=validacao_raw, prefixo="validation.")
    validar_chave(
        "fail_triggers_replan", bool,
        fallback=DEFAULT_VALIDATION["fail_triggers_replan"],
        alvo=validacao_raw, prefixo="validation.",
    )

    config["validation"] = validacao_raw

    return config