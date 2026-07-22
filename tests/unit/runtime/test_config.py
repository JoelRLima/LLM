import json

import pytest

from agent.runtime.config import DEFAULT_CONFIG, carregar_config


def test_carregar_config_sucesso(tmp_path):
    # Cria um config.json temporário válido
    config_data = {
        "api_url": "http://teste:8080",
        "model": "meu-modelo",
        "temperature": 0.8,
        "max_tokens": 1000,
        "timeout": 120,
        "default_system_prompt": "Teste"
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(config_data), encoding="utf-8")

    cfg = carregar_config(str(p))
    assert cfg["api_url"] == "http://teste:8080"
    assert cfg["temperature"] == 0.8

def test_carregar_config_arquivo_inexistente():
    with pytest.raises(FileNotFoundError):
        carregar_config("arquivo_nao_existe.json")

def test_carregar_config_falta_campos_usa_fallback(tmp_path):
    # Passa um json vazio, deve retornar os padrões
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")

    cfg = carregar_config(str(p))
    assert cfg["api_url"] == DEFAULT_CONFIG["api_url"]
    assert cfg["temperature"] == DEFAULT_CONFIG["temperature"]
    assert cfg["resume_retry_failed"] is False
    assert cfg["resume_retry_skipped"] is False
    assert cfg["hardware_profile"] == "low_vram_8gb"
    assert cfg["max_model_concurrency"] == 1
    assert cfg["max_model_calls"] == 20
    assert cfg["code_policy"] == DEFAULT_CONFIG["code_policy"]


def test_carregar_config_valida_politica_de_retry_da_retomada(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {"resume_retry_failed": True, "resume_retry_skipped": "sim"}
        ),
        encoding="utf-8",
    )

    cfg = carregar_config(str(p))
    assert cfg["resume_retry_failed"] is True
    assert cfg["resume_retry_skipped"] is False

def test_carregar_config_tipos_invalidos_usa_fallback(tmp_path):
    # Passa tipos errados, ex: temp como string
    config_data = {"temperature": "quente", "max_tokens": "muitos"}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(config_data), encoding="utf-8")

    cfg = carregar_config(str(p))
    assert cfg["temperature"] == DEFAULT_CONFIG["temperature"]
    assert cfg["max_tokens"] == DEFAULT_CONFIG["max_tokens"]

def test_carregar_config_limites_bounds(tmp_path):
    # Passa temp = 3.0 (max 2.0)
    config_data = {"temperature": 3.0}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(config_data), encoding="utf-8")

    cfg = carregar_config(str(p))
    assert cfg["temperature"] == DEFAULT_CONFIG["temperature"]


def test_carregar_config_normaliza_politica_de_codigo(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "code_policy": {
                    "auto_apply_min_confidence": 2,
                    "max_auto_files": 0,
                    "require_target_alignment": "sim",
                }
            }
        ),
        encoding="utf-8",
    )

    policy = carregar_config(str(p))["code_policy"]

    assert policy == DEFAULT_CONFIG["code_policy"]


def test_carregar_config_valida_hardware_e_perfil_de_modelo(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "hardware_profile": "desconhecido",
                "max_model_concurrency": 0,
                "max_model_calls": 0,
                "default_model_profile": "ausente",
                "model_profiles": {"local": {"provider": "openai_compatible"}},
            }
        ),
        encoding="utf-8",
    )

    cfg = carregar_config(str(p))

    assert cfg["hardware_profile"] == "low_vram_8gb"
    assert cfg["max_model_concurrency"] == 1
    assert cfg["max_model_calls"] == 20
    assert "default_model_profile" not in cfg


def test_carregar_config_normaliza_campos_internos_do_perfil(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "default_model_profile": "local",
                "model_profiles": {
                    "local": {
                        "provider": 123,
                        "max_tokens": "muitos",
                        "temperature": 9,
                        "capabilities": {
                            "streaming": "sim",
                            "structured_output": "mágico",
                        },
                        "provider_options": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    profile = carregar_config(str(p))["model_profiles"]["local"]

    assert profile["provider"] == "openai_compatible"
    assert profile["max_tokens"] == 2048
    assert profile["temperature"] == 0.2
    assert profile["capabilities"]["streaming"] is False
    assert profile["capabilities"]["structured_output"] == "json_prompt"
    assert profile["provider_options"] == {}
