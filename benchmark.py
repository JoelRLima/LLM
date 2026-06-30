"""
benchmark.py
============
Script de benchmark headless para o LLM Agent.

Carrega a configuração, monta uma ChatSession, registra todas as skills
disponíveis e roda o Orchestrator contra um pequeno conjunto de tarefas
fixas, sem qualquer intervenção do usuário (sem loop de CLI, sem prompts
interativos). Para cada tarefa mede: sucesso, número de passos
(tool_history) e tempo decorrido, respeitando um timeout individual de
120s por tarefa.

Uso:
    python benchmark.py

Saída:
    - Tabela comparativa impressa no terminal.
    - Arquivo benchmark_results.json com os resultados detalhados.

IMPORTANTE — premissas assumidas sobre módulos não incluídos neste pacote
de documentos (não tive acesso ao código-fonte de config.py nem de
agent/state.py, agent/skills/session_memory.py e agent/skills/summarize.py):

  1. config.py expõe uma função pública de carregamento de configuração.
     A documentação (EstruturaProjeto.md, seção 7) referencia essa função
     pelo nome `carregar_config`. Tento importá-la sob esse nome e, como
     rede de segurança, também tento `load_config` / `carregar_configuracao`
     antes de desistir. Se o nome real for outro, ajuste a constante
     CONFIG_LOADER_CANDIDATES abaixo ou avise para eu corrigir.

  2. `carregar_config()` é chamada sem argumentos e lê "config.json" no
     diretório atual (conforme o fluxo de Início Rápido do projeto, que
     pede para copiar config.example.json -> config.json antes de rodar).

  3. SessionMemorySkill e SummarizeSkill são instanciadas por
     load_all_skills() com `orchestrator=None` (ver SKILL_CONFIG em
     agent/skills/__init__.py). Como não vi o código que normalmente
     preenche essa referência (provavelmente cli.py faz isso na
     inicialização), este script faz isso manualmente logo após criar o
     Orchestrator: para toda skill registrada que tenha um atributo
     `orchestrator`, ele é apontado para a instância do Orchestrator.
     Isso é necessário para que essas duas skills funcionem fora da CLI.
     Se o projeto já fizer esse wiring em outro lugar (ex.: dentro do
     próprio Orchestrator.register_skill), este passo é inofensivo
     (apenas reatribui o mesmo valor).

  4. Determinação de "sucesso" de uma tarefa: o método Orchestrator.run()
     retorna apenas uma string de resposta final, sem um campo booleano
     de sucesso explícito na API pública. Por isso uso a seguinte
     heurística, baseada exclusivamente em estado público
     (orchestrator.agent_state):
       - Se uma exceção foi levantada ou o timeout de 120s estourou:
         sucesso = False.
       - Se nenhuma ferramenta foi usada (tool_history vazio, ex.:
         resposta trivial): sucesso = True (nada falhou).
       - Caso contrário: sucesso = True somente se
         agent_state.last_result for um dict com ok=True.
     Se preferir outro critério (ex.: checar se a resposta final contém
     avisos de "tarefa interrompida"), me avise para eu ajustar.

Nenhum arquivo existente do projeto é modificado por este script.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Dict, List, Optional

# Garante que a raiz do projeto esteja no sys.path, independente de onde
# o script seja chamado a partir de (mesmo padrão usado em agent/orchestrator.py).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TASK_TIMEOUT_SECONDS = 120
RESULTS_FILE = "benchmark_results.json"

CONFIG_LOADER_CANDIDATES = ("carregar_config", "load_config", "carregar_configuracao")


def _load_config() -> Dict[str, Any]:
    """Importa config.py e chama a função pública de carregamento de configuração.

    Tenta alguns nomes plausíveis (ver premissa 1 no docstring do módulo).
    """
    import config as config_module

    for fn_name in CONFIG_LOADER_CANDIDATES:
        fn = getattr(config_module, fn_name, None)
        if callable(fn):
            try:
                return fn()
            except TypeError:
                # função pode exigir um caminho explícito
                return fn("config.json")

    raise ImportError(
        "Não foi possível localizar a função de carregamento de configuração em "
        f"config.py (tentei: {', '.join(CONFIG_LOADER_CANDIDATES)}). "
        "Ajuste CONFIG_LOADER_CANDIDATES em benchmark.py com o nome correto."
    )


def _wire_skill_orchestrator_refs(orchestrator: Any) -> None:
    """Garante que skills que dependem de uma referência ao Orchestrator
    (ex.: SessionMemorySkill, SummarizeSkill) a recebam, mesmo fora da CLI.
    Ver premissa 3 no docstring do módulo.
    """
    for skill in orchestrator.skills.values():
        if hasattr(skill, "orchestrator"):
            try:
                setattr(skill, "orchestrator", orchestrator)
            except Exception:
                pass


def _determine_success(orchestrator: Any, errored: bool, timed_out: bool) -> bool:
    if errored or timed_out:
        return False

    tool_history = list(getattr(orchestrator.agent_state, "tool_history", []) or [])
    if not tool_history:
        # Nenhuma ferramenta foi necessária (ex.: resposta trivial) -> nada falhou.
        return True

    last_result = getattr(orchestrator.agent_state, "last_result", None)
    if isinstance(last_result, dict):
        return bool(last_result.get("ok") is True)
    return False


def run_task(orchestrator: Any, objective: str) -> Dict[str, Any]:
    """Executa uma única tarefa no Orchestrator, com timeout de
    TASK_TIMEOUT_SECONDS segundos, e coleta as métricas públicas
    disponíveis em agent_state.
    """
    errored = False
    timed_out = False
    error_message = ""
    answer = ""

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(orchestrator.run, objective)
        try:
            answer = future.result(timeout=TASK_TIMEOUT_SECONDS)
        except FutureTimeoutError:
            timed_out = True
            error_message = f"Timeout: tarefa excedeu {TASK_TIMEOUT_SECONDS}s."
            # A thread continua rodando em segundo plano (não há API pública
            # para cancelar o Orchestrator no meio da execução); o resultado
            # tardio, se houver, será simplesmente descartado.
        except Exception as e:  # noqa: BLE001 - queremos capturar qualquer falha do agente
            errored = True
            error_message = f"{type(e).__name__}: {e}"
            if orchestrator.verbose:
                traceback.print_exc()
    elapsed = time.perf_counter() - start

    steps = len(getattr(orchestrator.agent_state, "tool_history", []) or [])
    success = _determine_success(orchestrator, errored, timed_out)

    return {
        "objective": objective,
        "success": success,
        "steps": steps,
        "elapsed_seconds": round(elapsed, 3),
        "timed_out": timed_out,
        "errored": errored,
        "error_message": error_message,
        "answer_preview": (answer or "")[:300],
    }


def print_table(results: List[Dict[str, Any]]) -> None:
    headers = ["#", "Tarefa", "Sucesso", "Passos", "Tempo (s)"]
    col_widths = [3, 60, 9, 8, 10]

    def fmt_row(cells: List[str]) -> str:
        return " | ".join(c.ljust(w) for c, w in zip(cells, col_widths))

    sep = "-+-".join("-" * w for w in col_widths)

    print("\n=== Benchmark do LLM Agent ===\n")
    print(fmt_row(headers))
    print(sep)
    for i, r in enumerate(results, start=1):
        objetivo_curto = (r["objective"][:57] + "...") if len(r["objective"]) > 60 else r["objective"]
        print(fmt_row([
            str(i),
            objetivo_curto,
            "SIM" if r["success"] else "NAO",
            str(r["steps"]),
            f'{r["elapsed_seconds"]:.2f}',
        ]))
    print(sep)

    total = len(results)
    successes = sum(1 for r in results if r["success"])
    total_time = sum(r["elapsed_seconds"] for r in results)
    print(f"\nResumo: {successes}/{total} tarefas bem-sucedidas | Tempo total: {total_time:.2f}s\n")


def main() -> None:
    if not os.path.exists("config.json"):
        print(
            "ERRO: config.json não encontrado na raiz do projeto.\n"
            "Copie config.example.json para config.json antes de rodar o benchmark "
            "(veja a seção 0 de EstruturaProjeto.md)."
        )
        sys.exit(1)

    config = _load_config()

    from session import ChatSession
    from agent.skills import load_all_skills
    from agent.orchestrator import Orchestrator

    system_prompt = config.get(
        "default_system_prompt",
        "Você é um assistente útil. Pense em inglês e responda em português brasileiro.",
    )
    session = ChatSession(system_prompt, config)

    skills = load_all_skills()

    orchestrator = Orchestrator(session, skills=skills)
    orchestrator.verbose = False
    _wire_skill_orchestrator_refs(orchestrator)

    tasks = [
        "Liste todos os arquivos do projeto.",
        "Crie um arquivo hello.py que imprime 'Hello, world!'.",
        "Execute o arquivo hello.py com python_executor.",
        "Calcule a soma de 1 a 10 usando python_executor.",
        "Leia o arquivo EstruturaProjeto.md e faça um resumo de 3 linhas.",
    ]

    results: List[Dict[str, Any]] = []
    for objective in tasks:
        print(f"\n>>> Executando: {objective}")
        result = run_task(orchestrator, objective)
        status = "OK" if result["success"] else "FALHOU"
        print(f"<<< {status} | passos={result['steps']} | tempo={result['elapsed_seconds']}s")
        results.append(result)

    print_table(results)

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "task_timeout_seconds": TASK_TIMEOUT_SECONDS,
                "results": results,
                "summary": {
                    "total_tasks": len(results),
                    "successful_tasks": sum(1 for r in results if r["success"]),
                    "total_elapsed_seconds": round(sum(r["elapsed_seconds"] for r in results), 3),
                },
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"Resultados gravados em {RESULTS_FILE}")


if __name__ == "__main__":
    main()
