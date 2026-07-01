import datetime
import difflib
import os
import py_compile
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

from logger import logger

MEMORY_BACKUP_DIR = "memory_backups"


class ValidationFailedError(Exception):
    """
    Lançada pelo `WorkspaceManager.lint_check` quando uma ou mais verificações
    de validação pós-modificação falham e a chave de configuração
    `validation.fail_triggers_replan` está definida como `true`.

    O `PlanExecutor` deve capturar esta exceção e acionar o fluxo de
    replanejamento (`agent/replan.py`) em vez de simplesmente exibir o erro
    no console.
    """
    pass



class WorkspaceManager:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.restore_points: List[Dict[str, str]] = []
        self.created_files: List[str] = []

    def create_restore_point(self, plan: list) -> None:
        """
        Cria backups de todos os arquivos que o plano pretende modificar.
        Arquivos que ainda não existem (serão criados pelo plano) são
        registrados em `created_files` para que o rollback possa removê-los.
        """
        if not plan:
            return

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        restore_dir = os.path.join(MEMORY_BACKUP_DIR, "restore", timestamp)
        os.makedirs(restore_dir, exist_ok=True)

        for step in plan:
            tool = step.get("tool", "") if isinstance(step, dict) else ""
            args = step.get("args", {}) if isinstance(step, dict) else {}
            if tool in ("file_writer", "shell", "python_executor"):
                file_path = args.get("file_path") or args.get("target") or ""
                if not file_path:
                    continue
                if os.path.exists(file_path):
                    backup_path = os.path.join(restore_dir, file_path.replace(os.sep, "_"))
                    try:
                        shutil.copy2(file_path, backup_path)
                        self.restore_points.append({"original": file_path, "backup": backup_path})
                        if self.verbose:
                            print(f"[DEBUG] Checkpoint salvo para '{file_path}'")
                    except Exception as e:
                        logger.warning(f"Falha ao criar checkpoint para '{file_path}': {e}")
                else:
                    if file_path not in self.created_files:
                        self.created_files.append(file_path)
                        if self.verbose:
                            print(f"[DEBUG] '{file_path}' marcado como novo (sem checkpoint, será removido em rollback).")

    def rollback(self) -> None:
        """
        Restaura todos os arquivos a partir dos backups, na ordem inversa,
        e remove arquivos que foram criados durante a tarefa que falhou.
        """
        if not self.restore_points and not self.created_files:
            return

        if self.verbose:
            print("⏪ [ROLLBACK] Restaurando arquivos ao estado original...")

        for entry in reversed(self.restore_points):
            try:
                shutil.copy2(entry["backup"], entry["original"])
                os.remove(entry["backup"])
                if self.verbose:
                    print(f"   ✅ Restaurado: {entry['original']}")
            except Exception as e:
                logger.error(f"Falha ao restaurar '{entry['original']}': {e}")

        for file_path in reversed(self.created_files):
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    if self.verbose:
                        print(f"   🗑️  Removido (criado durante a tarefa): {file_path}")
            except Exception as e:
                logger.error(f"Falha ao remover arquivo criado '{file_path}': {e}")

        self.restore_points.clear()
        self.created_files.clear()

    @staticmethod
    def show_diff(file_path: str, new_content: str) -> None:
        """
        Exibe a diferença entre o arquivo original e o novo conteúdo usando difflib.
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                original = f.read()
        except Exception:
            original = ""

        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=file_path,
            tofile=f"{file_path} (proposto)",
        )
        diff_text = ''.join(diff)
        if diff_text.strip():
            print(f"\n📝 [DIFF] Mudanças propostas para '{file_path}':")
            print(diff_text)
        else:
            print(f"📝 [DIFF] Nenhuma mudança em '{file_path}'.")

    @staticmethod
    def _load_validation_config() -> Dict[str, Any]:
        """
        Carrega a seção `validation` de `config.json` (via `config.carregar_config`),
        aplicando um conjunto de padrões seguros caso o arquivo, a chave ou algum
        subcampo estejam ausentes ou malformados. Isso garante que `lint_check`
        nunca quebre por causa de um `config.json` incompleto.
        """
        default_validation: Dict[str, Any] = {
            "enabled": True,
            "ruff": False,
            "mypy": False,
            "pytest": False,
            "pytest_dir": "tests/",
            "fail_triggers_replan": False,
        }

        try:
            from config import carregar_config
            config = carregar_config()
        except Exception as e:
            logger.warning(
                f"Não foi possível carregar 'config.json' para a validação pós-modificação "
                f"({e}). Usando os padrões de validação."
            )
            return default_validation

        validation_cfg = config.get("validation")
        if not isinstance(validation_cfg, dict):
            return default_validation

        merged = dict(default_validation)
        for chave in default_validation:
            if chave in validation_cfg:
                merged[chave] = validation_cfg[chave]
        return merged

    @staticmethod
    def _run_ruff(file_path: str) -> Optional[str]:
        """Executa `ruff check` sobre `file_path`. Retorna a mensagem de erro ou None."""
        try:
            result = subprocess.run(
                ["ruff", "check", file_path],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                saida = (result.stdout + result.stderr).strip()
                return f"Ruff: {saida}" if saida else "Ruff: verificação falhou (sem saída detalhada)."
        except FileNotFoundError:
            logger.warning("Ferramenta 'ruff' não está instalada; verificação ignorada.")
        except subprocess.TimeoutExpired:
            logger.warning("Verificação 'ruff' excedeu o tempo limite (10s); ignorada.")
        except Exception as e:
            logger.warning(f"Falha inesperada ao executar 'ruff': {e}")
        return None

    @staticmethod
    def _run_mypy(file_path: str) -> Optional[str]:
        """Executa `mypy --ignore-missing-imports` sobre `file_path`. Retorna erro ou None."""
        try:
            result = subprocess.run(
                ["mypy", "--ignore-missing-imports", file_path],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                saida = (result.stdout + result.stderr).strip()
                return f"Mypy: {saida}" if saida else "Mypy: verificação falhou (sem saída detalhada)."
        except FileNotFoundError:
            logger.warning("Ferramenta 'mypy' não está instalada; verificação ignorada.")
        except subprocess.TimeoutExpired:
            logger.warning("Verificação 'mypy' excedeu o tempo limite (10s); ignorada.")
        except Exception as e:
            logger.warning(f"Falha inesperada ao executar 'mypy': {e}")
        return None

    @staticmethod
    def _run_pytest(pytest_dir: str) -> Optional[str]:
        """Executa `pytest <pytest_dir>` como módulo (portável). Retorna erro ou None."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", pytest_dir],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                saida = (result.stdout + result.stderr).strip()
                return f"Pytest: {saida}" if saida else "Pytest: verificação falhou (sem saída detalhada)."
        except FileNotFoundError:
            logger.warning("Ferramenta 'pytest' não está instalada; verificação ignorada.")
        except subprocess.TimeoutExpired:
            logger.warning("Verificação 'pytest' excedeu o tempo limite (10s); ignorada.")
        except Exception as e:
            logger.warning(f"Falha inesperada ao executar 'pytest': {e}")
        return None

    @staticmethod
    def lint_check(file_path: str) -> Optional[str]:
        """
        Executa a validação automática pós-modificação de um arquivo Python.

        Sempre roda a verificação sintática nativa (`py_compile`), que não é
        configurável. Em seguida, de acordo com a seção `validation` de
        `config.json`, executa opcionalmente `ruff`, `mypy` e `pytest`.

        Retorna:
            - `None` se `file_path` não for um arquivo `.py`.
            - `""` (string vazia) se todas as verificações passarem.
            - Uma string com os erros encontrados, caso alguma verificação falhe
              e `validation.fail_triggers_replan` seja `false`.

        Lança:
            ValidationFailedError: se alguma verificação falhar e
            `validation.fail_triggers_replan` for `true`. O `PlanExecutor` deve
            capturar esta exceção para acionar o replanejamento.
        """
        if not file_path.endswith(".py"):
            return None

        errors: List[str] = []

        # 1. Verificação de sintaxe (obrigatória, não configurável)
        try:
            py_compile.compile(file_path, doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(f"Sintaxe: {str(e)}")

        validation_cfg = WorkspaceManager._load_validation_config()

        if validation_cfg.get("enabled", True):
            if validation_cfg.get("ruff", False):
                ruff_error = WorkspaceManager._run_ruff(file_path)
                if ruff_error:
                    errors.append(ruff_error)

            if validation_cfg.get("mypy", False):
                mypy_error = WorkspaceManager._run_mypy(file_path)
                if mypy_error:
                    errors.append(mypy_error)

            if validation_cfg.get("pytest", False):
                pytest_dir = validation_cfg.get("pytest_dir", "tests/")
                pytest_error = WorkspaceManager._run_pytest(pytest_dir)
                if pytest_error:
                    errors.append(pytest_error)

        if not errors:
            return ""

        if validation_cfg.get("fail_triggers_replan", False):
            raise ValidationFailedError("\n".join(errors))

        return "\n".join(errors)