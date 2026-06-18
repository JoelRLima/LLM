import json
import os
from typing import Any, Dict, Tuple, Union, Type

DEFAULT_PROMPT: str = (
    "You are a helpful assistant. "
    "Always think and reason in English. "
    "Your final response must be in Portuguese (Brazil), natural and fluent. "
    "Do not mention the language switch."
)

DEFAULT_CONFIG: Dict[str, Any] = {
    "api_url": "http://127.0.0.1:8080/v1/chat/completions",
    "model": "default",
    "temperature": 0.6,
    "max_tokens": 4096,
    "timeout": 300,
    "default_system_prompt": DEFAULT_PROMPT
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

    def validar_chave(chave: str, tipo: Union[Type, Tuple[Type, ...]], min_val: Union[int, float, None] = None, max_val: Union[int, float, None] = None, fallback: Any = None) -> None:
        valor = config.get(chave)
        if valor is None:
            if fallback is not None:
                logger.warning(f"'{chave}' não encontrado. Usando valor padrão: {fallback}")
                config[chave] = fallback
            return
        if not isinstance(valor, tipo):
            logger.warning(f"'{chave}' deve ser {nome_tipo(tipo)}. Usando valor padrão: {fallback}")
            config[chave] = fallback
            return
        if min_val is not None and valor < min_val: # type: ignore
            logger.warning(f"'{chave}' muito baixo (mínimo {min_val}). Usando {fallback}.")
            config[chave] = fallback
        if max_val is not None and valor > max_val: # type: ignore
            logger.warning(f"'{chave}' muito alto (máximo {max_val}). Usando {fallback}.")
            config[chave] = fallback

    validar_chave("api_url", str, fallback=DEFAULT_CONFIG["api_url"])
    validar_chave("model", str, fallback=DEFAULT_CONFIG["model"])
    validar_chave("temperature", (int, float), min_val=0.0, max_val=2.0, fallback=DEFAULT_CONFIG["temperature"])
    validar_chave("max_tokens", int, min_val=1, fallback=DEFAULT_CONFIG["max_tokens"])
    validar_chave("timeout", (int, float), min_val=1, fallback=DEFAULT_CONFIG["timeout"])
    validar_chave("default_system_prompt", str, fallback=DEFAULT_CONFIG["default_system_prompt"])

    return config