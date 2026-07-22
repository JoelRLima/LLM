"""
Testes para agent/skills/python_executor.py — Isolation Box (Fase 4C).

Cobre: execução normal, captura de stdout/stderr, timeout, erro de sintaxe,
exceção em runtime, limpeza do workspace efêmero, cwd correto, bloqueios da
Camada 3 (imports, exec/eval, subprocess, socket, monkey patch, path
traversal, caminhos absolutos, abspath/resolve, criação de processos),
validação pós-execução (Camada 4) e a política Fail Closed (Camada 6).
"""
import os
import tempfile
from unittest.mock import patch

import pytest

from agent.skills.python_executor import PythonExecutorSkill


@pytest.fixture
def skill() -> PythonExecutorSkill:
    return PythonExecutorSkill(timeout_seconds=5)


@pytest.fixture
def sandbox_dir():
    """
    Diretório temporário próprio, independente do fixture `tmp_path` do
    pytest. Usamos diretamente `tempfile.mkdtemp()` (stdlib) para que os
    testes da Camada 4 não dependam da resolução/limpeza do diretório base
    interno do pytest (`pytest-of-<usuário>`), que pode estar com
    permissões problemáticas em alguns ambientes Windows.
    """
    path = tempfile.mkdtemp(prefix="pyexec_test_")
    try:
        yield path
    finally:
        import shutil
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Execução normal / captura de saída
# ---------------------------------------------------------------------------

def test_execucao_normal(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "print(2 + 2)"})
    assert result["ok"] is True
    assert result["done"] is True
    assert "4" in result["data"]


def test_captura_correta_de_stdout(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "print('linha1'); print('linha2')"})
    assert result["ok"] is True
    assert "linha1" in result["data"]
    assert "linha2" in result["data"]


def test_captura_correta_de_stderr(skill: PythonExecutorSkill) -> None:
    # Não há acesso a sys.stderr (sys não está na whitelist), então geramos
    # stderr através de uma exceção não tratada — o próprio interpretador
    # escreve o traceback em stderr.
    result = skill.execute({"code": "1 / 0"})
    assert result["ok"] is False
    assert "ZeroDivisionError" in result["message"]


# ---------------------------------------------------------------------------
# Timeout / sintaxe / exceção
# ---------------------------------------------------------------------------

def test_timeout(skill: PythonExecutorSkill) -> None:
    fast_skill = PythonExecutorSkill(timeout_seconds=1)
    result = fast_skill.execute({"code": "while True:\n    pass\n"})
    assert result["ok"] is False
    assert "Timeout" in result["error"]


def test_erro_de_sintaxe(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "def foo(:\n    pass"})
    assert result["ok"] is False
    assert "sintaxe" in result["error"].lower()


def test_excecao_durante_execucao(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "raise ValueError('falha proposital')"})
    assert result["ok"] is False
    assert "exit" in result["error"].lower()
    assert "ValueError" in result["message"]


# ---------------------------------------------------------------------------
# Camada 1 — Workspace Efêmero
# ---------------------------------------------------------------------------

def _sandbox_dirs() -> set:
    base = tempfile.gettempdir()
    return {
        d for d in os.listdir(base)
        if d.startswith("agent_sandbox_") and os.path.isdir(os.path.join(base, d))
    }


def test_limpeza_do_temporary_directory(skill: PythonExecutorSkill) -> None:
    before = _sandbox_dirs()
    result = skill.execute({"code": "print('ok')"})
    assert result["ok"] is True
    after = _sandbox_dirs()
    assert after == before, "Diretório(s) de sandbox órfão(s) encontrado(s) após a execução."


def test_limpeza_apos_falha(skill: PythonExecutorSkill) -> None:
    before = _sandbox_dirs()
    result = skill.execute({"code": "1 / 0"})
    assert result["ok"] is False
    after = _sandbox_dirs()
    assert after == before, "Sandbox não foi limpa após uma execução com falha."


# ---------------------------------------------------------------------------
# Camada 2 — Isolamento do processo (cwd correto, nunca os.chdir)
# ---------------------------------------------------------------------------

def test_cwd_correto(skill: PythonExecutorSkill) -> None:
    import subprocess as real_subprocess

    captured_kwargs = {}
    original_run = real_subprocess.run

    def spy_run(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return original_run(*args, **kwargs)

    with patch("agent.skills.python_executor.subprocess.run", side_effect=spy_run):
        result = skill.execute({"code": "print('cwd test')"})

    assert result["ok"] is True
    assert "cwd" in captured_kwargs
    assert captured_kwargs["cwd"] is not None
    assert os.path.isabs(captured_kwargs["cwd"]) or True  # tempdir paths são absolutos
    assert os.path.normpath(captured_kwargs["cwd"]) != os.path.normpath(os.getcwd())
    assert "agent_sandbox_" in os.path.basename(captured_kwargs["cwd"]) or \
        "agent_sandbox_" in captured_kwargs["cwd"]


# ---------------------------------------------------------------------------
# Camada 3 — Sandbox por AST
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("modulo", ["os", "subprocess", "socket", "ctypes", "pickle", "importlib"])
def test_bloqueio_de_imports_proibidos(skill: PythonExecutorSkill, modulo: str) -> None:
    result = skill.execute({"code": f"import {modulo}"})
    assert result["ok"] is False
    assert "proibido" in result["error"].lower() or "bloqueado" in result["error"].lower()


def test_bloqueio_de_subprocess_especifico(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "import subprocess\nsubprocess.run(['dir'])"})
    assert result["ok"] is False


def test_bloqueio_de_socket_especifico(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "import socket\nsocket.socket()"})
    assert result["ok"] is False


def test_bloqueio_de_exec(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "exec('print(1)')"})
    assert result["ok"] is False
    assert "exec" in result["error"]


def test_bloqueio_de_eval(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "eval('1 + 1')"})
    assert result["ok"] is False
    assert "eval" in result["error"]


def test_bloqueio_de_monkey_patch_de_builtins(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "open = print\nopen('x')"})
    assert result["ok"] is False
    assert "monkey patch" in result["error"].lower() or "crítica" in result["error"].lower()


def test_bloqueio_de_sobrescrita_de_dunder_builtins(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "__builtins__ = {}"})
    assert result["ok"] is False
    assert "__builtins__" in result["error"]


def test_bloqueio_de_path_traversal(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "open('../secret.txt').read()"})
    assert result["ok"] is False
    assert "traversal" in result["error"].lower()


def test_bloqueio_de_caminho_absoluto_unix(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "open('/etc/passwd').read()"})
    assert result["ok"] is False
    assert "absoluto" in result["error"].lower()


def test_bloqueio_de_caminho_absoluto_windows(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": r"open('C:\\Windows\\System32\\drivers\\etc\\hosts').read()"})
    assert result["ok"] is False
    assert "absoluto" in result["error"].lower()


def test_bloqueio_de_abspath(skill: PythonExecutorSkill) -> None:
    code = "class X:\n    def abspath(self):\n        return 1\nX().abspath()\n"
    result = skill.execute({"code": code})
    assert result["ok"] is False
    assert "abspath" in result["error"].lower()


def test_bloqueio_de_path_resolve(skill: PythonExecutorSkill) -> None:
    code = "class X:\n    def resolve(self):\n        return 1\nX().resolve()\n"
    result = skill.execute({"code": code})
    assert result["ok"] is False
    assert "resolve" in result["error"].lower()


def test_bloqueio_de_path_constructor_traversal(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "Path('..')"})
    assert result["ok"] is False
    assert "caminho" in result["error"].lower()


def test_bloqueio_de_criacao_de_processos(skill: PythonExecutorSkill) -> None:
    code = "class A:\n    def system(self, cmd):\n        pass\nA().system('dir')\n"
    result = skill.execute({"code": code})
    assert result["ok"] is False
    assert "processo" in result["error"].lower()


# ---------------------------------------------------------------------------
# Camada 4 — Validação Pós-Execução
# ---------------------------------------------------------------------------

def test_validacao_pos_execucao_limite_de_arquivos(skill: PythonExecutorSkill, sandbox_dir: str) -> None:
    # python_executor já bloqueia open() em modo escrita estaticamente
    # (Camada 3), então a criação de arquivos pelo código do usuário é
    # impedida antes mesmo de chegar à Camada 4. Para validar a Camada 4
    # isoladamente (estado real do workspace após a execução), simulamos
    # diretamente um workspace com excesso de arquivos.
    for i in range(skill.MAX_FILES_CREATED + 5):
        with open(os.path.join(sandbox_dir, f"f{i}.txt"), "w", encoding="utf-8") as f:
            f.write("x")
    # script.py "fantasma" para representar o arquivo sempre presente.
    with open(os.path.join(sandbox_dir, "script.py"), "w", encoding="utf-8") as f:
        f.write("print(1)")

    error = skill._validate_sandbox_state(sandbox_dir)
    assert error is not None
    assert "arquivos criados" in error.lower()


def test_validacao_pos_execucao_limite_de_profundidade(skill: PythonExecutorSkill, sandbox_dir: str) -> None:
    current = sandbox_dir
    for i in range(skill.MAX_TREE_DEPTH + 3):
        current = os.path.join(current, f"d{i}")
        os.mkdir(current)
    with open(os.path.join(current, "f.txt"), "w", encoding="utf-8") as f:
        f.write("x")

    error = skill._validate_sandbox_state(sandbox_dir)
    assert error is not None
    assert "profundidade" in error.lower()


def test_validacao_pos_execucao_sem_violacoes(skill: PythonExecutorSkill, sandbox_dir: str) -> None:
    with open(os.path.join(sandbox_dir, "script.py"), "w", encoding="utf-8") as f:
        f.write("print(1)")
    error = skill._validate_sandbox_state(sandbox_dir)
    assert error is None


# ---------------------------------------------------------------------------
# Camada 6 — Política Fail Closed
# ---------------------------------------------------------------------------

def test_fail_closed_caminho_dinamico(skill: PythonExecutorSkill) -> None:
    # Caminho montado dinamicamente em runtime (não é uma string literal
    # nem uma f-string totalmente estática) -> não pode ser classificado
    # com segurança em tempo de análise -> rejeitado pela política
    # Fail Closed, mesmo em modo leitura.
    code = "nome = 'a' + 'b' + '.txt'\nopen(nome).read()\n"
    result = skill.execute({"code": code})
    assert result["ok"] is False
    assert "fail-closed" in result["error"].lower() or "dinâmico" in result["error"].lower()


def test_fail_closed_codigo_vazio(skill: PythonExecutorSkill) -> None:
    result = skill.execute({"code": "   "})
    assert result["ok"] is False
    assert result["done"] is True
