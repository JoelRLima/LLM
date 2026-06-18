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