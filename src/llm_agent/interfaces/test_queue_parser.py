"""Argument schema for queue commands; parsing never starts work."""
from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fila persistente de testes no runtime real do agent.")
    commands = parser.add_subparsers(dest="action", required=True)
    draft = commands.add_parser("draft", help="Preparar caso de avaliação de uma evidência revisada; preview sem escrever nem chamar LLM.")
    draft.add_argument("output", type=Path)
    draft.add_argument("--objective", required=True)
    draft.add_argument("--checks", type=Path, required=True)
    draft.add_argument("--evidence", type=Path, required=True)
    draft.add_argument("--write-reviewed", action="store_true", help="Confirma revisão humana e escreve em diretório novo, sem sobrescrever.")
    create = commands.add_parser("create", help="Importar pastas com task.md, seed/ e checks.json; sem chamar LLM.")
    create.add_argument("tasks", type=Path)
    create.add_argument("queue", type=Path)
    run = commands.add_parser("run", help="Executar/retomar os testes pendentes.")
    run.add_argument("queue", type=Path)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--profile")
    run.add_argument("--live", action="store_true", help="Autoriza chamadas ao modelo configurado, com possivel custo.")
    run.add_argument("--yes", action="store_true", help="Autoriza automaticamente ferramentas nos projetos de teste. Nao e sandbox.")
    run.add_argument("--case-seconds", type=int, default=300)
    run.add_argument("--total-seconds", type=int, default=3600)
    run.add_argument("--skip-interrupted", action="store_true", help="Preservar tarefa interrompida sem repetir e seguir com as pendentes.")
    run.add_argument("--detach", action="store_true", help="Continuar em processo independente após fechar a interface; consulte status, pause ou stop.")
    run.add_argument("--expected-identity", help=argparse.SUPPRESS)
    for name, help_text in (("status", "Mostrar progresso salvo."), ("pause", "Pausar apos a tarefa atual."),
                            ("stop", "Interromper a tarefa atual e parar a fila.")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("queue", type=Path)
    next_command = commands.add_parser("next", help="Priorizar uma tarefa pendente enquanto o runner esta parado.")
    next_command.add_argument("queue", type=Path)
    next_command.add_argument("case_id")
    check = commands.add_parser("check", help="Conferir criterios e, opcionalmente, configuracao sem chamar a LLM.")
    check.add_argument("queue", type=Path)
    check.add_argument("--config", type=Path)
    check.add_argument("--profile")
    check.add_argument("--yes", action="store_true")
    check.add_argument("--case-seconds", type=int, default=300)
    details = commands.add_parser("details", help="Explicar resultado declarado e verificacao de uma tarefa.")
    details.add_argument("queue", type=Path)
    details.add_argument("case_id")
    report = commands.add_parser("report", help="Gerar relatorio HTML local, sem executar testes.")
    report.add_argument("queue", type=Path)
    report.add_argument("--output", type=Path)
    for command in commands.choices.values():
        command.add_argument("--json", action="store_true", help="Emitir somente JSON para integracoes.")
    return parser
