"""Accessible CLI for importing, running and controlling a durable test queue."""
from __future__ import annotations

import json
import sys

from llm_agent.application.test_queue_service import TestQueueService
from llm_agent.interfaces import test_queue_actions
from llm_agent.interfaces.test_queue_output import safe_text
from llm_agent.interfaces.test_queue_parser import build_parser


def _menu_run_arguments(queue: str, last_config: str) -> tuple[list[str] | None, str]:
    print("A execucao usa a LLM configurada e pode ter custo. Abra outra janela deste menu para pausar/parar.")
    config = input(f"Arquivo de configuracao JSON [{last_config}]: ").strip().strip('"') or last_config
    if not config:
        print("Informe a configuracao antes de executar.")
        return None, last_config
    profile = input("Perfil (Enter = perfil da configuracao): ").strip()
    args = ["run", queue, "--config", config, "--live"]
    if profile:
        args += ["--profile", profile]
    if input("Autorizar ferramentas automaticamente nos projetos de teste, sem sandbox? [s/N]: ").strip().lower() == "s":
        args += ["--yes"]
    if input("Se houver tarefa interrompida ja revisada, pular e preservar seu resultado? [s/N]: ").strip().lower() == "s":
        args += ["--skip-interrupted"]
    return args, config


def menu() -> int:
    last_queue = ""
    last_config = ""
    while True:
        print("\nTestes do Agent - fila persistente")
        print("1 Importar tarefas   2 Executar/retomar   3 Ver progresso")
        print("4 Pausar apos tarefa atual   5 Parar agora   6 Priorizar pendente   0 Sair")
        print("7 Conferir antes de executar   8 Gerar relatorio HTML   9 Entender resultado de uma tarefa")
        choice = input("Escolha: ").strip()
        if choice == "0":
            return 0
        actions = {"1": "create", "2": "run", "3": "status", "4": "pause", "5": "stop", "6": "next",
                   "7": "check", "8": "report", "9": "details"}
        if choice not in actions:
            continue
        default = f" [{last_queue}]" if last_queue else ""
        queue = input(f"Pasta da fila{default}: ").strip().strip('"') or last_queue
        if not queue:
            print("Informe a pasta em que deseja guardar ou consultar a fila.")
            continue
        last_queue = queue
        args = [actions[choice], queue]
        if choice == "1":
            args = ["create", input("Pasta das tarefas: ").strip().strip('"'), queue]
        elif choice == "2":
            prepared, last_config = _menu_run_arguments(queue, last_config)
            if prepared is None:
                continue
            args = prepared
        elif choice == "6":
            args += [input("ID da tarefa pendente: ").strip()]
        elif choice == "9":
            args += [input("ID da tarefa: ").strip()]
        main(args)


def main(argv: list[str] | None = None) -> int:
    if (argv is None and len(sys.argv) == 1) or argv == []:
        try:
            return menu()
        except (KeyboardInterrupt, EOFError):
            return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.action == "run" and not args.live:
        parser.error("run exige --live: usa a LLM configurada e pode ter custo")
    try:
        if args.action == "draft":
            return test_queue_actions.draft(args)
        return test_queue_actions.execute(args, TestQueueService(args.queue))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        message = safe_text(exc)
        print(json.dumps({"status": "error", "message": message}, ensure_ascii=False) if args.json else f"Nao foi possivel concluir a operacao da fila: {message}")
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted"}) if args.json else "Fila interrompida. Revise o progresso salvo antes de retomar.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
