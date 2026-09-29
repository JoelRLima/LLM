from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any


class BaseSkill(ABC):
    """Interface que toda skill deve implementar."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Nome único da skill (usado pelo modelo para selecionar)."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Descrição curta do que a skill faz."""
        ...

    def get_schema(self) -> dict:
        """
        Retorna um dicionário descrevendo os argumentos esperados.
        Exemplo:
        {
            "expression": {
                "type": "string",
                "description": "A expressão matemática a ser avaliada"
            }
        }
        """
        return {}

    def validate_arguments(
        self,
        args: Mapping[str, Any],
        *,
        bound_fields: frozenset[str] = frozenset(),
        planning: bool = False,
    ) -> None:
        """Validate cross-field invariants owned by this operation contract."""

        del args, bound_fields, planning

    @abstractmethod
    def execute(self, args: dict) -> dict:
        """
        Executa a skill e retorna o resultado no contrato padrão:
        {
            "ok": bool,       # true se a operação foi bem-sucedida
            "done": bool,     # true se a tarefa da skill foi concluída
            "data": Any,      # dados de saída (pode ser None)
            "error": str,     # mensagem de erro (ou None)
            "message": str    # descrição amigável do resultado
        }
        """
        ...
