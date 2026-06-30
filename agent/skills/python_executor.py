"""
python_executor — Skill de execução de código Python.

Fase 4C — Isolation Box
========================
Este módulo evolui a skill original (validação AST + whitelist de imports +
timeout + subprocesso) adicionando uma arquitetura de Defense in Depth,
composta por camadas independentes de proteção, já que o ambiente alvo
(Windows, biblioteca padrão, sem containers/AppContainer/Hyper-V/WSL) não
oferece isolamento de sistema operacional.

Camadas implementadas:
    1. Workspace Efêmero       -> tempfile.TemporaryDirectory por execução.
    2. Isolamento do Processo  -> subprocess.run(cwd=<tempdir>), sem chdir.
    3. Sandbox por AST         -> validação estática expandida (imports,
                                   execução dinâmica, criação de processos,
                                   manipulação de builtins, path traversal,
                                   caminhos absolutos, APIs de path, escrita
                                   de arquivos).
    4. Validação Pós-Execução  -> inspeciona o estado final do workspace
                                   (qtde de arquivos/diretórios, profundidade,
                                   tamanhos, links simbólicos/junctions).
    5. Limitação de Recursos   -> timeout (já existente) + limites rígidos
                                   de stdout/stderr.
    6. Política Fail Closed    -> qualquer comportamento não classificável
                                   com segurança é rejeitado.

Nenhuma camada assume que as demais já bloquearam um comportamento — cada
uma é independente e redundante por design.

A API pública da Skill (nome da classe, propriedades, assinatura de
execute(), contrato de retorno) permanece inalterada.
"""
import ast
import os
import re
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

from logger import logger

from .base import BaseSkill

# ---------------------------------------------------------------------------
# Constantes originais (NÃO alteradas — API/comportamento preservados)
# ---------------------------------------------------------------------------

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
    "frozenset", "hash", "hex", "id", "__import__",
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

# ---------------------------------------------------------------------------
# Camada 3 — novas constantes de defense in depth
# ---------------------------------------------------------------------------

# Lista explícita de módulos negados. É redundante em relação à whitelist
# (ALLOWED_MODULES), mas funciona como segunda barreira independente: mesmo
# que a whitelist seja futuramente ampliada por engano, estes módulos
# continuam bloqueados explicitamente.
BLOCKED_MODULES = {
    "subprocess", "multiprocessing", "threading", "asyncio", "socket",
    "urllib", "http", "requests", "ssl", "ftplib", "telnetlib", "ctypes",
    "inspect", "importlib", "pkgutil", "builtins", "pickle", "marshal",
    "resource", "signal", "gc", "sysconfig", "site", "faulthandler",
    "tracemalloc", "os", "shutil", "pathlib", "sys",
}

# Métodos/atributos associados à criação de processos. Bloqueados pelo nome
# do atributo, independentemente do módulo de origem (defesa adicional caso
# o bloqueio de import seja contornado por algum caminho não previsto).
PROCESS_CREATION_ATTRS = {
    "system", "fork", "forkpty", "popen",
    "Popen", "run", "call", "check_call", "check_output",
    "create_subprocess_exec", "create_subprocess_shell",
    "execv", "execve", "execvp", "execvpe",
    "execl", "execle", "execlp", "execlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe",
}

# APIs de caminho usadas classicamente para escapar de uma sandbox baseada
# em caminhos relativos (resolvem para caminho absoluto real do SO).
DANGEROUS_PATH_APIS = {"abspath", "realpath", "resolve", "absolute"}

# Construtores de caminho — verificados mesmo sem import bem-sucedido,
# como camada extra (ex.: caso pathlib seja liberado futuramente).
PATH_CONSTRUCTORS = {
    "Path", "PurePath", "WindowsPath", "PosixPath",
    "PurePosixPath", "PureWindowsPath",
}

# Nomes de builtins "críticos" cuja reatribuição (monkey patch) é proibida.
# Não usamos o conjunto inteiro de ALLOWED_BUILTINS para não impedir o uso
# comum (embora não recomendado) de nomes como `list`, `str`, `id` etc. como
# variáveis — o foco aqui é proteger funções que sustentam a própria sandbox.
CRITICAL_BUILTINS_TO_PROTECT = {
    "open", "print", "compile", "eval", "exec", "__import__",
    "globals", "locals", "vars", "input", "breakpoint",
}

_ABS_WINDOWS_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _classify_path_string(value: str) -> Optional[str]:
    """
    Classifica uma string literal quanto a padrões de path traversal ou
    caminho absoluto. Retorna o motivo do bloqueio, ou None se a string
    não apresentar nenhum padrão suspeito.
    """
    if ".." in value:
        return "padrão de path traversal ('..') detectado"
    if _ABS_WINDOWS_RE.match(value):
        return "caminho absoluto do Windows detectado (ex.: C:\\, D:\\)"
    if value.startswith("\\\\"):
        return "caminho UNC detectado (ex.: \\\\Servidor)"
    if value.startswith("/"):
        return "caminho absoluto Unix detectado"
    return None


def _validate_path_arg(node: ast.AST) -> Optional[str]:
    """
    Valida estaticamente um nó de AST usado como argumento de caminho
    (ex.: primeiro argumento de open()). Política Fail Closed: se a
    expressão não puder ser classificada com segurança (não é uma string
    literal nem uma f-string com partes literais conhecidas), a operação
    é rejeitada.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _classify_path_string(node.value)

    if isinstance(node, ast.JoinedStr):  # f-strings
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                reason = _classify_path_string(value.value)
                if reason:
                    return reason
        # Partes dinâmicas dentro do f-string não podem ser validadas com
        # segurança -> fail closed.
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                return "caminho dinâmico (f-string com expressão) não pode ser validado estaticamente (política fail-closed)"
        return None

    # Qualquer outra expressão (variável, chamada de função, concatenação,
    # etc.) não pode ser classificada com segurança em tempo de análise
    # estática -> rejeitamos por política Fail Closed.
    return "caminho dinâmico não pode ser validado estaticamente (política fail-closed)"


class PythonExecutorSkill(BaseSkill):
    name = "python_executor"
    description = "Executa código Python seguro em um subprocesso isolado, com timeout, imports restritos e builtins restritos em runtime."

    # Camada 4 — limites de validação pós-execução
    MAX_FILES_CREATED = 20
    MAX_DIRS_CREATED = 10
    MAX_TREE_DEPTH = 5
    MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024       # 2 MB por arquivo
    MAX_TOTAL_SIZE_BYTES = 5 * 1024 * 1024      # 5 MB no total

    # Camada 5 — limites rígidos de saída (além da truncagem cosmética via
    # MAX_OUTPUT_CHARS, que permanece inalterada).
    MAX_STDOUT_HARD_LIMIT = 2_000_000
    MAX_STDERR_HARD_LIMIT = 2_000_000

    def __init__(self, timeout_seconds: int = 10):
        self.timeout = timeout_seconds

    def get_schema(self):
        return {
            "code": {
                "type": "string",
                "description": "Código Python a ser executado. Use print() para exibir resultados."
            }
        }

    # ------------------------------------------------------------------
    # Camada 3 — Sandbox por AST
    # ------------------------------------------------------------------

    def _validate_code(self, code: str) -> str | None:
        """Retorna None se o código for seguro, ou uma mensagem de erro."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return f"Erro de sintaxe: {e.msg} (linha {e.lineno})"

        for node in ast.walk(tree):
            # --- 3.1 Imports proibidos --------------------------------
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = node.module if isinstance(node, ast.ImportFrom) else None
                names = [alias.name for alias in node.names]
                for name in names:
                    full = f"{module}.{name}" if module else name
                    base = full.split(".")[0]
                    if base in BLOCKED_MODULES:
                        return f"Import explicitamente bloqueado pela política de sandbox: '{full}'"
                    if base not in ALLOWED_MODULES and base != "__future__":
                        return f"Import proibido: '{full}'"

            # Bloqueia QUALQUER acesso a atributo dunder, independente de o
            # valor base ser um Name, literal ou expressão encadeada.
            # Isso cobre o escape clássico de sandbox via introspecção de
            # classes, ex.: ().__class__.__bases__[0].__subclasses__()
            if isinstance(node, ast.Attribute):
                if node.attr.startswith("__") and node.attr.endswith("__"):
                    return f"Acesso a atributo reservado proibido: '{node.attr}'."

                # --- 3.7 APIs de caminho perigosas --------------------
                if node.attr in DANGEROUS_PATH_APIS:
                    return f"Uso proibido de API de resolução de caminho: '{node.attr}' (pode escapar da sandbox)."

                # --- 3.3 Criação de processos (por nome de atributo) --
                if node.attr in PROCESS_CREATION_ATTRS:
                    return f"Chamada proibida (criação/controle de processo): '{node.attr}'."

            # --- 3.2 Execução dinâmica + nomes perigosos --------------
            if isinstance(node, ast.Name) and node.id in DANGEROUS_NAMES:
                return f"Uso de '{node.id}' não é permitido."

            # --- 3.4 Manipulação de builtins / monkey patch -----------
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                if node.id == "__builtins__":
                    return "Reatribuição de '__builtins__' não é permitida."
                if node.id in CRITICAL_BUILTINS_TO_PROTECT:
                    return f"Monkey patch de função crítica não é permitido: '{node.id}'."

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in CRITICAL_BUILTINS_TO_PROTECT:
                    return f"Redefinição de função/classe crítica não é permitida: '{node.name}'."

            if isinstance(node, ast.Global):
                if "__builtins__" in node.names:
                    return "Declaração 'global __builtins__' não é permitida."

            if isinstance(node, ast.Call):
                func = node.func
                func_name = func.id if isinstance(func, ast.Name) else (
                    func.attr if isinstance(func, ast.Attribute) else None
                )

                if func_name in DANGEROUS_NAMES:
                    return f"Chamada proibida: '{func_name}'."

                if func_name in PROCESS_CREATION_ATTRS:
                    return f"Chamada proibida (criação/controle de processo): '{func_name}'."

                if func_name in ATTR_FUNCS:
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "__" in arg.value:
                            return f"Uso de '{func_name}' com atributo reservado não é permitido."

                # --- 3.5 / 3.6 / 3.8 — Construtores de caminho --------
                if isinstance(func, ast.Name) and func.id in PATH_CONSTRUCTORS:
                    for arg in node.args:
                        reason = _validate_path_arg(arg)
                        if reason:
                            return f"Construção de caminho não permitida ({func.id}): {reason}"

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

                    # --- 3.8 Toda chamada a open() precisa de caminho
                    # estaticamente classificável e contido na sandbox.
                    path_arg = None
                    if node.args:
                        path_arg = node.args[0]
                    else:
                        for kw in node.keywords:
                            if kw.arg == "file":
                                path_arg = kw.value
                                break
                    if path_arg is not None:
                        reason = _validate_path_arg(path_arg)
                        if reason:
                            return f"open() com caminho não permitido: {reason}"

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

        Observação: o exec()/compile() abaixo roda DENTRO do subprocesso
        isolado (Camada 2), nunca no processo do orquestrador — a regra de
        "nunca usar exec/eval/compile" se aplica ao código que orquestra a
        execução, não ao mecanismo interno necessário para rodar o script
        do usuário de forma restrita.
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

    # ------------------------------------------------------------------
    # Camada 4 — Validação Pós-Execução
    # ------------------------------------------------------------------

    @staticmethod
    def _is_reparse_point(path: str) -> bool:
        """
        Detecta reparse points / junctions no Windows. st_file_attributes
        só existe na implementação de os.stat() do Windows; em outros SOs
        a verificação é simplesmente ignorada (retorna False).
        """
        try:
            st = os.lstat(path)
        except OSError:
            return False
        attrs = getattr(st, "st_file_attributes", None)
        if attrs is None:
            return False
        file_attribute_reparse_point = 0x400
        return bool(attrs & file_attribute_reparse_point)

    def _inspect_sandbox(self, temp_dir: str) -> Dict[str, Any]:
        file_count = 0
        dir_count = 0
        max_depth = 0
        total_size = 0
        max_file_size = 0
        suspicious: List[str] = []

        for dirpath, dirnames, filenames in os.walk(temp_dir, followlinks=False):
            rel = os.path.relpath(dirpath, temp_dir)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            max_depth = max(max_depth, depth)

            for d in dirnames:
                full = os.path.join(dirpath, d)
                dir_count += 1
                if os.path.islink(full):
                    suspicious.append(f"link simbólico (diretório): {d}")
                elif self._is_reparse_point(full):
                    suspicious.append(f"reparse point/junction (diretório): {d}")

            for fname in filenames:
                full = os.path.join(dirpath, fname)
                file_count += 1
                try:
                    if os.path.islink(full):
                        suspicious.append(f"link simbólico (arquivo): {fname}")
                    elif self._is_reparse_point(full):
                        suspicious.append(f"reparse point (arquivo): {fname}")
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                total_size += size
                max_file_size = max(max_file_size, size)

        return {
            "file_count": file_count,
            "dir_count": dir_count,
            "max_depth": max_depth,
            "total_size": total_size,
            "max_file_size": max_file_size,
            "suspicious": suspicious,
        }

    def _validate_sandbox_state(self, temp_dir: str) -> Optional[str]:
        """
        Verifica o estado final do workspace efêmero após a execução.
        A AST (Camada 3) protege contra intenções conhecidas; esta camada
        protege contra os efeitos reais da execução — nenhuma substitui
        a outra.
        """
        stats = self._inspect_sandbox(temp_dir)

        # script.py sempre existe e não conta como "criado pelo usuário".
        file_count = max(0, stats["file_count"] - 1)

        if stats["suspicious"]:
            return "Estruturas não permitidas detectadas: " + "; ".join(stats["suspicious"][:5])
        if file_count > self.MAX_FILES_CREATED:
            return f"Quantidade de arquivos criados excede o limite ({file_count} > {self.MAX_FILES_CREATED})"
        if stats["dir_count"] > self.MAX_DIRS_CREATED:
            return f"Quantidade de diretórios criados excede o limite ({stats['dir_count']} > {self.MAX_DIRS_CREATED})"
        if stats["max_depth"] > self.MAX_TREE_DEPTH:
            return f"Profundidade da árvore excede o limite ({stats['max_depth']} > {self.MAX_TREE_DEPTH})"
        if stats["max_file_size"] > self.MAX_FILE_SIZE_BYTES:
            return f"Arquivo excede o tamanho máximo permitido ({stats['max_file_size']} > {self.MAX_FILE_SIZE_BYTES} bytes)"
        if stats["total_size"] > self.MAX_TOTAL_SIZE_BYTES:
            return f"Tamanho total da sandbox excede o limite ({stats['total_size']} > {self.MAX_TOTAL_SIZE_BYTES} bytes)"
        return None

    # ------------------------------------------------------------------
    # Execução
    # ------------------------------------------------------------------

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

        # Camada 3 — validação estática antes de qualquer execução.
        validation_error = self._validate_code(code)
        if validation_error:
            logger.debug(f"[python_executor] Falha de validação AST: {validation_error}")
            return {
                "ok": False,
                "done": True,
                "error": validation_error,
                "message": f"Erro de segurança na validação: {validation_error}"
            }

        wrapped_code = self._build_wrapper(code)

        # Camada 1 — Workspace Efêmero. O diretório (e tudo dentro dele) é
        # destruído ao sair do bloco `with`, mesmo em caso de exceção,
        # SyntaxError, timeout, KeyboardInterrupt ou retorno antecipado.
        try:
            with tempfile.TemporaryDirectory(
                prefix="agent_sandbox_", ignore_cleanup_errors=True
            ) as temp_dir:
                logger.debug(f"[python_executor] Sandbox criada em '{temp_dir}'")

                script_path = os.path.join(temp_dir, "script.py")
                try:
                    with open(script_path, "w", encoding="utf-8") as f:
                        f.write(wrapped_code)
                    logger.debug(f"[python_executor] script.py criado em '{script_path}'")
                except Exception as e:
                    logger.debug(f"[python_executor] Falha ao criar script.py: {e}")
                    return {
                        "ok": False,
                        "done": True,
                        "error": str(e),
                        "message": "Erro ao criar arquivo da sandbox.",
                    }

                # Camada 2 — Isolamento do Processo. Execução exclusivamente
                # via subprocess.run, nunca exec()/eval()/compile()/runpy/
                # importlib no processo do orquestrador. `cwd` aponta para o
                # workspace efêmero; jamais usamos os.chdir().
                try:
                    logger.debug("[python_executor] Iniciando execução isolada em subprocesso")
                    result = subprocess.run(
                        [sys.executable, "script.py"],
                        capture_output=True,
                        text=True,
                        timeout=self.timeout,
                        cwd=temp_dir,
                    )
                except subprocess.TimeoutExpired:
                    logger.debug(f"[python_executor] Timeout após {self.timeout}s")
                    return {
                        "ok": False,
                        "done": True,
                        "error": f"Timeout após {self.timeout}s",
                        "message": "O código excedeu o tempo limite.",
                    }
                except Exception as e:
                    logger.debug(f"[python_executor] Falha ao executar subprocesso: {e}")
                    return {
                        "ok": False,
                        "done": True,
                        "error": str(e),
                        "message": "Erro ao executar o subprocesso.",
                    }

                stdout = result.stdout or ""
                stderr = result.stderr or ""

                # Camada 5 — limites rígidos de recursos (saída).
                if len(stdout) > self.MAX_STDOUT_HARD_LIMIT or len(stderr) > self.MAX_STDERR_HARD_LIMIT:
                    logger.debug("[python_executor] Limite rígido de stdout/stderr excedido")
                    return {
                        "ok": False,
                        "done": True,
                        "error": "Limite de tamanho de stdout/stderr excedido.",
                        "message": "A execução foi rejeitada por exceder os limites de saída permitidos pela sandbox.",
                    }

                # Camada 4 — validação pós-execução do estado do workspace.
                post_validation_error = self._validate_sandbox_state(temp_dir)
                if post_validation_error:
                    logger.debug(f"[python_executor] Falha de validação pós-execução: {post_validation_error}")
                    return {
                        "ok": False,
                        "done": True,
                        "error": post_validation_error,
                        "message": f"Execução rejeitada pela validação pós-execução da sandbox: {post_validation_error}",
                    }

                output = stdout
                if stderr:
                    output += "\n[stderr]\n" + stderr

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
        finally:
            logger.debug("[python_executor] Sandbox destruída e limpeza concluída")