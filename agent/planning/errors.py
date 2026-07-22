

class ToolNotFoundError(Exception):
    """Exceção lançada quando uma ferramenta referenciada no plano não existe."""


class InvalidToolError(Exception):
    """Exceção lançada quando uma ferramenta existe mas não pode ser executada."""


class PlanExecutionError(Exception):
    """Erro genérico de execução do plano."""
