import os
import json
import pytest
from config import carregar_config, DEFAULT_CONFIG

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
