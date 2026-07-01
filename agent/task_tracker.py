"""Rastreamento (tracking) da execução de um MacroPlan hierárquico.

O `TaskTracker` mantém o estado de progresso de uma execução hierárquica em
um arquivo JSON estruturado (fonte de verdade) e, em paralelo, renderiza um
arquivo Markdown equivalente para leitura humana. Nenhuma falha de I/O ou
de lógica interna deste módulo deve escapar para o chamador: todas as
operações públicas são protegidas por try/except e, em caso de erro, apenas
registram um aviso via `logger`.
"""
import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from logger import logger


class StepStatus(str, Enum):
    """Estados possíveis de um passo (MacroStep) durante a execução."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskStatus(str, Enum):
    """Estado global da execução hierárquica como um todo."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def _now_iso() -> str:
    """Retorna o timestamp atual em UTC, formato ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


def _step_to_dict(step: Any) -> Dict[str, Any]:
    """Normaliza um `MacroStep` (dataclass), dict ou objeto arbitrário.

    Aceita tanto a dataclass `MacroStep` definida em
    `agent.hierarchical_planner` quanto um dict equivalente, evitando que
    este módulo precise importar (e acoplar-se a) esse tipo diretamente.
    """
    if is_dataclass(step) and not isinstance(step, type):
        data = asdict(step)
    elif isinstance(step, dict):
        data = dict(step)
    else:
        data = {
            "id": getattr(step, "id", ""),
            "title": getattr(step, "title", ""),
            "goal": getattr(step, "goal", ""),
            "priority": getattr(step, "priority", "medium"),
            "depends_on": getattr(step, "depends_on", []) or [],
            "estimated_tools": getattr(step, "estimated_tools", []) or [],
        }

    priority = data.get("priority", "medium")
    if isinstance(priority, Enum):
        priority = priority.value

    return {
        "id": str(data.get("id", "")),
        "title": str(data.get("title", "")),
        "goal": str(data.get("goal", "")),
        "priority": priority if priority is not None else "medium",
        "status": StepStatus.PENDING.value,
        "estimated_tools": list(data.get("estimated_tools") or []),
        "duration_seconds": None,
        "summary": "",
        "depends_on": list(data.get("depends_on") or []),
        "notes": [],
    }


class TaskTracker:
    """Mantém e persiste o estado de uma execução hierárquica.

    O arquivo JSON (`json_path`) é a fonte de verdade estruturada; o
    arquivo Markdown (`markdown_path`) é uma renderização amigável,
    regenerada automaticamente a cada atualização. Ambas as escritas são
    atômicas (grava-se em um arquivo temporário e então renomeia-se),
    evitando arquivos corrompidos em caso de interrupção durante a
    gravação.
    """

    def __init__(
        self,
        json_path: str = "task_tracker.json",
        markdown_path: str = "task_tracker.md",
    ) -> None:
        self.json_path = json_path
        self.markdown_path = markdown_path
        self._data: Dict[str, Any] = {}

    # ---- Ciclo de vida ----

    def start(
        self,
        objective: str,
        steps: List[Any],
        planning_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Inicializa o tracking de uma nova execução hierárquica.

        `steps` deve ser a lista de `MacroStep` (ou dicts equivalentes) do
        `MacroPlan` gerado. `planning_metadata` pode conter as chaves
        `model`, `timestamp` e `prompt`, descrevendo como o plano foi
        gerado.
        """
        try:
            step_dicts = [_step_to_dict(s) for s in (steps or [])]
            now = _now_iso()
            metadata = planning_metadata or {}
            self._data = {
                "objective": objective,
                "status": TaskStatus.RUNNING.value,
                "progress": {
                    "completed": 0,
                    "total": len(step_dicts),
                    "percent": 0.0,
                },
                "metrics": {
                    "steps": len(step_dicts),
                    "tool_calls": 0,
                    "llm_calls": 0,
                },
                "planning": {
                    "model": metadata.get("model", ""),
                    "timestamp": metadata.get("timestamp", now),
                    "prompt": metadata.get("prompt", ""),
                },
                "steps": step_dicts,
                "final_summary": "",
                "failure_reason": "",
                "created_at": now,
                "updated_at": now,
            }
            self._persist()
        except Exception as e:
            logger.warning(f"TaskTracker: falha ao iniciar tracking: {e}")

    # ---- Consulta interna ----

    def _find_step(self, step_id: str) -> Optional[Dict[str, Any]]:
        for step in self._data.get("steps", []):
            if step.get("id") == step_id:
                return step
        return None

    # ---- Atualização de passos ----

    def mark_running(self, step_id: str) -> None:
        """Marca o passo `step_id` como em execução."""
        self._update_step_status(step_id, StepStatus.RUNNING)

    def mark_completed(
        self,
        step_id: str,
        summary: str = "",
        duration_seconds: Optional[float] = None,
    ) -> None:
        """Marca o passo `step_id` como concluído com sucesso."""
        self._update_step_status(
            step_id, StepStatus.COMPLETED, summary=summary, duration_seconds=duration_seconds
        )
        self._recompute_progress()

    def mark_failed(
        self,
        step_id: str,
        summary: str = "",
        duration_seconds: Optional[float] = None,
    ) -> None:
        """Marca o passo `step_id` como falho."""
        self._update_step_status(
            step_id, StepStatus.FAILED, summary=summary, duration_seconds=duration_seconds
        )
        self._recompute_progress()

    def mark_skipped(self, step_id: str, reason: str = "") -> None:
        """Marca o passo `step_id` como pulado (não executado)."""
        self._update_step_status(step_id, StepStatus.SKIPPED, summary=reason)
        self._recompute_progress()

    def add_note(self, step_id: str, note: str) -> None:
        """Adiciona uma nota livre a um passo, sem alterar seu status."""
        try:
            step = self._find_step(step_id)
            if step is None:
                return
            step.setdefault("notes", []).append({"text": note, "timestamp": _now_iso()})
            self._persist()
        except Exception as e:
            logger.warning(f"TaskTracker: falha ao adicionar nota ao passo '{step_id}': {e}")

    def record_tool_call(self, amount: int = 1) -> None:
        """Incrementa a métrica agregada de chamadas de ferramenta."""
        self._bump_metric("tool_calls", amount)

    def record_llm_call(self, amount: int = 1) -> None:
        """Incrementa a métrica agregada de chamadas ao modelo (LLM)."""
        self._bump_metric("llm_calls", amount)

    def _bump_metric(self, key: str, amount: int) -> None:
        try:
            metrics = self._data.setdefault("metrics", {})
            metrics[key] = metrics.get(key, 0) + amount
            self._persist()
        except Exception as e:
            logger.warning(f"TaskTracker: falha ao registrar métrica '{key}': {e}")

    def _update_step_status(
        self,
        step_id: str,
        status: StepStatus,
        summary: Optional[str] = None,
        duration_seconds: Optional[float] = None,
    ) -> None:
        try:
            step = self._find_step(step_id)
            if step is None:
                logger.warning(f"TaskTracker: passo '{step_id}' não encontrado.")
                return
            step["status"] = status.value
            if summary is not None:
                step["summary"] = summary
            if duration_seconds is not None:
                step["duration_seconds"] = round(duration_seconds, 3)
            self._data["updated_at"] = _now_iso()
            self._persist()
        except Exception as e:
            logger.warning(f"TaskTracker: falha ao atualizar passo '{step_id}': {e}")

    def _recompute_progress(self) -> None:
        steps = self._data.get("steps", [])
        total = len(steps)
        finished_states = (StepStatus.COMPLETED.value, StepStatus.FAILED.value, StepStatus.SKIPPED.value)
        completed = sum(1 for s in steps if s.get("status") in finished_states)
        percent = (completed / total * 100.0) if total else 0.0
        self._data["progress"] = {
            "completed": completed,
            "total": total,
            "percent": round(percent, 1),
        }

    # ---- Finalização ----

    def finish_success(self, final_summary: str = "") -> None:
        """Finaliza o tracking marcando a execução hierárquica como bem-sucedida."""
        try:
            self._data["status"] = TaskStatus.COMPLETED.value
            self._data["final_summary"] = final_summary or ""
            self._data["updated_at"] = _now_iso()
            self._persist()
        except Exception as e:
            logger.warning(f"TaskTracker: falha ao finalizar com sucesso: {e}")

    def finish_failure(self, reason: str = "") -> None:
        """Finaliza o tracking marcando a execução hierárquica como falha."""
        try:
            self._data["status"] = TaskStatus.FAILED.value
            self._data["failure_reason"] = reason or ""
            self._data["updated_at"] = _now_iso()
            self._persist()
        except Exception as e:
            logger.warning(f"TaskTracker: falha ao finalizar com falha: {e}")

    # ---- Persistência ----

    def _persist(self) -> None:
        self._write_json()
        self._write_markdown()

    def _atomic_write(self, path: str, content: str) -> None:
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)

    def _write_json(self) -> None:
        try:
            content = json.dumps(self._data, indent=2, ensure_ascii=False, default=str)
            self._atomic_write(self.json_path, content)
        except Exception as e:
            logger.warning(f"TaskTracker: falha ao gravar JSON em '{self.json_path}': {e}")

    def _write_markdown(self) -> None:
        try:
            content = self._render_markdown()
            self._atomic_write(self.markdown_path, content)
        except Exception as e:
            logger.warning(f"TaskTracker: falha ao gravar Markdown em '{self.markdown_path}': {e}")

    def _render_markdown(self) -> str:
        objective = self._data.get("objective", "")
        status = self._data.get("status", "")
        progress = self._data.get("progress", {})
        metrics = self._data.get("metrics", {})

        status_emoji = {
            StepStatus.PENDING.value: "⏳",
            StepStatus.RUNNING.value: "🔄",
            StepStatus.COMPLETED.value: "✅",
            StepStatus.FAILED.value: "❌",
            StepStatus.SKIPPED.value: "⏭️",
        }

        lines: List[str] = [
            f"# Execução Hierárquica: {objective}",
            "",
            f"**Status:** {status}",
            (
                f"**Progresso:** {progress.get('completed', 0)}/{progress.get('total', 0)} "
                f"({progress.get('percent', 0.0)}%)"
            ),
            (
                f"**Métricas:** {metrics.get('steps', 0)} passos · "
                f"{metrics.get('tool_calls', 0)} chamadas de ferramenta · "
                f"{metrics.get('llm_calls', 0)} chamadas LLM"
            ),
            "",
            "## Sub-objetivos",
            "",
        ]

        for step in self._data.get("steps", []):
            emoji = status_emoji.get(step.get("status"), "•")
            lines.append(f"### {emoji} {step.get('title', '')} (`{step.get('id', '')}`)")
            lines.append(f"- **Objetivo:** {step.get('goal', '')}")
            lines.append(f"- **Prioridade:** {step.get('priority', '')}")
            lines.append(f"- **Status:** {step.get('status', '')}")
            if step.get("depends_on"):
                lines.append(f"- **Depende de:** {', '.join(step.get('depends_on'))}")
            if step.get("duration_seconds") is not None:
                lines.append(f"- **Duração:** {step.get('duration_seconds')}s")
            if step.get("estimated_tools"):
                lines.append(f"- **Ferramentas estimadas:** {', '.join(step.get('estimated_tools'))}")
            if step.get("summary"):
                lines.append(f"- **Resumo:** {step.get('summary')}")
            notes = step.get("notes") or []
            if notes:
                lines.append("- **Notas:**")
                for note in notes:
                    lines.append(f"  - {note.get('text', '')}")
            lines.append("")

        final_summary = self._data.get("final_summary")
        failure_reason = self._data.get("failure_reason")
        if final_summary:
            lines.append("## Resposta final consolidada")
            lines.append(final_summary)
            lines.append("")
        if failure_reason:
            lines.append("## Motivo da falha")
            lines.append(failure_reason)
            lines.append("")

        return "\n".join(lines)
