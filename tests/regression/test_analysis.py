"""
Testes de regressão para análise de código.

Valida que o agente consegue analisar arquivos Python usando
code_analyzer + file_reader, com plano canônico e execução direta.
"""
from tests.regression.conftest import assert_agent_invariants, load_plan


def test_analysis_cli_plan_structure():
    """O fixture JSON para analysis_cli deve conter as ferramentas esperadas."""
    plan_data = load_plan("analysis_cli")
    tools_in_plan = {step["tool"] for step in plan_data["plan"]}
    assert tools_in_plan == {"code_analyzer", "file_reader"}, \
        f"Ferramentas no plano: {tools_in_plan}"


def test_analysis_cli_execution(agent, fake_model):
    """Executa o plano de análise da CLI canônica e verifica invariantes e ferramentas."""
    # Carrega o plano e a resposta final
    plan_data = load_plan("analysis_cli")
    final_response = {
        "action": "final",
        "answer": "A CLI canônica contém as funções main e obter_status_think."
    }

    # Configura as respostas do fake
    fake_model.set_response("analysis_cli", "plan", plan_data)
    fake_model.set_response("analysis_cli", "final", final_response)

    # Executa o agente
    result = agent.run("analysis_cli")

    # Valida invariantes arquiteturais
    assert_agent_invariants(result, agent.agent_state)

    # Verifica que as ferramentas esperadas foram usadas na ordem correta
    tools_used = [h["tool"] for h in agent.agent_state.tool_history]
    assert tools_used == ["code_analyzer", "file_reader"], \
        f"Ferramentas executadas: {tools_used}"

    # Verifica que o code_analyzer recebeu os argumentos corretos
    code_analyzer_call = agent.agent_state.tool_history[0]
    assert code_analyzer_call["tool"] == "code_analyzer"
    assert code_analyzer_call["args"]["target"] == "agent/interfaces/cli/app.py"
    assert code_analyzer_call["args"]["mode"] == "file"
    assert code_analyzer_call["args"]["compact"] is True

    # Verifica que o file_reader recebeu o caminho correto
    file_reader_call = agent.agent_state.tool_history[1]
    assert file_reader_call["tool"] == "file_reader"
    assert file_reader_call["args"]["file_path"] == "agent/interfaces/cli/app.py"

    # A resposta final deve mencionar funções reais da implementação canônica.
    assert "main" in result.lower()
    assert "obter_status_think" in result.lower()


def test_analysis_cli_cache_hit(agent, fake_model):
    """
    Se o arquivo já foi analisado e não mudou, a segunda execução deve usar cache.
    """
    plan_data = load_plan("analysis_cli")
    final_response = {
        "action": "final",
        "answer": "Cache hit: funções já conhecidas."
    }

    # Primeira execução
    fake_model.set_response("analysis_cli", "plan", plan_data)
    fake_model.set_response("analysis_cli", "final", final_response)
    agent.run("analysis_cli")

    # Segunda execução — deve usar cache
    fake_model.set_response("analysis_cli", "plan", plan_data)
    fake_model.set_response("analysis_cli", "final", final_response)
    result2 = agent.run("analysis_cli")

    assert_agent_invariants(result2, agent.agent_state)

    # Verifica que o evento de cache_hit foi emitido
    cache_events = [e for e in agent.agent_state.events if e["type"] == "cache_hit"]
    assert len(cache_events) >= 2, f"Esperados pelo menos 2 cache_hit, encontrados: {len(cache_events)}"
