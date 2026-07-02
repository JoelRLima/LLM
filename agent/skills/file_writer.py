import os
from pathlib import Path
from typing import Any, Dict, List
from .base import BaseSkill
from logger import logger

# Arquivos que o agente NUNCA pode modificar (núcleo do próprio sistema)
CORE_FILES_BLOCKLIST = [
    "agent/__init__.py",
    "agent/orchestrator.py",
    "agent/plan_executor.py",
    "agent/plan_builder.py",
    "agent/plan_validator.py",
    "agent/plan_optimizer.py",
    "agent/tool_metadata.py",
    "agent/grammars.py",
    "agent/model_client.py",
    "agent/context_manager.py",
    "agent/state.py",
    "agent/memory.py",
    "agent/router.py",
    "agent/prompts.py",
    "agent/final_response.py",
    "agent/error_handler.py",
    "agent/replan.py",
    "agent/reactive_loop.py",
    "agent/watchdog.py",
    "agent/cost_guard.py",
    "agent/auto_coder.py",
    "agent/tool_executor.py",
    "agent/workspace.py",
    "agent/parsers.py",
    "agent/semantic_memory.py",
    "agent/health_check.py",
    "agent/complexity.py",
    "agent/hierarchical_planner.py",
    "agent/hierarchical_executor.py",
    "agent/incremental_summarizer.py",
    "agent/task_report.py",
    "agent/task_tracker.py",
    "agent/security_patterns.py",
    "agent/security_scanner.py",
    "agent/cancellation.py",
    "agent/skills/base.py",
    "agent/skills/__init__.py",
    "agent/skills/code_analyzer.py",
    "agent/skills/grep.py",
    "agent/skills/file_reader.py",
    "agent/skills/file_writer.py",
    "agent/skills/python_executor.py",
    "agent/skills/shell.py",
    "agent/skills/directory_reader.py",
    "agent/skills/web_search.py",
    "agent/skills/summarize.py",
    "agent/skills/session_memory.py",
    "agent/skills/calculator.py",
    "agent/skills/echo.py",
    "agent/skills/git.py",
]

class FileWriterSkill(BaseSkill):
    name = "file_writer"
    description = (
        "Cria ou edita arquivos de texto no projeto. "
        "Pode criar um arquivo novo, sobrescrever o conteúdo completo, ou aplicar uma edição cirúrgica "
        "substituindo um bloco de texto específico. "
        "NÃO pode modificar os arquivos core do agente (cli.py, orchestrator.py, etc.)."
    )

    def __init__(self, base_dir: str = ".") -> None:
        self.base_dir = Path(base_dir).resolve()

    def _get_workspace_path(self, original_path):
        """Retorna o caminho correspondente no workspace isolado."""
        workspace_dir = os.path.join(self.base_dir, ".temp_analysis", "workspace")
        # Mantém a estrutura de diretórios relativa
        rel_path = original_path.relative_to(self.base_dir)
        workspace_path = os.path.join(workspace_dir, str(rel_path))
        os.makedirs(os.path.dirname(workspace_path), exist_ok=True)
        return workspace_path

    def _invalidate_cache(self, file_path: str) -> None:
        """Invalida o cache de leitura e os hashes após uma modificação confirmada."""
        try:
            # Remove cache de leitura
            temp_dir = os.path.join(self.base_dir, ".temp_analysis")
            temp_file = os.path.join(temp_dir, file_path)
            if os.path.exists(temp_file):
                os.remove(temp_file)
            # Remove do workspace
            workspace_copy = self._get_workspace_path(self.base_dir / file_path)
            if os.path.exists(workspace_copy):
                os.remove(workspace_copy)
        except Exception as e:
            logger.warning(f"Falha ao invalidar cache para '{file_path}': {e}")

    def get_schema(self) -> Dict[str, Any]:
        return {
            "action": "string: 'write' (cria/sobrescreve), 'patch' (substitui trecho exato), 'append' (adiciona ao final), 'delete_lines' (remove linhas), 'ast_patch' (substitui função/classe por nome)",
            "file_path": "string: caminho relativo do arquivo",
            "content": "string: conteúdo a escrever (para 'write' e 'append')",
            "old_content": "string: trecho EXATO a ser substituído (para 'patch')",
            "new_content": "string: novo trecho que vai substituir o old_content (para 'patch')",
            "start_line": "integer: linha inicial a deletar (para 'delete_lines')",
            "end_line": "integer: linha final a deletar (para 'delete_lines')",
            "target": "string: nome da função, classe ou método a substituir (para 'ast_patch')",
            "new_code": "string: novo código completo da função/classe (para 'ast_patch')",
            "old_hash": "string: hash SHA256 do arquivo antes da edição (opcional, para 'ast_patch')"
        }

    def _is_safe(self, requested: Path) -> tuple[bool, str]:
        """Verifica se o arquivo pode ser escrito com segurança."""
        try:
            rel = requested.relative_to(self.base_dir)
        except ValueError:
            return False, "Acesso fora do diretório do projeto não é permitido."

        rel_str = str(rel).replace("\\", "/")

        # Checa contra blocklist de arquivos core
        for blocked in CORE_FILES_BLOCKLIST:
            if rel_str == blocked or rel_str.endswith("/" + blocked):
                return False, f"'{rel_str}' é um arquivo core do agente e não pode ser modificado."

        # Bloqueia extensões binárias/executáveis
        blocked_exts = {".exe", ".dll", ".so", ".pyc", ".bin", ".jpg", ".png", ".zip", ".tar"}
        if requested.suffix.lower() in blocked_exts:
            return False, f"Extensão '{requested.suffix}' não é permitida para escrita."

        return True, ""

    def _ast_patch(self, file_path: Path, target: str, new_code: str, old_hash: str = None) -> dict:
        import ast, hashlib

        try:
            original = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return {"ok": False, "done": True, "error": str(e), "message": "Erro ao ler arquivo para patch."}

        # Validação opcional de hash
        if old_hash:
            current_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
            if current_hash != old_hash:
                return {"ok": False, "done": True, "error": "hash mismatch",
                        "message": f"Hash não confere (esperado {old_hash[:8]}..., atual {current_hash[:8]}...)."}

        try:
            tree = ast.parse(original)
        except SyntaxError as e:
            return {"ok": False, "done": True, "error": str(e), "message": "Arquivo com erro de sintaxe, patch não aplicado."}

        # Encontra o nó alvo
        found = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == target:
                found = node
                break

        if not found:
            return {"ok": False, "done": True, "error": "target not found",
                    "message": f"'{target}' não encontrado no arquivo."}

        # Determina linhas e indentação
        lines = original.splitlines(keepends=True)
        start = found.lineno - 1
        end = found.end_lineno - 1 if hasattr(found, 'end_lineno') and found.end_lineno else start

        orig_line = lines[start]
        indent = len(orig_line) - len(orig_line.lstrip())
        indent_str = orig_line[:indent]

        # Indenta o novo código com a mesma indentação
        new_lines = []
        for line in new_code.strip().splitlines(keepends=True):
            if line.strip():
                new_lines.append(indent_str + line.lstrip())
            else:
                new_lines.append(line)
        new_block = "".join(new_lines)

        # Monta novo conteúdo
        new_content_lines = lines[:start] + [new_block] + lines[end+1:]
        new_content = "".join(new_content_lines)

        # Verifica sintaxe do novo arquivo
        try:
            ast.parse(new_content)
        except SyntaxError as e:
            return {"ok": False, "done": True, "error": str(e), "message": "O novo código tem erro de sintaxe."}

        # Backup leve (na mesma pasta, com extensão .bak)
        backup_path = file_path.with_suffix(file_path.suffix + ".ast_bak")
        try:
            backup_path.write_text(original, encoding="utf-8")
        except Exception:
            pass  # backup não é obrigatório

        # Escreve o arquivo
        try:
            file_path.write_text(new_content, encoding="utf-8")
        except Exception as e:
            return {"ok": False, "done": True, "error": str(e), "message": "Erro ao escrever o patch."}

        return {"ok": True, "done": True, "message": f"'{target}' substituído com sucesso em '{file_path}'."}

    def execute(self, args: Dict[str, Any]) -> Any:
        action = args.get("action", "write")
        file_path = args.get("file_path", "")

        if not file_path:
            return {"ok": False, "done": True, "error": "Nenhum file_path fornecido."}

        try:
            requested = (self.base_dir / file_path).resolve()
        except Exception as e:
            return {"ok": False, "done": True, "error": f"Caminho inválido: {e}"}

        safe, reason = self._is_safe(requested)
        if not safe:
            return {"ok": False, "done": True, "error": f"Escrita bloqueada: {reason}"}

        # --- PROTEÇÃO INTERATIVA ---
        # Bloqueia modificações em arquivos do núcleo sem confirmação
        if "agent" in str(requested).replace("\\", "/").split("/"):
            resposta = input(f"\n⚠️  Modificar '{file_path}'? (s/N): ").strip().lower()
            if resposta not in ("s", "sim"):
                return {
                    "ok": False, "done": False,
                    "error": "bloqueado pelo usuário",
                    "message": "Modificação cancelada pelo usuário."
                }

        # --- WORKSPACE ISOLADO ---
        import shutil
        workspace_copy = None
        if requested.exists():
            # Cria o workspace copy com o conteúdo ORIGINAL completo
            workspace_copy = Path(self._get_workspace_path(requested))
            workspace_copy.parent.mkdir(parents=True, exist_ok=True)
            if not workspace_copy.exists():
                shutil.copy2(str(requested), str(workspace_copy))
            # Verifica se a cópia foi criada e tem conteúdo
            if not workspace_copy.exists() or workspace_copy.stat().st_size == 0:
                return {"ok": False, "done": True, "error": "Falha ao criar cópia no workspace."}
            target_for_edit = str(workspace_copy)
        else:
            # Arquivo novo: cria direto no workspace
            workspace_copy = Path(self._get_workspace_path(requested))
            workspace_copy.parent.mkdir(parents=True, exist_ok=True)
            target_for_edit = str(workspace_copy)

        # Garante que o diretório pai existe
        Path(target_for_edit).parent.mkdir(parents=True, exist_ok=True)

        try:
            if action == "write":
                content = args.get("content", "")
                with open(target_for_edit, "w", encoding="utf-8") as f:
                    f.write(content)
                lines = content.count("\n") + 1
                result_msg = f"'{file_path}' escrito com sucesso ({lines} linhas) no workspace."

            elif action == "append":
                content = args.get("content", "")
                with open(target_for_edit, "a", encoding="utf-8") as f:
                    f.write(content)
                result_msg = f"Conteúdo adicionado ao final de '{file_path}' no workspace."

            elif action == "patch":
                old_content = args.get("old_content")
                new_content = args.get("new_content", "")
                if old_content is None:
                    return {"ok": False, "done": True, "error": "Falta 'old_content' para a ação 'patch'."}
                with open(target_for_edit, "r", encoding="utf-8") as f:
                    current = f.read()
                if old_content not in current:
                    return {"ok": False, "done": True, "error": f"'old_content' não encontrado exatamente no arquivo. Verifique espaços e indentação."}
                patched = current.replace(old_content, new_content, 1)
                with open(target_for_edit, "w", encoding="utf-8") as f:
                    f.write(patched)
                result_msg = f"Patch aplicado em '{file_path}' no workspace."

            elif action == "delete_lines":
                start = args.get("start_line")
                end = args.get("end_line")
                if start is None or end is None:
                    return {"ok": False, "done": True, "error": "Falta 'start_line' ou 'end_line' para 'delete_lines'."}
                with open(target_for_edit, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                total_lines = len(lines)
                if not isinstance(start, int) or not isinstance(end, int):
                    return {"ok": False, "done": True, "error": "'start_line' e 'end_line' devem ser inteiros."}
                if start < 1 or end < 1:
                    return {"ok": False, "done": True, "error": "'start_line' e 'end_line' devem ser >= 1."}
                if start > end:
                    return {"ok": False, "done": True, "error": "'start_line' não pode ser maior que 'end_line'."}
                if start > total_lines or end > total_lines:
                    return {"ok": False, "done": True,
                            "error": f"Intervalo {start}-{end} fora do arquivo (total de {total_lines} linhas)."}
                new_lines = lines[:start - 1] + lines[end:]
                with open(target_for_edit, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
                result_msg = f"Linhas {start}-{end} removidas de '{file_path}' no workspace."

            elif action == "ast_patch":
                target = args.get("target", "")
                new_code = args.get("new_code", "")
                old_hash = args.get("old_hash")
                if not target or not new_code:
                    return {"ok": False, "done": True, "error": "'target' e 'new_code' são obrigatórios para ast_patch."}
                return self._ast_patch(target_for_edit, target, new_code, old_hash)

            else:
                return {"ok": False, "done": True, "error": f"Ação desconhecida: '{action}'."}

            # --- DIFF E CONFIRMAÇÃO ---
            if requested.exists():
                with open(str(requested), "r", encoding="utf-8") as f:
                    original_content = f.read()
            else:
                original_content = ""
            with open(target_for_edit, "r", encoding="utf-8") as f:
                new_content = f.read()

            print(f"\n📝 [DIFF] Mudanças propostas para '{file_path}':")
            import difflib
            diff = difflib.unified_diff(
                original_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=file_path,
                tofile=f"{file_path} (proposto)",
            )
            diff_text = "".join(diff)
            if diff_text.strip():
                print(diff_text)
            else:
                print("Nenhuma mudança detectada.")

            resposta = input(f"\n⚠️  Aplicar mudanças em '{file_path}'? (s/N): ").strip().lower()
            if resposta in ("s", "sim"):
                # Atomic commit
                import tempfile
                tmp_path = str(requested) + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                os.replace(tmp_path, str(requested))
                # Invalida cache
                self._invalidate_cache(file_path)
                return {"ok": True, "done": True, "message": f"Mudanças aplicadas em '{file_path}'."}
            else:
                return {"ok": True, "done": True, "message": f"Mudanças mantidas no workspace. Arquivo original não foi alterado."}

        except Exception as e:
            logger.error(f"FileWriterSkill error: {e}", exc_info=True)
            return {"ok": False, "done": True, "error": f"Erro ao escrever arquivo: {e}"}