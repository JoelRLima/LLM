import json
import os

DEFAULT_PROMPT = (
    "You are a helpful assistant. "
    "Always think and reason in English. "
    "Your final response must be in Portuguese (Brazil), natural and fluent. "
    "Do not mention the language switch."
)

def carregar_config(caminho="config.json"):
    """Carrega o arquivo de configuração, aplicando validações e fallbacks."""
    if not os.path.exists(caminho):
        raise FileNotFoundError(f"O arquivo '{caminho}' não foi encontrado!")

    with open(caminho, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Validações com fallbacks seguros
    config["api_url"] = config.get("api_url", "http://127.0.0.1:8080/v1/chat/completions")
    config["model"] = config.get("model", "default")
    config["temperature"] = float(config.get("temperature", 0.6))
    config["max_tokens"] = int(config.get("max_tokens", 4096))
    config["timeout"] = int(config.get("timeout", 300))
    config["default_system_prompt"] = config.get("default_system_prompt", DEFAULT_PROMPT)

    return config