"""Terminal projections of queue use cases, with no scheduler or model composition."""
from __future__ import annotations

import argparse
import json
from typing import Any

from llm_agent.application.test_queue_service import QueueExecutionRequest, TestQueueService
from llm_agent.interfaces.test_queue_output import detail_text, html_report, progress_text, summary_text


def show(args: argparse.Namespace, service: TestQueueService, document: dict[str, Any]) -> None:
    print(json.dumps(document, ensure_ascii=False) if args.json else summary_text(document))
    if not args.json:
        print(f"Detalhes: Workbench.cmd queue details {json.dumps(str(service.root))} ID_DA_TAREFA")


def run(args: argparse.Namespace, service: TestQueueService) -> int:
    request = QueueExecutionRequest(args.config, profile=args.profile, live_authorized=args.live,
        automatic_approval=args.yes, case_seconds=args.case_seconds, total_seconds=args.total_seconds,
        skip_interrupted=args.skip_interrupted, expected_identity=args.expected_identity)
    if args.detach:
        worker = service.start_detached(request)
        print(json.dumps(worker, ensure_ascii=False) if args.json else f"{worker['message']}\nPID: {worker['pid']}\nLog: {worker['log']}")
        return 0
    if not args.json:
        print(summary_text(service.inspect()), flush=True)
        print("Iniciando as tarefas pendentes. O resultado de cada uma sera mostrado apos salvar.", flush=True)
    outcome = service.run(request, on_progress=None if args.json else lambda item: print(progress_text(item), flush=True))
    show(args, service, outcome.document)
    return {"verified": 0, "failed": 1, "incomplete": 2}[outcome.assessment]


def control(args: argparse.Namespace, service: TestQueueService) -> int:
    response = service.control(args.action)
    if args.json:
        print(json.dumps(response))
    else:
        print("Pausa solicitada: a tarefa atual e sua verificacao terminarao antes de pausar." if args.action == "pause"
              else "Parada solicitada: aguarde a interrupcao do processo atual e confira o progresso salvo.")
    return 0


def details(args: argparse.Namespace, service: TestQueueService) -> int:
    detail = service.details(args.case_id)
    print(json.dumps(detail, ensure_ascii=False) if args.json else detail_text(detail))
    return 0


def report(args: argparse.Namespace, service: TestQueueService) -> int:
    output = service.export(html_report(service.inspect(include_details=True)), args.output)
    print(json.dumps({"report": str(output)}, ensure_ascii=False) if args.json else f"Relatorio salvo: {output}")
    return 0


def check(args: argparse.Namespace, service: TestQueueService) -> int:
    document = service.check(config_path=args.config, profile=args.profile,
                             automatic_approval=args.yes, case_seconds=args.case_seconds)
    print(json.dumps(document, ensure_ascii=False) if args.json else summary_text(document))
    if not args.json:
        print("Conferencia local: nenhum teste, verificador ou modelo foi executado.")
        if document.get("configuration"):
            print(json.dumps(document["configuration"], ensure_ascii=False))
    if document.get("configuration", {}).get("resume_compatible") is False:
        if not args.json:
            print("A configuracao ou o codigo mudou. Crie outra fila para este experimento.")
        return 2
    return 0


def draft(args: argparse.Namespace) -> int:
    from llm_agent.application.evaluation_case_draft import prepare_case_draft, write_case_draft

    preview = prepare_case_draft(case_id=args.output.name, objective=args.objective,
                                checks_path=args.checks, evidence=args.evidence)
    if args.write_reviewed:
        write_case_draft(args.output, preview)
    print(json.dumps({**preview, "written": args.write_reviewed}, ensure_ascii=False, indent=None if args.json else 2))
    return 0


def execute(args: argparse.Namespace, service: TestQueueService) -> int:
    operations = {"run": run, "pause": control, "stop": control, "details": details, "report": report, "check": check}
    if args.action in operations:
        return operations[args.action](args, service)
    readers = {"create": lambda: service.create(args.tasks), "next": lambda: service.prioritize(args.case_id),
               "status": service.inspect}
    show(args, service, readers[args.action]())
    return 0
