"""Benchmark headless do fluxo completo do LLM Agent.

O script carrega ``config.json``, cria ``ChatSession`` e ``Orchestrator`` pelo
mesmo catálogo de skills usado na CLI e executa tarefas fixas com timeout de
120 segundos. A saída vai para o terminal e para
``runtime/benchmark_results.json``.

Este benchmark usa o backend de modelo configurado e pode modificar somente os
arquivos de exercício descritos nas tarefas. Ele não substitui os cenários
herméticos de ``agent/evaluation``. Como ``Orchestrator.run`` ainda retorna uma
string, o sucesso é inferido do resultado público da última ferramenta; timeout
ou exceção sempre contam como falha.
"""

from __future__ import annotations

import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Dict, List

from agent.llm.session import ChatSession
from agent.runtime import paths
from agent.runtime.config import carregar_config

TASK_TIMEOUT_SECONDS = 120
RESULTS_FILE = paths.BENCHMARK_RESULTS_FILE

def _load_config() -> Dict[str, Any]:
    """Load configuration through the canonical runtime service."""
    return carregar_config()


def _wire_skill_orchestrator_refs(orchestrator: Any) -> None:
    """Garante que skills que dependem de uma referência ao Orchestrator
    (ex.: SessionMemorySkill, SummarizeSkill) a recebam, mesmo fora da CLI.
    Ver premissa 3 no docstring do módulo.
    """
    for skill in orchestrator.skills.values():
        if hasattr(skill, "orchestrator"):
            try:
                skill.orchestrator = orchestrator
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
        return " | ".join(c.ljust(w) for c, w in zip(cells, col_widths, strict=False))

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
    try:
        config = _load_config()
    except FileNotFoundError:
        print(
            "ERRO: config.json não encontrado na raiz do projeto.\n"
            "Copie config.example.json para config.json antes de rodar o benchmark "
            "(veja a seção 0 de EstruturaProjeto.md)."
        )
        return

    from agent.orchestrator import Orchestrator
    from agent.skills import load_all_skills
    system_prompt = config.get(
        "default_system_prompt",
        "Você é um assistente útil. Pense em inglês e responda em português brasileiro.",
    )
    session = ChatSession(system_prompt, config)

    skills = load_all_skills(model_gateway=session.gateway, config=config)

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

    paths.ensure_runtime_dir()
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
