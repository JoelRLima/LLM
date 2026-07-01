import json
from typing import Any, Dict

from agent.memory import AgentMemory

class AgentState:
    """Estado completo e unificado do agente."""

    def __init__(self):
        # Dados da execução atual
        self.objective: str = None
        self.plan: list = []
        self.plan_step: int = 0
        self.last_result = None
        self.last_tool: str = None
        self.last_args: dict = None
        self.tool_history: list = []       # lista de {"tool", "args", "result"}

        # Componentes de memória e histórico
        self.memory = AgentMemory()
        self.events: list = []             # telemetria
        self.conversation_history: list = []   # histórico multi‑turno
        self.max_history_turns: int = 6

    def record_tool_result(self, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Registra o resultado de uma execução de ferramenta no estado global.

        Centraliza a mutação de last_tool, last_args, last_result e tool_history,
        evitando que múltiplos componentes escrevam diretamente nesses atributos.
        """
        self.last_tool = tool_name
        self.last_args = args
        self.last_result = result
        self.tool_history.append({"tool": tool_name, "args": args, "result": result})

    def to_checkpoint_dict(self) -> Dict[str, Any]:
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
        return json.loads(sanitized_text)

    def from_checkpoint_dict(self, data: Dict[str, Any]) -> None:
        """Restaura o estado a partir de um dicionário de checkpoint.

        Espera-se que `data` já tenha sido carregado (e validado) a partir de
        um arquivo JSON. Chaves ausentes preservam os valores padrão/atuais.
        """
        if not isinstance(data, dict):
            return

        self.objective = data.get("objective", self.objective)
        self.plan = data.get("plan", self.plan) or []
        self.plan_step = data.get("plan_step", self.plan_step) or 0
        self.last_tool = data.get("last_tool", self.last_tool)
        self.last_args = data.get("last_args", self.last_args)
        self.last_result = data.get("last_result", self.last_result)
        self.tool_history = data.get("tool_history", self.tool_history) or []
        self.events = data.get("events", self.events) or []
        self.conversation_history = data.get("conversation_history", self.conversation_history) or []

        memory_state = data.get("memory_state")
        if memory_state is not None and hasattr(self.memory, "state"):
            self.memory.state = memory_state