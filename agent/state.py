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