import ast
import operator
import math
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

    def get_schema(self):
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
            raise ValueError(f"Erro de sintaxe: {e.msg} (posição {e.offset})")

        def _eval(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return float(node.value)
            elif isinstance(node, ast.BinOp):
                left = _eval(node.left)
                right = _eval(node.right)
                op_type = type(node.op)
                if op_type in self._BINARY_OPS:
                    return self._BINARY_OPS[op_type](left, right)
                raise ValueError(f"Operador binário não permitido: {op_type.__name__}")
            elif isinstance(node, ast.UnaryOp):
                operand = _eval(node.operand)
                op_type = type(node.op)
                if op_type in self._UNARY_OPS:
                    return self._UNARY_OPS[op_type](operand)
                raise ValueError(f"Operador unário não permitido: {op_type.__name__}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in self._ALLOWED_FUNCTIONS:
                    func = self._ALLOWED_FUNCTIONS[node.func.id]
                    args = [_eval(arg) for arg in node.args]
                    kwargs = {kw.arg: _eval(kw.value) for kw in node.keywords}
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        raise ValueError(f"Erro ao chamar {node.func.id}: {e}")
                else:
                    raise ValueError(f"Função não permitida: {getattr(node.func, 'id', 'desconhecida')}")
            elif isinstance(node, ast.Name):
                if node.id in self._ALLOWED_FUNCTIONS:
                    val = self._ALLOWED_FUNCTIONS[node.id]
                    if isinstance(val, (int, float)):
                        return float(val)
                raise ValueError(f"Nome não permitido: '{node.id}'")
            else:
                raise ValueError(f"Construção não suportada: {type(node).__name__}")

        result = _eval(tree.body)
        if isinstance(result, float):
            result = round(result, 12)  # Evita floats muito longos
        return result

    def execute(self, args: dict) -> dict:
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