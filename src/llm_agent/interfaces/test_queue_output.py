"""Human-readable CLI and standalone HTML views over copied queue observations."""
from __future__ import annotations

import html
import json
from typing import Any

from llm_agent.application.test_queue import QueueProgress
from llm_agent.application.test_queue_service import redact_queue_text

LABELS = {"ready": "Pronta", "running": "Em execução", "paused": "Pausada", "stopped": "Parada",
          "completed": "Finalizada", "budget_exhausted": "Limite de tempo atingido", "pending": "Pendente",
          "verified": "Verificado", "unverified": "Sem verificação", "failed": "Reprovado",
          "error": "Erro de execução", "timeout": "Tempo esgotado", "interrupted": "Interrompido",
          "passed": "Aprovada", "blocked": "Bloqueado", "succeeded": "Concluído"}


def safe_text(value: Any) -> str:
    text = redact_queue_text(str(value))
    return "".join(char if char in "\n\t" or char.isprintable() else f"\\u{ord(char):04x}" for char in text)


def label(status: Any) -> str:
    return LABELS.get(str(status), safe_text(status))


def _names(values: list[str]) -> str:
    shown = ", ".join(map(safe_text, values[:10]))
    return shown if len(values) <= 10 else f"{shown} (+{len(values) - 10})"


def progress_text(progress: QueueProgress) -> str:
    return f"[{progress.position}/{progress.total}] {safe_text(progress.case_id)}: {label(progress.status)} | {progress.elapsed_seconds:.1f}s acumulados"


def queue_run_text(observation: dict[str, Any]) -> str:
    counts = " · ".join(f"{label(status)}: {count}" for status, count in observation.get("counts", {}).items())
    return f"Bateria de testes: {label(observation.get('queue_status'))}.\n{counts}\nAbra Testes para conferir os resultados e as evidências de cada tarefa."


def summary_text(document: dict[str, Any], *, include_cases: bool = True) -> str:
    lines = [f"Fila: {label(document['status'])}", f"Pasta: {safe_text(document['queue'])}",
             f"Processamento acumulado: {document['elapsed_seconds']:.1f}s"]
    counts = document["counts"]
    lines.append(" | ".join(f"{label(status)}: {count}" for status, count in counts.items()))
    if include_cases:
        for row in document["cases"]:
            lines.append(f"  {safe_text(row['id'])}: {label(row['status'])} ({row['checks']} critérios)")
    if document["without_checks"]:
        lines.append("Atenção: sem critérios externos: " + _names(document["without_checks"]))
    if document["not_imported"]:
        lines.append("Pastas não importadas (sem task.md): " + _names(document["not_imported"]))
    if counts.get("interrupted"):
        lines.append("Revise as tarefas interrompidas antes de usar --skip-interrupted. Elas não serão repetidas.")
    if document["status"] == "completed":
        lines.append("Finalizada significa que a fila terminou; confira os resultados individuais.")
    elif not counts.get("pending") and not document["runner_active"]:
        lines.append("Não há tarefas pendentes. Importe uma nova fila para repetir o experimento.")
    return "\n".join(lines)


def detail_text(row: dict[str, Any]) -> str:
    execution = row.get("execution") or {}
    verification = row.get("verification") or {}
    lines = [f"{safe_text(row['id'])}: {label(row['status'])}"]
    if execution:
        success = "sim" if execution.get("success") is True else "não"
        lines.append(f"  Sucesso declarado pelo runtime: {success} ({label(execution.get('status', 'indisponível'))})")
    lines.append(f"  Verificação externa: {label(verification.get('status', 'não executada'))}")
    for key in ("error", "detail_error"):
        if row.get(key):
            lines.append(f"  Detalhe: {safe_text(row[key])[:2000]}")
    if execution.get("error"):
        lines.append(f"  Motivo do runtime: {safe_text(execution['error'])[:2000]}")
    for check in verification.get("checks", []):
        if check.get("passed") is not True:
            lines.extend(_failed_check_lines(check))
    if row.get("workspace"):
        lines.append(f"  Projeto e evidências: {safe_text(row['workspace'])}")
    return "\n".join(lines)

def _failed_check_lines(check: dict[str, Any]) -> list[str]:
    lines = [f"  Critério reprovado: {safe_text(check.get('kind'))} em {safe_text(check.get('path'))}"]
    errors = {"FileNotFoundError": "O arquivo esperado não foi produzido.",
              "UnicodeDecodeError": "O arquivo não pôde ser lido como UTF-8.",
              "WorkspacePathError": "O caminho do resultado foi bloqueado por segurança."}
    for key in ("error", "stderr", "stdout"):
        if check.get(key):
            value = errors.get(check[key], check[key]) if key == "error" else check[key]
            lines.append(f"    {key}: {safe_text(value)[:2000]}")
    if check.get("timed_out"):
        lines.append("    O verificador atingiu o limite de tempo.")
    return lines


def html_report(document: dict[str, Any]) -> str:
    escape = lambda value: html.escape(safe_text(value), quote=True)  # noqa: E731
    sections = []
    navigation = []
    priority = {"error": 0, "interrupted": 1, "timeout": 2, "failed": 3, "unverified": 4,
                "running": 5, "pending": 6, "verified": 7}
    results = sorted(document.get("results", []), key=lambda row: priority.get(row["status"], 8))
    for index, row in enumerate(results):
        tone = "good" if row["status"] == "verified" else "other"
        navigation.append(f'<a href="#case-{index}">{escape(row["id"])}: {escape(label(row["status"]))}</a>')
        sections.append(f'<section id="case-{index}" class="{tone}"><h2>{escape(row["id"])} — {escape(label(row["status"]))}</h2>'
                        f'<pre>{escape(detail_text(row))}</pre><details><summary>Dados técnicos e evidências</summary>'
                        f'<pre>{escape(json.dumps(row, ensure_ascii=False, indent=2))}</pre></details></section>')
    return ('<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            '<title>Relatório de testes do Agent</title><style>'
            'body{font:17px system-ui,sans-serif;line-height:1.5;max-width:1050px;margin:32px auto;padding:0 20px;background:#f7f9fc;color:#17202e}'
            'section{background:white;border:1px solid #64748b;border-left:5px solid #64748b;border-radius:8px;padding:16px;margin:20px 0}'
            '.good{border-left-color:#16703c}h2{font-size:1.2rem}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px ui-monospace,monospace}'
            'summary{cursor:pointer;padding:8px}summary:focus-visible{outline:3px solid #1d4ed8}'
            'nav{display:flex;flex-wrap:wrap;gap:8px}nav a{border:1px solid #64748b;border-radius:4px;padding:6px;color:inherit}'
            '@media(prefers-color-scheme:dark){body{background:#121923;color:#e9eef5}section{background:#1c2634}.good{border-left-color:#60ce87}}'
            '</style></head><body><h1>Relatório de testes do Agent</h1>'
            '<p>Retrato do progresso salvo. “Verificado” exige sucesso do runtime e aprovação dos critérios externos definidos.</p>'
            f'<pre>{escape(summary_text(document, include_cases=False))}</pre>'
            f'<nav aria-label="Ir para resultado de uma tarefa">{"".join(navigation)}</nav>{"".join(sections)}'
            f'<footer>Identidade do experimento: <code>{escape(document.get("identity") or "ainda não executado")}</code></footer></body></html>')
