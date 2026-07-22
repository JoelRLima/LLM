import json
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence, cast

from agent.contracts import (
    AgentEvent,
    CheckpointData,
    PlanStep,
    ToolArgs,
    ToolHistoryEntry,
    ToolResult,
)
from agent.execution_state import TERMINAL_STEP_STATUSES, StepExecutionRecord, StepStatus
from agent.memory.memory import AgentMemory


class AgentState:
    """Estado completo e unificado do agente."""

    def __init__(self) -> None:
        # Dados da execução atual
        self.objective: Optional[str] = None
        self.plan: List[PlanStep] = []
        self.plan_step: int = 0
        self.current_step_id: Optional[str] = None
        self.step_records: Dict[str, StepExecutionRecord] = {}
        self.last_result: Optional[ToolResult] = None
        self.last_tool: Optional[str] = None
        self.last_args: Optional[ToolArgs] = None
        self.tool_history: List[ToolHistoryEntry] = []

        # Componentes de memória e histórico
        self.memory = AgentMemory()
        self.events: List[AgentEvent] = []
        self.conversation_history: List[Dict[str, str]] = []
        self.max_history_turns: int = 6

    def record_tool_result(
        self,
        tool_name: str,
        args: ToolArgs,
        result: ToolResult,
        step_id: Optional[str] = None,
    ) -> None:
        """Registra o resultado de uma execução de ferramenta no estado global.

        Centraliza a mutação de last_tool, last_args, last_result e tool_history,
        evitando que múltiplos componentes escrevam diretamente nesses atributos.
        """
        self.last_tool = tool_name
        self.last_args = args
        self.last_result = result
        self.tool_history.append(
            {
                "step_id": step_id or self.current_step_id,
                "tool": tool_name,
                "args": args,
                "result": result,
            }
        )

    @staticmethod
    def _new_step_id() -> str:
        return f"step-{uuid.uuid4().hex}"

    def set_plan(self, plan: Sequence[Mapping[str, Any]]) -> None:
        """Substitui o plano e preserva registros de IDs que sobreviveram à transformação."""
        normalized: List[PlanStep] = []
        records: Dict[str, StepExecutionRecord] = {}
        for raw_step in plan:
            step = cast(PlanStep, dict(raw_step))
            args = step.get("args")
            step["args"] = dict(args) if isinstance(args, dict) else {}
            step_id = str(step.get("_step_id") or self._new_step_id())
            step["_step_id"] = step_id
            normalized.append(step)
            records[step_id] = self.step_records.get(step_id) or StepExecutionRecord(step_id=step_id)
        self.plan = normalized
        self.step_records = records
        if self.current_step_id not in records:
            self.current_step_id = None

    def reset_execution(self) -> None:
        self.plan = []
        self.plan_step = 0
        self.current_step_id = None
        self.step_records = {}

    def clear_plan(self) -> None:
        self.reset_execution()

    def insert_plan_step(self, index: int, step: Mapping[str, Any]) -> None:
        prepared = cast(PlanStep, dict(step))
        step_id = str(prepared.get("_step_id") or self._new_step_id())
        prepared["_step_id"] = step_id
        args = prepared.get("args")
        prepared["args"] = dict(args) if isinstance(args, dict) else {}
        self.plan.insert(index, prepared)
        self.step_records[step_id] = StepExecutionRecord(step_id=step_id)

    def remove_plan_step(self, index: int) -> None:
        step = self.plan.pop(index)
        step_id = str(step.get("_step_id", ""))
        self.step_records.pop(step_id, None)

    def replace_plan_step(
        self, index: int, new_steps: Sequence[Mapping[str, Any]]
    ) -> None:
        self.remove_plan_step(index)
        for offset, step in enumerate(new_steps):
            self.insert_plan_step(index + offset, step)

    def get_step_id(self, index: int) -> str:
        return str(self.plan[index]["_step_id"])

    def get_step_status(self, index: int) -> StepStatus:
        return self.step_records[self.get_step_id(index)].status

    def next_pending_index(self, start: int = 0) -> Optional[int]:
        for index in range(max(0, start), len(self.plan)):
            if self.get_step_status(index) is StepStatus.PENDING:
                return index
        return None

    def mark_step_running(self, index: int) -> None:
        step_id = self.get_step_id(index)
        record = self.step_records[step_id]
        record.status = StepStatus.RUNNING
        record.attempts += 1
        record.last_error = ""
        self.current_step_id = step_id
        self.plan_step = index + 1

    def mark_step_completed(self, index: int) -> None:
        self._mark_step_terminal(index, StepStatus.COMPLETED)

    def mark_step_failed(self, index: int, error: str = "") -> None:
        self._mark_step_terminal(index, StepStatus.FAILED, error)

    def mark_step_skipped(self, index: int, reason: str = "") -> None:
        self._mark_step_terminal(index, StepStatus.SKIPPED, reason)

    def _mark_step_terminal(self, index: int, status: StepStatus, error: str = "") -> None:
        step_id = self.get_step_id(index)
        record = self.step_records[step_id]
        record.status = status
        record.last_error = error
        if self.current_step_id == step_id:
            self.current_step_id = None

    def prepare_for_resume(
        self, retry_failed: bool = False, retry_skipped: bool = False
    ) -> None:
        for record in self.step_records.values():
            record.prepare_for_resume(
                retry_failed=retry_failed, retry_skipped=retry_skipped
            )
        self.current_step_id = None

    def all_steps_terminal(self) -> bool:
        return bool(self.step_records) and all(
            record.status in TERMINAL_STEP_STATUSES for record in self.step_records.values()
        )

    def add_event(self, event: AgentEvent) -> None:
        """Adiciona um evento ao histórico de telemetria."""
        self.events.append(event)

    def add_conversation_turn(self, user: str, agent: str) -> None:
        """Adiciona uma nova entrada ao histórico de conversa."""
        self.conversation_history.append({"user": user, "agent": agent})

    def to_checkpoint_dict(self) -> CheckpointData:
        """Serializa os campos necessários para retomar a tarefa atual.

        Usa `json.dumps`/`json.loads` com `default=str` como uma "ida e volta"
        de sanitização, garantindo que somente dados JSON-serializáveis
        (convertendo tipos exóticos, como datetime, para string) acabem no
        dicionário retornado.
        """
        memory_state = getattr(self.memory, "state", None)

        raw: Dict[str, Any] = {
            "objective": self.objective,
            "plan": self.plan,
            "plan_step": self.plan_step,
            "current_step_id": self.current_step_id,
            "step_records": [record.to_dict() for record in self.step_records.values()],
            "last_tool": self.last_tool,
            "last_args": self.last_args,
            "last_result": self.last_result,
            "tool_history": self.tool_history,
            "events": self.events,
            "conversation_history": self.conversation_history,
            "memory_state": memory_state,
        }

        # Round-trip via json para sanitizar tipos não serializáveis
        # (ex.: datetime) usando default=str, mantendo o retorno como dict.
        sanitized_text = json.dumps(raw, ensure_ascii=False, default=str)
        return cast(CheckpointData, json.loads(sanitized_text))

    def from_checkpoint_dict(
        self,
        data: Mapping[str, Any],
        retry_failed: bool = False,
        retry_skipped: bool = False,
    ) -> None:
        """Restaura o estado a partir de um dicionário de checkpoint.

        Espera-se que `data` já tenha sido carregado (e validado) a partir de
        um arquivo JSON. Chaves ausentes preservam os valores padrão/atuais.
        """
        if not isinstance(data, dict):
            return

        self.objective = data.get("objective", self.objective)
        raw_plan = data.get("plan", self.plan) or []
        self.set_plan(raw_plan if isinstance(raw_plan, list) else [])
        self.plan_step = data.get("plan_step", self.plan_step) or 0
        raw_records = data.get("step_records") or []
        if isinstance(raw_records, list):
            for raw_record in raw_records:
                if not isinstance(raw_record, dict):
                    continue
                record = StepExecutionRecord.from_dict(raw_record)
                if record.step_id in self.step_records:
                    self.step_records[record.step_id] = record
        self.current_step_id = data.get("current_step_id")
        self.last_tool = data.get("last_tool", self.last_tool)
        self.last_args = data.get("last_args", self.last_args)
        self.last_result = data.get("last_result", self.last_result)
        self.tool_history = data.get("tool_history", self.tool_history) or []
        self.events = data.get("events", self.events) or []
        self.conversation_history = data.get("conversation_history", self.conversation_history) or []

        memory_state = data.get("memory_state")
        if memory_state is not None and hasattr(self.memory, "state"):
            self.memory.state = memory_state
        self.prepare_for_resume(
            retry_failed=retry_failed, retry_skipped=retry_skipped
        )
