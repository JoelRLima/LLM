"""
Fixtures compartilhadas para a suíte de regressão do agente.

Fornece:
- fake_model: mock do ModelClient que retorna respostas pré-definidas.
- agent: instância do Orchestrator com o modelo fake injetado.
- workspace: diretório temporário para testes de arquivos.
- assert_agent_invariants: função de validação arquitetural.
- load_plan: carrega um plano JSON da pasta fixtures/regression/plans/.
- load_all_valid_plans: carrega todos os planos da pasta valid/.
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from agent.orchestrator import Orchestrator
from agent.model_client import ModelClient
from session import ChatSession

# Versão atual do schema dos fixtures. Deve ser incrementada sempre que
# o formato do plano evoluir de forma incompatível.
CURRENT_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# FakeModelClient
# ---------------------------------------------------------------------------

class FakeModelClient:
    """
    Substitui ModelClient.request() por um dicionário de respostas pré-definidas.
    Indexado por (task_id, step_type).

    Modo strict (padrão para regressão): lança exceção se uma chamada não
    estiver configurada, em vez de retornar um fallback silencioso.
    Modo non-strict (útil para testes exploratórios): retorna erro controlado.
    """

    def __init__(self, responses: Dict[str, Dict[str, Any]] = None, strict: bool = True):
        self.responses = responses or {}
        self.strict = strict
        self.calls: List[Dict[str, Any]] = []  # histórico de chamadas recebidas

    def set_response(self, task_id: str, step_type: str, response: Dict[str, Any]):
        """Configura uma resposta para um par (task_id, step_type)."""
        self.responses[f"{task_id}|{step_type}"] = response

    def request(
        self,
        session,
        payload: Dict[str, Any],
        step_type: str = "tool_decision",
        log_metric_callback=None,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """
        Ignora o payload real e retorna a resposta pré-definida.
        Em modo strict, lança ValueError se a chamada não estiver configurada.
        """
        # Registra a chamada para auditoria
        user_messages = [m for m in session.messages if m["role"] == "user"]
        prompt = user_messages[-1]["content"] if user_messages else ""
        self.calls.append({
            "step_type": step_type,
            "prompt": prompt[:200],
        })

        # Procura por task_id conhecidos no prompt
        task_id = None
        for known in self.responses:
            if "|" in known:
                tid, _ = known.split("|", 1)
                if tid in prompt.lower().replace(" ", "_"):
                    task_id = tid
                    break

        if task_id:
            key = f"{task_id}|{step_type}"
            if key in self.responses:
                return self.responses[key]

        # Nenhuma resposta configurada
        if self.strict:
            raise ValueError(
                f"FakeModelClient em modo strict: chamada não configurada.\n"
                f"  step_type: {step_type}\n"
                f"  prompt: {prompt[:150]}\n"
                f"  Respostas configuradas: {list(self.responses.keys())}"
            )

        return {
            "action": "final",
            "answer": "Resposta fake não configurada para este prompt.",
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Diretório temporário que simula a raiz do projeto."""
    return tmp_path


@pytest.fixture
def fake_model() -> FakeModelClient:
    """
    Instância do FakeModelClient em modo strict.
    Cada teste deve configurar as respostas necessárias via set_response().
    """
    return FakeModelClient(strict=True)


@pytest.fixture
def agent(monkeypatch, fake_model: FakeModelClient) -> Orchestrator:
    """
    Instância do Orchestrator com o ModelClient fake injetado via monkeypatch.
    O patch permanece ativo durante todo o teste.
    """
    config = {
        "api_url": "http://127.0.0.1:8080/v1/chat/completions",
        "model": "default",
        "temperature": 0.6,
        "max_tokens": 4096,
        "timeout": 300,
        "default_system_prompt": "You are a helpful assistant.",
        "max_task_steps": 20,
        "max_task_tokens": 25000,
        "max_task_tool_calls": 40,
    }

    session = ChatSession(config["default_system_prompt"], config)

    from agent.skills import load_all_skills
    skills = load_all_skills()

    # Patch permanente durante o teste
    monkeypatch.setattr(ModelClient, "request", fake_model.request)

    orchestrator = Orchestrator(session, skills, verbose=False)
    return orchestrator


# ---------------------------------------------------------------------------
# Invariantes arquiteturais
# ---------------------------------------------------------------------------

def assert_agent_invariants(result: str, agent_state: Any) -> None:
    """
    Valida os contratos arquiteturais após uma execução do agente.
    Chamada por todo teste de regressão.
    """
    # Plano deve ser uma lista
    assert isinstance(agent_state.plan, list), "Plano deve ser uma lista."

    # Tool history deve ser uma lista
    assert isinstance(agent_state.tool_history, list), "tool_history deve ser uma lista."

    # Nenhuma ferramenta desconhecida
    known_tools = {
        "file_reader", "file_writer", "code_analyzer", "directory_lister",
        "grep", "python_executor", "shell", "git_reader", "web_search",
        "summarize", "session_memory", "calculator", "echo",
    }
    for entry in agent_state.tool_history:
        tool = entry.get("tool", "")
        assert tool in known_tools, f"Ferramenta desconhecida no histórico: '{tool}'."

    # Resposta final não pode ser vazia
    assert isinstance(result, str), "Resposta final deve ser uma string."
    assert len(result.strip()) > 0, "Resposta final não pode ser vazia."

    # Eventos devem existir
    assert isinstance(agent_state.events, list), "events deve ser uma lista."


# ---------------------------------------------------------------------------
# Loaders de fixtures JSON com validação de schema_version
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "regression" / "plans"


def _validate_schema_version(data: Dict[str, Any], filepath: Path) -> None:
    """Garante que o fixture JSON tem a versão de schema compatível."""
    version = data.get("schema_version")
    if version is None:
        raise ValueError(
            f"Fixture '{filepath.name}' não possui 'schema_version'. "
            f"Adicione \"schema_version\": {CURRENT_SCHEMA_VERSION} ao JSON."
        )
    if version != CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"Fixture '{filepath.name}' tem schema_version={version}, "
            f"mas o esperado é {CURRENT_SCHEMA_VERSION}. "
            f"Atualize o fixture ou incremente CURRENT_SCHEMA_VERSION no conftest.py."
        )


def load_plan(task_id: str, variant: str = "valid") -> Dict[str, Any]:
    """
    Carrega um plano JSON da pasta fixtures/regression/plans/<variant>/.

    Args:
        task_id: nome do arquivo sem extensão (ex.: 'analysis_cli').
        variant: 'valid' ou 'invalid'.

    Returns:
        Dicionário com o plano e metadados (inclui 'expected_tools' e 'expected_result').
    """
    filepath = FIXTURES_DIR / variant / f"{task_id}.json"
    if not filepath.exists():
        raise FileNotFoundError(f"Fixture não encontrada: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    _validate_schema_version(data, filepath)
    return data


def load_all_valid_plans() -> List[Dict[str, Any]]:
    """Carrega todos os planos JSON da pasta valid/."""
    plans = []
    valid_dir = FIXTURES_DIR / "valid"
    if not valid_dir.exists():
        raise FileNotFoundError(
            f"Diretório de fixtures não encontrado: {valid_dir}. "
            f"Crie a pasta e adicione arquivos JSON com schema_version={CURRENT_SCHEMA_VERSION}."
        )
    for filepath in sorted(valid_dir.glob("*.json")):
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        _validate_schema_version(data, filepath)
        plans.append(data)
    return plans