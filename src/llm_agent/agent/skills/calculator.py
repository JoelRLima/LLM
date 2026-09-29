import ast
import math
import operator
from collections.abc import Callable
from typing import Any

from .base import BaseSkill


class CalculatorSkill(BaseSkill):
    name = "calculator"
    description = "Avalia expressões matemáticas seguras com operadores básicos, funções e constantes."

    # Operadores binários permitidos
    _BINARY_OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.Mod: operator.mod,
        ast.FloorDiv: operator.floordiv,
    }

    # Operadores unários
    _UNARY_OPS = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }

    # Funções matemáticas permitidas (mapeia nome -> função)
    _ALLOWED_FUNCTIONS = {
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "asin": math.asin,
        "acos": math.acos,
        "atan": math.atan,
        "atan2": math.atan2,
        "log": math.log,
        "log10": math.log10,
        "exp": math.exp,
        "pow": math.pow,
        "pi": math.pi,
        "e": math.e,
        "ceil": math.ceil,
        "floor": math.floor,
        "factorial": math.factorial,
        "gcd": math.gcd,
        "radians": math.radians,
        "degrees": math.degrees,
    }

    def get_schema(self) -> dict[str, Any]:
        return {
            "expression": {
                "type": "string",
                "description": (
                    "Expressão matemática a ser avaliada. "
                    "Exemplos: '2+2', 'sqrt(16)', 'sin(pi/2)', 'log(e)'"
                )
            }
        }

    def _safe_eval(self, expr: str) -> float:
        """Avalia uma expressão matemática de forma segura usando AST."""
        try:
            tree = ast.parse(expr.strip(), mode='eval')
        except SyntaxError as e:
            raise ValueError(f"Erro de sintaxe: {e.msg} (posição {e.offset})") from e

        return round(self._eval_node(tree.body), 12)

    def _eval_node(self, node: ast.expr) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp):
            return self._eval_binary(node)
        if isinstance(node, ast.UnaryOp):
            return self._eval_unary(node)
        if isinstance(node, ast.Call):
            return self._eval_call(node)
        if isinstance(node, ast.Name):
            return self._eval_name(node)
        raise ValueError(f"Construção não suportada: {type(node).__name__}")

    def _eval_binary(self, node: ast.BinOp) -> float:
        operation: Callable[[float, float], Any] | None = self._BINARY_OPS.get(type(node.op))
        if operation is None:
            raise ValueError(f"Operador binário não permitido: {type(node.op).__name__}")
        return float(operation(self._eval_node(node.left), self._eval_node(node.right)))

    def _eval_unary(self, node: ast.UnaryOp) -> float:
        value = self._eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.USub):
            return -value
        raise ValueError(f"Operador unário não permitido: {type(node.op).__name__}")

    def _eval_call(self, node: ast.Call) -> float:
        name = node.func.id if isinstance(node.func, ast.Name) else ""
        function = self._ALLOWED_FUNCTIONS.get(name)
        if not callable(function):
            raise ValueError(f"Função não permitida: {name or 'desconhecida'}")
        args = [self._eval_node(arg) for arg in node.args]
        kwargs = {keyword.arg: self._eval_node(keyword.value) for keyword in node.keywords if keyword.arg}
        try:
            return float(function(*args, **kwargs))
        except Exception as exc:
            raise ValueError(f"Erro ao chamar {name}: {exc}") from exc

    def _eval_name(self, node: ast.Name) -> float:
        value = self._ALLOWED_FUNCTIONS.get(node.id)
        if isinstance(value, (int, float)):
            return float(value)
        raise ValueError(f"Nome não permitido: '{node.id}'")

    def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Executa o cálculo e retorna o contrato padrão:
        { ok, done, data, error, message }
        """
        expression = args.get("expression", "")
        if not expression:
            return {
                "ok": False,
                "done": True,
                "error": "expressão vazia",
                "message": "Nenhuma expressão fornecida."
            }
        try:
            result = self._safe_eval(expression)
            # Formata de forma limpa
            if result == int(result):
                result = int(result)
            return {
                "ok": True,
                "done": True,
                "data": result,
                "error": None,
                "message": f"Cálculo realizado: {expression} = {result}"
            }
        except Exception as e:
            return {
                "ok": False,
                "done": True,
                "error": str(e),
                "message": f"Erro ao avaliar '{expression}': {e}"
            }
