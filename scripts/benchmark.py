"""Benchmark real e headless do fluxo completo do assistente.

O benchmark usa o backend configurado e executa tarefas reais no workspace
explícito. Ele não substitui os cenários herméticos de ``agent.evaluation``.
Configuração, estado, logs e resultado são resolvidos pelo mesmo composition
root usado pelas demais interfaces standalone.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any, Sequence

TASK_TIMEOUT_SECONDS = 120
BENCHMARK_TASKS = (
    "Liste todos os arquivos do projeto.",
    "Crie um arquivo hello.py que imprime 'Hello, world!'.",
    "Execute o arquivo hello.py com python_executor.",
    "Calcule a soma de 1 a 10 usando python_executor.",
    "Leia o arquivo EstruturaProjeto.md e faça um resumo de 3 linhas.",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description="Executa o benchmark real do assistente no workspace selecionado.",
    )
    parser.add_argument("--workspace", default=Path.cwd(), metavar="DIR", help="workspace das tarefas")
    parser.add_argument("--home", metavar="DIR", help="diretório-base dos dados da aplicação")
    parser.add_argument("--config", metavar="ARQUIVO", help="arquivo de configuração explícito")
    parser.add_argument("--profile", metavar="NOME", help="perfil de modelo configurado")
    return parser


def _create_application(args: argparse.Namespace) -> Any:
    from agent.application import AgentApplication
    from agent.runtime.paths import AppPaths

    return AgentApplication.create(
        workspace=Path(args.workspace).expanduser(),
        paths=AppPaths.discover(app_home=args.home),
        config_path=args.config,
        profile=args.profile,
    )


def _determine_success(application: Any, application_succeeded: bool, errored: bool, timed_out: bool) -> bool:
    if errored or timed_out or not application_succeeded:
        return False

    orchestrator = application.orchestrator
    tool_history = list(getattr(orchestrator.agent_state, "tool_history", []) or [])
    if not tool_history:
        return True

    last_result = getattr(orchestrator.agent_state, "last_result", None)
    if isinstance(last_result, dict):
        return bool(last_result.get("ok") is True)
    return False


def run_task(
    application: Any,
    objective: str,
    *,
    timeout_seconds: int = TASK_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Executa uma tarefa pela fronteira pública e coleta métricas disponíveis."""

    errored = False
    timed_out = False
    error_message = ""
    answer = ""
    application_succeeded = False

    start = time.perf_counter()
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(application.run, objective)
    try:
        result = future.result(timeout=timeout_seconds)
        answer = result.answer
        application_succeeded = result.success
        error_message = result.error or ""
    except FutureTimeoutError:
        timed_out = True
        error_message = f"Timeout: tarefa excedeu {timeout_seconds}s."
        application.cancel()
    except Exception as exc:  # noqa: BLE001 - qualquer falha invalida a medição
        errored = True
        error_message = f"{type(exc).__name__}: {exc}"
        if getattr(application.orchestrator, "verbose", False):
            traceback.print_exc()
    finally:
        # Threads Python não podem ser terminadas com segurança. Após solicitar
        # cancelamento cooperativo, aguardamos a execução em voo encerrar antes
        # de reutilizar ou fechar os recursos da aplicação.
        pool.shutdown(wait=True, cancel_futures=timed_out)
    elapsed = time.perf_counter() - start

    orchestrator = application.orchestrator
    steps = len(getattr(orchestrator.agent_state, "tool_history", []) or [])
    success = _determine_success(application, application_succeeded, errored, timed_out)

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


def print_table(results: list[dict[str, Any]]) -> None:
    headers = ["#", "Tarefa", "Sucesso", "Passos", "Tempo (s)"]
    col_widths = [3, 60, 9, 8, 10]

    def fmt_row(cells: list[str]) -> str:
        return " | ".join(cell.ljust(width) for cell, width in zip(cells, col_widths, strict=False))

    separator = "-+-".join("-" * width for width in col_widths)

    print("\n=== Benchmark do LLM Agent ===\n")
    print(fmt_row(headers))
    print(separator)
    for index, result in enumerate(results, start=1):
        objective = result["objective"]
        short_objective = (objective[:57] + "...") if len(objective) > 60 else objective
        print(
            fmt_row(
                [
                    str(index),
                    short_objective,
                    "SIM" if result["success"] else "NAO",
                    str(result["steps"]),
                    f"{result['elapsed_seconds']:.2f}",
                ]
            )
        )
    print(separator)

    total = len(results)
    successes = sum(1 for result in results if result["success"])
    total_time = sum(result["elapsed_seconds"] for result in results)
    print(f"\nResumo: {successes}/{total} tarefas bem-sucedidas | Tempo total: {total_time:.2f}s\n")


def _report(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "task_timeout_seconds": TASK_TIMEOUT_SECONDS,
        "results": results,
        "summary": {
            "total_tasks": len(results),
            "successful_tasks": sum(1 for result in results if result["success"]),
            "total_elapsed_seconds": round(
                sum(result["elapsed_seconds"] for result in results),
                3,
            ),
        },
    }


def run_benchmark(
    application: Any,
    *,
    tasks: Sequence[str] = BENCHMARK_TASKS,
) -> Path:
    results: list[dict[str, Any]] = []
    for objective in tasks:
        print(f"\n>>> Executando: {objective}")
        result = run_task(application, objective)
        status = "OK" if result["success"] else "FALHOU"
        print(f"<<< {status} | passos={result['steps']} | tempo={result['elapsed_seconds']}s")
        results.append(result)
        if result["timed_out"]:
            print("Benchmark interrompido após timeout; a aplicação não será reutilizada.")
            break

    print_table(results)
    results_file = Path(application.workspace_paths.benchmark_results_file)
    results_file.parent.mkdir(parents=True, exist_ok=True)
    results_file.write_text(
        json.dumps(_report(results), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Resultados gravados em {results_file}")
    return results_file


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    application = None
    try:
        application = _create_application(args)
        run_benchmark(application)
        return 0
    except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        from agent.runtime.config_errors import ConfigError

        if isinstance(exc, ConfigError):
            print(f"ERRO: {exc}", file=sys.stderr)
            return 2
        print(f"ERRO: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if application is not None:
            application.close()


if __name__ == "__main__":
    raise SystemExit(main())
