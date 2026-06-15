import json
import os

DEFAULT_PROMPT = (
    "You are a helpful assistant. "
    "Always think and reason in English. "
    "Your final response must be in Portuguese (Brazil), natural and fluent. "
    "Do not mention the language switch."
)

DEFAULT_CONFIG = {
    "api_url": "http://127.0.0.1:8080/v1/chat/completions",
    "model": "default",
    "temperature": 0.6,
    "max_tokens": 4096,
    "timeout": 300,
    "default_system_prompt": DEFAULT_PROMPT
}

def carregar_config(caminho="config.json"):
    """Carrega e valida o arquivo de configuração, aplicando fallbacks com avisos."""
    if not os.path.exists(caminho):
        raise FileNotFoundError(f"❌ O arquivo '{caminho}' não foi encontrado!")

    with open(caminho, "r", encoding="utf-8") as f:
        config = json.load(f)

    def nome_tipo(tipo):
        """Retorna uma string legível para o tipo (ex.: 'int', 'int ou float')."""
        if isinstance(tipo, tuple):
            return " ou ".join(t.__name__ for t in tipo)
        return tipo.__name__

    def validar_chave(chave, tipo, min_val=None, max_val=None, fallback=None):
        valor = config.get(chave)
        if valor is None:
            if fallback is not None:
                print(f"⚠️  '{chave}' não encontrado. Usando valor padrão: {fallback}")
                config[chave] = fallback
            return
        if not isinstance(valor, tipo):
            print(f"⚠️  '{chave}' deve ser {nome_tipo(tipo)}. Usando valor padrão: {fallback}")
            config[chave] = fallback
            return
        if min_val is not None and valor < min_val:
            print(f"⚠️  '{chave}' muito baixo (mínimo {min_val}). Usando {fallback}.")
            config[chave] = fallback
        if max_val is not None and valor > max_val:
            print(f"⚠️  '{chave}' muito alto (máximo {max_val}). Usando {fallback}.")
            config[chave] = fallback

    validar_chave("api_url", str, fallback=DEFAULT_CONFIG["api_url"])
    validar_chave("model", str, fallback=DEFAULT_CONFIG["model"])
    validar_chave("temperature", (int, float), min_val=0.0, max_val=2.0, fallback=DEFAULT_CONFIG["temperature"])
    validar_chave("max_tokens", int, min_val=1, fallback=DEFAULT_CONFIG["max_tokens"])
    validar_chave("timeout", (int, float), min_val=1, fallback=DEFAULT_CONFIG["timeout"])
    validar_chave("default_system_prompt", str, fallback=DEFAULT_CONFIG["default_system_prompt"])

    return config