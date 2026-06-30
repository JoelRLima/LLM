import datetime
import difflib
import os
import py_compile
import shutil
import subprocess
from typing import Dict, List, Optional

from logger import logger

MEMORY_BACKUP_DIR = "memory_backups"

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
    def lint_check(file_path: str) -> Optional[str]:
        """
        Verifica a sintaxe e boas práticas de um arquivo Python.
        Retorna mensagem de erro se houver problemas, ou None se estiver limpo.
        """
        if not file_path.endswith(".py"):
            return None

        errors = []

        # 1. Verificação de sintaxe (py_compile)
        try:
            py_compile.compile(file_path, doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(f"Sintaxe: {str(e)}")

        # 2. Verificação de estilo com flake8 (opcional, se instalado)
        try:
            result = subprocess.run(
                ["flake8", "--max-line-length=120", file_path],
                capture_output=True, text=True, timeout=10
            )
            if result.stdout.strip():
                errors.append(f"Estilo: {result.stdout.strip()}")
        except Exception:
            pass  # flake8 não instalado ou falhou, ignora

        if errors:
            return "\n".join(errors)
        return None