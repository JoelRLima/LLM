"""Relatório de Tarefa (Task Report) — registro de auditoria consolidado.

Este módulo é intencionalmente desacoplado do `Orchestrator`: o
`TaskReportBuilder` só depende do estado público exposto por `AgentState`
(ou de qualquer objeto com os mesmos atributos), de uma lista de entradas de
métricas já lidas de `agent_metrics.jsonl`, e da resposta final da tarefa.
Isso permite testá-lo isoladamente e reutilizá-lo fora do fluxo do
orquestrador, se necessário.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Chaves possíveis usadas por diferentes emissores de métricas para
# representar tokens, duração e o tipo do evento. Como o esquema exato de
# `agent_metrics.jsonl` pode variar entre versões do agente, a agregação
# tenta múltiplos nomes de campo de forma tolerante.
_TOKEN_KEYS = ("tokens", "total_tokens", "token_count")
_DURATION_KEYS = ("duration_ms", "elapsed_ms", "latency_ms")
_TIMESTAMP_KEYS = ("timestamp", "time", "ts")
_MODEL_CALL_TYPES = ("model_call", "llm_call", "completion")

MAX_SUMMARY_CHARS = 500
MAX_PREVIEW_CHARS = 500


class TaskReportBuilder:
    """Constrói e persiste o Relatório da Tarefa ao final de uma execução do agente."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Inicializa o builder a partir da configuração do agente.

        Args:
            config: dicionário de configuração completo do agente (pode conter
                a chave opcional `task_report` com `enabled`, `format` e
                `output_dir`). Se ausente ou incompleto, valores padrão são
                usados.
        """
        config = config or {}
        task_report_cfg = config.get("task_report") or {}
        if not isinstance(task_report_cfg, dict):
            task_report_cfg = {}

        self.enabled: bool = bool(task_report_cfg.get("enabled", True))
        self.default_format: str = task_report_cfg.get("format", "json")
        self.output_dir: str = task_report_cfg.get("output_dir", "reports/")

    # ------------------------------------------------------------------
    # Construção do relatório
    # ------------------------------------------------------------------
    def build_report(
        self,
        agent_state: Any,
        metrics_entries: List[Dict[str, Any]],
        final_answer: str,
    ) -> Dict[str, Any]:
        """Monta o dicionário estruturado do relatório da tarefa.

        Args:
            agent_state: instância de `AgentState` (ou compatível) com os
                atributos públicos `objective`, `tool_history` e `events`.
            metrics_entries: lista de entradas já lidas de
                `agent_metrics.jsonl` referentes a esta tarefa.
            final_answer: resposta final produzida pelo agente.

        Returns:
            Um dicionário serializável em JSON com a estrutura descrita no
            relatório da tarefa.
        """
        metrics_entries = metrics_entries or []
        tool_history = getattr(agent_state, "tool_history", None) or []
        events = getattr(agent_state, "events", None) or []
        objective = getattr(agent_state, "objective", None)

        steps = self._build_steps(tool_history)
        replan_events = self._extract_replan_events(events)
        errors = self._collect_errors(steps)
        start_time, end_time = self._resolve_time_range(events, metrics_entries)
        metrics = self._aggregate_metrics(metrics_entries, tools_called=len(tool_history))
        success = self._determine_success(steps, final_answer)

        final_answer = final_answer or ""

        report: Dict[str, Any] = {
            "task_id": self._generate_task_id(),
            "objective": objective,
            "success": success,
            "start_time": start_time,
            "end_time": end_time,
            "steps": steps,
            "replan_events": replan_events,
            "metrics": metrics,
            "errors": errors,
            "final_answer_preview": final_answer[:MAX_PREVIEW_CHARS],
        }
        return report

    # ------------------------------------------------------------------
    # Persistência
    # ------------------------------------------------------------------
    def save_report(self, report: Dict[str, Any], format: str = "json", path: Optional[str] = None) -> str:
        """Salva o relatório em disco no formato solicitado.

        Args:
            report: dicionário produzido por `build_report`.
            format: `"json"` (padrão) ou `"markdown"`.
            path: caminho de destino. Se `None`, um nome automático baseado
                em timestamp é gerado dentro de `output_dir`.

        Returns:
            O caminho absoluto/relativo do arquivo escrito.
        """
        fmt = (format or self.default_format or "json").lower()
        if fmt not in ("json", "markdown"):
            fmt = "json"

        if path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            extension = "json" if fmt == "json" else "md"
            path = os.path.join(self.output_dir, f"task_{timestamp}.{extension}")

        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        if fmt == "json":
            content = json.dumps(report, indent=2, ensure_ascii=False, default=str)
        else:
            content = self._render_markdown(report)

        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)

        return path

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------
    @staticmethod
    def _generate_task_id() -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{timestamp}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _truncate(value: Any, max_chars: int = MAX_SUMMARY_CHARS) -> str:
        """Converte `value` em string e trunca para no máximo `max_chars`."""
        if value is None:
            text = ""
        elif isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                text = str(value)
        if len(text) > max_chars:
            return text[:max_chars] + "…"
        return text

    def _build_steps(self, tool_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        steps: List[Dict[str, Any]] = []
        for index, entry in enumerate(tool_history):
            if not isinstance(entry, dict):
                continue
            tool = entry.get("tool")
            args = entry.get("args") or {}
            raw_result = entry.get("result")

            if isinstance(raw_result, dict):
                ok = bool(raw_result.get("ok"))
                error = raw_result.get("error") or ""
                data_summary = self._truncate(raw_result.get("data", raw_result))
                cache_hit = raw_result.get("cache_hit")
            else:
                ok = False
                error = "" if raw_result is None else "resultado em formato inesperado"
                data_summary = self._truncate(raw_result)
                cache_hit = None

            step: Dict[str, Any] = {
                "index": index,
                "tool": tool,
                "args": args,
                "result": {
                    "ok": ok,
                    "error": self._truncate(error),
                    "data_summary": data_summary,
                },
            }
            if cache_hit is not None:
                step["cache_hit"] = bool(cache_hit)

            steps.append(step)
        return steps

    @staticmethod
    def _extract_replan_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extrai eventos de replanejamento a partir da telemetria do agente.

        Eventos são considerados de replanejamento quando `type == "replan"`,
        formato emitido opcionalmente pelo orquestrador/ErrorHandler. Campos
        ausentes recebem valores padrão para manter a estrutura estável.
        """
        replans: List[Dict[str, Any]] = []
        for event in events or []:
            if not isinstance(event, dict) or event.get("type") != "replan":
                continue
            data = event.get("data") or {}
            replans.append({
                "original_step": data.get("original_step"),
                "error": data.get("error", ""),
                "strategy": data.get("strategy", ""),
                "replacement_steps": data.get("replacement_steps", 0),
            })
        return replans

    @staticmethod
    def _collect_errors(steps: List[Dict[str, Any]]) -> List[str]:
        errors: List[str] = []
        for step in steps:
            result = step.get("result") or {}
            if not result.get("ok") and result.get("error"):
                errors.append(result["error"])
        return errors

    @staticmethod
    def _determine_success(steps: List[Dict[str, Any]], final_answer: str) -> bool:
        last_ok = False
        if steps:
            last_ok = bool(steps[-1].get("result", {}).get("ok"))
        has_final_answer = bool(final_answer and str(final_answer).strip())
        return last_ok or has_final_answer

    @staticmethod
    def _resolve_time_range(
        events: List[Dict[str, Any]],
        metrics_entries: List[Dict[str, Any]],
    ) -> "tuple[str, str]":
        """Determina início/fim da tarefa.

        Nem `AgentState.events` nem, necessariamente, as entradas de
        métricas carregam um timestamp confiável em todas as versões do
        agente. Quando disponível em `metrics_entries`, usa-se o menor e o
        maior timestamp encontrados; caso contrário, usa-se o instante atual
        para ambos os campos (melhor esforço, sinalizado como tal aqui).
        """
        timestamps: List[str] = []
        for entry in metrics_entries or []:
            if not isinstance(entry, dict):
                continue
            for key in _TIMESTAMP_KEYS:
                if key in entry and entry[key]:
                    timestamps.append(str(entry[key]))
                    break

        if timestamps:
            timestamps.sort()
            return timestamps[0], timestamps[-1]

        now = datetime.now(timezone.utc).isoformat()
        return now, now

    @staticmethod
    def _aggregate_metrics(metrics_entries: List[Dict[str, Any]], tools_called: int) -> Dict[str, int]:
        total_tokens = 0
        total_duration_ms = 0
        model_calls = 0

        for entry in metrics_entries or []:
            if not isinstance(entry, dict):
                continue

            for key in _TOKEN_KEYS:
                value = entry.get(key)
                if isinstance(value, (int, float)):
                    total_tokens += int(value)
                    break

            for key in _DURATION_KEYS:
                value = entry.get(key)
                if isinstance(value, (int, float)):
                    total_duration_ms += int(value)
                    break

            entry_type = entry.get("type")
            if entry_type in _MODEL_CALL_TYPES or any(k in entry for k in _TOKEN_KEYS):
                model_calls += 1

        return {
            "total_tokens": total_tokens,
            "total_duration_ms": total_duration_ms,
            "model_calls": model_calls,
            "tools_called": tools_called,
        }

    @staticmethod
    def _render_markdown(report: Dict[str, Any]) -> str:
        lines: List[str] = []
        lines.append(f"# Relatório da Tarefa {report.get('task_id', '')}")
        lines.append("")
        lines.append(f"- **Objetivo:** {report.get('objective')}")
        lines.append(f"- **Sucesso:** {'✅' if report.get('success') else '❌'}")
        lines.append(f"- **Início:** {report.get('start_time')}")
        lines.append(f"- **Fim:** {report.get('end_time')}")
        lines.append("")

        metrics = report.get("metrics") or {}
        lines.append("## Métricas")
        lines.append(f"- Total de tokens: {metrics.get('total_tokens', 0)}")
        lines.append(f"- Duração total (ms): {metrics.get('total_duration_ms', 0)}")
        lines.append(f"- Chamadas ao modelo: {metrics.get('model_calls', 0)}")
        lines.append(f"- Ferramentas chamadas: {metrics.get('tools_called', 0)}")
        lines.append("")

        steps = report.get("steps") or []
        lines.append("## Passos")
        if not steps:
            lines.append("_Nenhum passo registrado._")
        for step in steps:
            result = step.get("result") or {}
            status = "✅" if result.get("ok") else "❌"
            lines.append(f"### {step.get('index')}. {step.get('tool')} {status}")
            lines.append(f"- Args: `{json.dumps(step.get('args', {}), ensure_ascii=False, default=str)}`")
            if result.get("error"):
                lines.append(f"- Erro: {result.get('error')}")
            lines.append(f"- Resultado (resumo): {result.get('data_summary', '')}")
            if "cache_hit" in step:
                lines.append(f"- Cache hit: {step.get('cache_hit')}")
            lines.append("")

        replan_events = report.get("replan_events") or []
        if replan_events:
            lines.append("## Eventos de Replanejamento")
            for ev in replan_events:
                lines.append(
                    f"- Passo original {ev.get('original_step')}: {ev.get('error')} "
                    f"→ estratégia: {ev.get('strategy')} "
                    f"({ev.get('replacement_steps')} novo(s) passo(s))"
                )
            lines.append("")

        errors = report.get("errors") or []
        if errors:
            lines.append("## Erros")
            for err in errors:
                lines.append(f"- {err}")
            lines.append("")

        lines.append("## Resposta Final (prévia)")
        lines.append(report.get("final_answer_preview", ""))
        lines.append("")

        return "\n".join(lines)
