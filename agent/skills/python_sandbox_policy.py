"""Static, fail-closed policy for code accepted by ``python_executor``."""

from __future__ import annotations

import ast
import re

ALLOWED_MODULES = {
    "math", "itertools", "collections", "json", "re", "datetime",
    "statistics", "random", "string", "functools", "operator", "typing",
    "textwrap", "unicodedata", "fractions", "decimal", "heapq", "bisect",
}

ALLOWED_BUILTINS = {
    "abs", "all", "any", "bin", "bool", "bytes", "chr", "complex",
    "dict", "divmod", "enumerate", "filter", "float", "format",
    "frozenset", "hash", "hex", "id", "__import__", "int", "isinstance",
    "issubclass", "iter", "len", "list", "map", "max", "min", "next",
    "oct", "ord", "pow", "print", "range", "repr", "reversed", "round",
    "set", "slice", "sorted", "str", "sum", "tuple", "type", "zip",
    "open", "True", "False", "None", "Exception", "BaseException",
    "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError",
    "ZeroDivisionError", "ArithmeticError", "OverflowError",
    "FloatingPointError", "StopIteration", "StopAsyncIteration",
    "RuntimeError", "NotImplementedError", "NameError", "UnboundLocalError",
    "AssertionError", "LookupError", "OSError", "FileNotFoundError",
    "PermissionError", "IsADirectoryError", "NotADirectoryError",
    "RecursionError", "MemoryError", "GeneratorExit",
}

BLOCKED_MODULES = {
    "subprocess", "multiprocessing", "threading", "asyncio", "socket",
    "urllib", "http", "requests", "ssl", "ftplib", "telnetlib", "ctypes",
    "inspect", "importlib", "pkgutil", "builtins", "pickle", "marshal",
    "resource", "signal", "gc", "sysconfig", "site", "faulthandler",
    "tracemalloc", "os", "shutil", "pathlib", "sys",
}

DANGEROUS_NAMES = {
    "eval", "exec", "compile", "__import__", "globals", "locals", "vars",
    "input", "breakpoint", "memoryview",
}
ATTR_FUNCS = {"getattr", "setattr", "delattr"}
PROCESS_CREATION_ATTRS = {
    "system", "fork", "forkpty", "popen", "Popen", "run", "call",
    "check_call", "check_output", "create_subprocess_exec",
    "create_subprocess_shell", "execv", "execve", "execvp", "execvpe",
    "execl", "execle", "execlp", "execlpe", "spawnv", "spawnve",
    "spawnvp", "spawnvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe",
}
DANGEROUS_PATH_APIS = {"abspath", "realpath", "resolve", "absolute"}
PATH_CONSTRUCTORS = {
    "Path", "PurePath", "WindowsPath", "PosixPath", "PurePosixPath",
    "PureWindowsPath",
}
CRITICAL_BUILTINS = {
    "open", "print", "compile", "eval", "exec", "__import__", "globals",
    "locals", "vars", "input", "breakpoint",
}
_ABS_WINDOWS_RE = re.compile(r"^[A-Za-z]:[\\/]")


def classify_path_string(value: str) -> str | None:
    if ".." in value:
        return "padrão de path traversal ('..') detectado"
    if _ABS_WINDOWS_RE.match(value):
        return "caminho absoluto do Windows detectado (ex.: C:\\, D:\\)"
    if value.startswith("\\\\"):
        return "caminho UNC detectado (ex.: \\\\Servidor)"
    if value.startswith("/"):
        return "caminho absoluto Unix detectado"
    return None


def validate_path_arg(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return classify_path_string(node.value)
    if not isinstance(node, ast.JoinedStr):
        return "caminho dinâmico não pode ser validado estaticamente (política fail-closed)"
    for value in node.values:
        if isinstance(value, ast.FormattedValue):
            return "caminho dinâmico (f-string com expressão) não pode ser validado estaticamente (política fail-closed)"
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            reason = classify_path_string(value.value)
            if reason:
                return reason
    return None


def _validate_import(node: ast.Import | ast.ImportFrom) -> str | None:
    module = node.module if isinstance(node, ast.ImportFrom) else None
    for alias in node.names:
        full_name = f"{module}.{alias.name}" if module else alias.name
        base = full_name.split(".")[0]
        if base in BLOCKED_MODULES:
            return f"Import explicitamente bloqueado pela política de sandbox: '{full_name}'"
        if base not in ALLOWED_MODULES and base != "__future__":
            return f"Import proibido: '{full_name}'"
    return None


def _validate_attribute(node: ast.Attribute) -> str | None:
    if node.attr.startswith("__") and node.attr.endswith("__"):
        return f"Acesso a atributo reservado proibido: '{node.attr}'."
    if node.attr in DANGEROUS_PATH_APIS:
        return f"Uso proibido de API de resolução de caminho: '{node.attr}' (pode escapar da sandbox)."
    if node.attr in PROCESS_CREATION_ATTRS:
        return f"Chamada proibida (criação/controle de processo): '{node.attr}'."
    return None


def _validate_name(node: ast.Name) -> str | None:
    if node.id in DANGEROUS_NAMES:
        return f"Uso de '{node.id}' não é permitido."
    if not isinstance(node.ctx, ast.Store):
        return None
    if node.id == "__builtins__":
        return "Reatribuição de '__builtins__' não é permitida."
    if node.id in CRITICAL_BUILTINS:
        return f"Monkey patch de função crítica não é permitido: '{node.id}'."
    return None


def _validate_definition(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str | None:
    if node.name in CRITICAL_BUILTINS:
        return f"Redefinição de função/classe crítica não é permitida: '{node.name}'."
    return None


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _validate_reserved_attribute_call(node: ast.Call, name: str | None) -> str | None:
    if name not in ATTR_FUNCS:
        return None
    for argument in node.args:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str) and "__" in argument.value:
            return f"Uso de '{name}' com atributo reservado não é permitido."
    return None


def _validate_path_constructor(node: ast.Call) -> str | None:
    if not isinstance(node.func, ast.Name) or node.func.id not in PATH_CONSTRUCTORS:
        return None
    for argument in node.args:
        reason = validate_path_arg(argument)
        if reason:
            return f"Construção de caminho não permitida ({node.func.id}): {reason}"
    return None


def _open_mode(node: ast.Call) -> ast.AST | None:
    if len(node.args) >= 2:
        return node.args[1]
    for keyword in node.keywords:
        if keyword.arg == "mode":
            return keyword.value
    return None


def _open_path(node: ast.Call) -> ast.AST | None:
    if node.args:
        return node.args[0]
    for keyword in node.keywords:
        if keyword.arg == "file":
            return keyword.value
    return None


def _validate_open(node: ast.Call, name: str | None) -> str | None:
    if name != "open":
        return None
    mode = _open_mode(node)
    if isinstance(mode, ast.Constant) and any(marker in str(mode.value) for marker in ("w", "a", "x", "+")):
        return "open() em modo escrita/append é proibido. Use a skill file_writer para escrever arquivos."
    path = _open_path(node)
    if path is None:
        return None
    reason = validate_path_arg(path)
    return f"open() com caminho não permitido: {reason}" if reason else None


def _validate_call(node: ast.Call) -> str | None:
    name = _call_name(node)
    if name in DANGEROUS_NAMES:
        return f"Chamada proibida: '{name}'."
    if name in PROCESS_CREATION_ATTRS:
        return f"Chamada proibida (criação/controle de processo): '{name}'."
    for check in (
        lambda: _validate_reserved_attribute_call(node, name),
        lambda: _validate_path_constructor(node),
        lambda: _validate_open(node, name),
    ):
        error = check()
        if error:
            return error
    if isinstance(node.func, ast.Attribute) and node.func.attr in {"remove", "unlink", "rmtree", "rmdir", "rename"}:
        return f"Chamada proibida: '{node.func.attr}' pode deletar arquivos. Operação não permitida."
    return None


def _validate_node(node: ast.AST) -> str | None:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return _validate_import(node)
    if isinstance(node, ast.Attribute):
        return _validate_attribute(node)
    if isinstance(node, ast.Name):
        return _validate_name(node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return _validate_definition(node)
    if isinstance(node, ast.Global) and "__builtins__" in node.names:
        return "Declaração 'global __builtins__' não é permitida."
    if isinstance(node, ast.Call):
        return _validate_call(node)
    return None


def validate_code(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"Erro de sintaxe: {exc.msg} (linha {exc.lineno})"
    for node in ast.walk(tree):
        error = _validate_node(node)
        if error:
            return error
    return None
