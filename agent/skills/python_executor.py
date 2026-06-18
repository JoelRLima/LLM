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
    "operator", "typing", "textwrap", "unicodedata", "fractions"
}

# Builtins permitidos
ALLOWED_BUILTINS = {
    "abs", "all", "any", "bin", "bool", "bytes", "chr", "complex",
    "dict", "divmod", "enumerate", "filter", "float", "format",
    "frozenset", "getattr", "hasattr", "hash", "hex", "id",
    "int", "isinstance", "issubclass", "iter", "len", "list",
    "map", "max", "min", "next", "oct", "ord", "pow", "print",
    "range", "repr", "reversed", "round", "set", "slice",
    "sorted", "str", "sum", "tuple", "type", "zip"
}

class PythonExecutorSkill(BaseSkill):
    name = "python_executor"
    description = "Executa código Python seguro em um subprocesso isolado, com timeout e imports restritos."

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
            # Bloqueia imports perigosos
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = node.module if isinstance(node, ast.ImportFrom) else None
                names = [alias.name for alias in node.names]
                for name in names:
                    if module:
                        full = f"{module}.{name}"
                    else:
                        full = name
                    base = full.split(".")[0]
                    if base not in ALLOWED_MODULES and base != "__future__":
                        return f"Import proibido: '{full}'"
            # Bloqueia acesso a atributos perigosos
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "__builtins__" or node.attr.startswith("__"):
                    return "Acesso a builtins perigoso detectado."
            # Bloqueia chamadas a open() em modo escrita
            if isinstance(node, ast.Call):
                func = node.func
                func_name = None
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr
                if func_name == "open" and len(node.args) >= 2:
                    mode_arg = node.args[1]
                    if isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str):
                        if any(m in mode_arg.value for m in ("w", "a", "x", "+")):
                            return "open() em modo escrita/append é proibido. Use a skill file_writer para escrever arquivos."
                # Bloqueia também calls de keywords: open("f", mode="w")
                if func_name == "open":
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            if any(m in str(kw.value.value) for m in ("w", "a", "x", "+")):
                                return "open() em modo escrita/append é proibido. Use a skill file_writer para escrever arquivos."
                # Bloqueia os.remove, os.unlink, shutil.rmtree etc.
                if isinstance(func, ast.Attribute) and func.attr in ("remove", "unlink", "rmtree", "rmdir", "rename"):
                    return f"Chamada proibida: '{func.attr}' pode deletar arquivos. Operação não permitida."
        return None

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

        # Validação de segurança via AST
        validation_error = self._validate_code(code)
        if validation_error:
            return {
                "ok": False,
                "done": True,
                "error": validation_error,
                "message": f"Erro de segurança na validação: {validation_error}"
            }

        # Cria um arquivo temporário com o código
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
                f.write(code)
                temp_path = f.name
        except Exception as e:
            return {
                "ok": False,
                "done": True,
                "error": str(e),
                "message": "Erro ao criar arquivo temporário."
            }

        try:
            # Executa em subprocesso isolado
            result = subprocess.run(
                [sys.executable, temp_path],
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            output = result.stdout
            if result.stderr:
                output += "\n[stderr]\n" + result.stderr

            # Determina sucesso baseado no código de saída
            if result.returncode == 0:
                return {
                    "ok": True,
                    "done": True,
                    "data": output.strip() or "(sem saída)",
                    "error": None,
                    "message": "Código executado com sucesso."
                }
            else:
                return {
                    "ok": False,
                    "done": True,
                    "error": f"Código terminou com erro (exit {result.returncode})",
                    "message": output.strip() or "(sem saída)"
                }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "done": True,
                "error": f"Timeout após {self.timeout}s",
                "message": "O código excedeu o tempo limite."
            }
        except Exception as e:
            return {
                "ok": False,
                "done": True,
                "error": str(e),
                "message": "Erro ao executar o subprocesso."
            }
        finally:
            # Remove o arquivo temporário
            try:
                os.unlink(temp_path)
            except Exception:
                pass