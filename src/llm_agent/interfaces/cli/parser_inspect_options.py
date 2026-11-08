"""Option definitions shared by inspect CLI subcommands."""

from __future__ import annotations

import argparse


def add_inspect_options(item: argparse.ArgumentParser) -> None:
    item.add_argument("--run-id", dest="inspect_run_id", metavar="RUN_ID", help="seleciona uma execução")
    item.add_argument("--json", action="store_true", dest="json_output", help="emite um único documento JSON")
    item.add_argument("--follow", action="store_true", help="acompanha um run ativo em terminal interativo")
    item.add_argument("--limit", type=int, default=100, metavar="N", help="limite bounded de registros")
    item.add_argument("--after", type=int, default=0, metavar="SEQ", help="começa após a sequência")
    item.add_argument(
        "--sequence", type=int, dest="inspect_sequence", metavar="SEQ", help="seleciona detalhe de uma sequência"
    )
    item.add_argument("--sequence-start", type=int, dest="inspect_sequence_start", metavar="SEQ")
    item.add_argument("--sequence-end", type=int, dest="inspect_sequence_end", metavar="SEQ")
    item.add_argument("--search", metavar="TEXT", help="busca apenas na representação redigida")
    item.add_argument("--source", action="append", dest="inspect_sources", metavar="SOURCE")
    item.add_argument("--kind", action="append", dest="inspect_kinds", metavar="KIND")
    item.add_argument("--category", action="append", dest="inspect_categories", metavar="CATEGORY")
    item.add_argument("--severity", action="append", dest="inspect_severities", metavar="SEVERITY")
    item.add_argument("--status", action="append", dest="inspect_statuses", metavar="STATUS")
    item.add_argument("--task-id", dest="inspect_task_id", metavar="ID")
    item.add_argument("--root-task-id", dest="inspect_root_task_id", metavar="ID")
    item.add_argument("--step", type=int, dest="inspect_step", metavar="N")
    item.add_argument("--correlation-id", dest="inspect_correlation_id", metavar="ID")
    item.add_argument("--invocation-id", dest="inspect_invocation_id", metavar="ID")
    item.add_argument("--time-start", dest="inspect_time_start", metavar="ISO_TIME")
    item.add_argument("--time-end", dest="inspect_time_end", metavar="ISO_TIME")
    item.add_argument("--bookmarked-only", action="store_true", dest="inspect_bookmarked_only")
