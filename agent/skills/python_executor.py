import sys
import ast
import subprocess
import tempfile
import os
from .base import BaseSkill

# Módulos permitidos (whitelist)
ALLOWED_MODULES = {
    "math", "itertools", "collections", "json", "re",
    "datetime", "statistics", "random", "string", "functools",
    "operator", "typing", "textwrap", "unicodedata", "fractions",
    "decimal", "heapq", "bisect"
}

# Builtins permitidos — agora aplicados de fato em runtime (ver _build_wrapper).
ALLOWED_BUILTINS = {
    "abs", "all", "any", "bin", "bool", "bytes", "chr", "complex",
    "dict", "divmod", "enumerate", "filter", "float", "format",
    "frozenset", "hash", "hex", "id",
    "int", "isinstance", "issubclass", "iter", "len", "list",
    "map", "max", "min", "next", "oct", "ord", "pow", "print",
    "range", "repr", "reversed", "round", "set", "slice",
    "sorted", "str", "sum", "tuple", "type", "zip", "open",
    "True", "False", "None",
    # Exceções padrão — necessárias para try/except funcionar em código
    # gerado pelo LLM (ex.: divisão por zero, conversão de tipo, índice
    # fora do range). Não dão acesso a nada perigoso, são apenas tipos.
    "Exception", "BaseException", "ValueError", "TypeError",
    "KeyError", "IndexError", "AttributeError", "ZeroDivisionError",
    "ArithmeticError", "OverflowError", "FloatingPointError",
    "StopIteration", "StopAsyncIteration", "RuntimeError",
    "NotImplementedError", "NameError", "UnboundLocalError",
    "AssertionError", "LookupError", "OSError", "FileNotFoundError",
    "PermissionError", "IsADirectoryError", "NotADirectoryError",
    "RecursionError", "MemoryError", "GeneratorExit",
}

# Nomes que dão acesso a execução dinâmica ou introspecção perigosa.
# Bloqueados tanto como chamada quanto como referência simples (ex.: `f = eval`).
DANGEROUS_NAMES = {
    "eval", "exec", "compile", "__import__",
    "globals", "locals", "vars", "input",
    "breakpoint", "memoryview",
}

# getattr/setattr/delattr são vetores comuns para acessar dunders dinamicamente
# (ex.: getattr([], '__class__')). Bloqueamos apenas quando o argumento é uma
# string literal contendo "__" — não cobre concatenação dinâmica de string,
# que é um limite conhecido de análise estática.
ATTR_FUNCS = {"getattr", "setattr", "delattr"}

MAX_OUTPUT_CHARS = 4000


class PythonExecutorSkill(BaseSkill):
    name = "python_executor"
    description = "Executa código Python seguro em um subprocesso isolado, com timeout, imports restritos e builtins restritos em runtime."

    def __init__(self, timeout_seconds: int = 10):
        self.timeout = timeout_seconds

    def get_schema(self):
        return {
            "code": {
                "type": "string",
                "description": "Código Python a ser executado. Use print() para exibir resultados."
            }
        }

    def _validate_code(self, code: str) -> str | None:
        """Retorna None se o código for seguro, ou uma mensagem de erro."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return f"Erro de sintaxe: {e.msg} (linha {e.lineno})"

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = node.module if isinstance(node, ast.ImportFrom) else None
                names = [alias.name for alias in node.names]
                for name in names:
                    full = f"{module}.{name}" if module else name
                    base = full.split(".")[0]
                    if base not in ALLOWED_MODULES and base != "__future__":
                        return f"Import proibido: '{full}'"

            # Bloqueia QUALQUER acesso a atributo dunder, independente de o
            # valor base ser um Name, literal ou expressão encadeada.
            # Isso cobre o escape clássico de sandbox via introspecção de
            # classes, ex.: ().__class__.__bases__[0].__subclasses__()
            if isinstance(node, ast.Attribute):
                if node.attr.startswith("__") and node.attr.endswith("__"):
                    return f"Acesso a atributo reservado proibido: '{node.attr}'."

            if isinstance(node, ast.Name) and node.id in DANGEROUS_NAMES:
                return f"Uso de '{node.id}' não é permitido."

            if isinstance(node, ast.Call):
                func = node.func
                func_name = func.id if isinstance(func, ast.Name) else (
                    func.attr if isinstance(func, ast.Attribute) else None
                )

                if func_name in DANGEROUS_NAMES:
                    return f"Chamada proibida: '{func_name}'."

                if func_name in ATTR_FUNCS:
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "__" in arg.value:
                            return f"Uso de '{func_name}' com atributo reservado não é permitido."

                if func_name == "open" and len(node.args) >= 2:
                    mode_arg = node.args[1]
                    if isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str):
                        if any(m in mode_arg.value for m in ("w", "a", "x", "+")):
                            return "open() em modo escrita/append é proibido. Use a skill file_writer para escrever arquivos."
                if func_name == "open":
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            if any(m in str(kw.value.value) for m in ("w", "a", "x", "+")):
                                return "open() em modo escrita/append é proibido. Use a skill file_writer para escrever arquivos."

                if isinstance(func, ast.Attribute) and func.attr in ("remove", "unlink", "rmtree", "rmdir", "rename"):
                    return f"Chamada proibida: '{func.attr}' pode deletar arquivos. Operação não permitida."
        return None

    def _build_wrapper(self, code: str) -> str:
        """
        Monta o script real que roda no subprocesso. Em vez de executar o
        código do usuário diretamente com o interpretador completo (que dá
        acesso a todos os builtins, independente da whitelist declarada),
        o código é executado via exec() dentro de um namespace cujo
        '__builtins__' é restrito a ALLOWED_BUILTINS. Isso faz a whitelist
        valer de fato em runtime, e não apenas como checagem estática.
        """
        allowed_repr = repr(sorted(ALLOWED_BUILTINS))
        user_code_repr = repr(code)
        return (
            "import builtins as _builtins\n"
            f"_allowed_names = {allowed_repr}\n"
            "_restricted = {n: getattr(_builtins, n) for n in _allowed_names if hasattr(_builtins, n)}\n"
            "_user_code = " + user_code_repr + "\n"
            "_globals = {'__builtins__': _restricted, '__name__': '__main__'}\n"
            "exec(compile(_user_code, '<agent_code>', 'exec'), _globals)\n"
        )

    def execute(self, args: dict) -> dict:
        """
        Executa o código Python e retorna o contrato padrão:
        { ok, done, data, error, message }
        """
        code = args.get("code", "")
        if not code.strip():
            return {
                "ok": False,
                "done": True,
                "error": "código vazio",
                "message": "Nenhum código fornecido."
            }

        validation_error = self._validate_code(code)
        if validation_error:
            return {
                "ok": False,
                "done": True,
                "error": validation_error,
                "message": f"Erro de segurança na validação: {validation_error}"
            }

        wrapped_code = self._build_wrapper(code)

        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
                f.write(wrapped_code)
                temp_path = f.name
        except Exception as e:
            return {
                "ok": False,
                "done": True,
                "error": str(e),
                "message": "Erro ao criar arquivo temporário."
            }

        try:
            result = subprocess.run(
                [sys.executable, temp_path],
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            output = result.stdout
            if result.stderr:
                output += "\n[stderr]\n" + result.stderr

            total_chars = len(output)
            if total_chars > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + (
                    f"\n... (output truncado, {total_chars} caracteres no total)"
                )
                trunc_msg = f" (truncado, {total_chars} caracteres)"
            else:
                trunc_msg = ""

            if result.returncode == 0:
                return {
                    "ok": True,
                    "done": True,
                    "data": output.strip() or "(sem saída)",
                    "error": None,
                    "message": "Código executado com sucesso." + trunc_msg,
                }
            else:
                return {
                    "ok": False,
                    "done": True,
                    "error": f"Código terminou com erro (exit {result.returncode})",
                    "message": (output.strip() or "(sem saída)") + trunc_msg,
                }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "done": True,
                "error": f"Timeout após {self.timeout}s",
                "message": "O código excedeu o tempo limite.",
            }
        except Exception as e:
            return {
                "ok": False,
                "done": True,
                "error": str(e),
                "message": "Erro ao executar o subprocesso.",
            }
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass