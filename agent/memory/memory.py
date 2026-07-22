import datetime
import json
import os
import shutil
import sqlite3
from typing import Any, Dict, Optional

from agent.runtime import paths
from agent.runtime.logging import logger

MAX_MEMORY_BACKUPS = 5
MEMORY_BACKUP_DIR = paths.MEMORY_BACKUP_DIR

class AgentMemory:
    def __init__(self) -> None:
        self.db_path = paths.MEMORY_DB_FILE
        self.state: Dict[str, Any] = {
            "project_map": {},
            "key_findings": {},
            "files_index": {},
            "todo": [],
            "notes": {},
            "analyzed_files": {},   # { "caminho/arquivo.py": "resumo de uma linha" }
            "file_summaries": {}    # { "caminho/arquivo.py": "resumo detalhado" }
        }
        self._ensure_db()
        self._load_db_state()

    def _ensure_db(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS key_findings (key TEXT PRIMARY KEY, value TEXT)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS file_summaries (file_path TEXT PRIMARY KEY, summary TEXT)"
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Não foi possível inicializar a memória SQLite: {e}")

    def _load_db_state(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT key, value FROM key_findings")
                for key, value in cursor.fetchall():
                    try:
                        self.state.setdefault("key_findings", {})[key] = json.loads(value)
                    except Exception:
                        self.state.setdefault("key_findings", {})[key] = value

                cursor.execute("SELECT file_path, summary FROM file_summaries")
                for file_path, summary in cursor.fetchall():
                    self.state.setdefault("file_summaries", {})[file_path] = summary
        except Exception as e:
            logger.warning(f"Falha ao carregar estado da memória SQLite: {e}")

    def remember(self, key: str, value: Any, section: str = "key_findings") -> None:
        if section == "key_findings":
            self.state.setdefault("key_findings", {})[key] = value
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO key_findings (key, value) VALUES (?, ?)",
                        (key, json.dumps(value, ensure_ascii=False))
                    )
                    conn.commit()
            except Exception as e:
                logger.warning(f"Falha ao gravar key_findings em SQLite: {e}")
        elif section == "file_summaries":
            self.state.setdefault("file_summaries", {})[key] = value
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO file_summaries (file_path, summary) VALUES (?, ?)",
                        (key, str(value))
                    )
                    conn.commit()
            except Exception as e:
                logger.warning(f"Falha ao gravar file_summaries em SQLite: {e}")
        elif section in self.state and isinstance(self.state[section], dict):
            self.state[section][key] = value
        else:
            self.state[key] = value

    def forget(self, key: str, section: str = "key_findings") -> None:
        if section == "key_findings":
            self.state.get("key_findings", {}).pop(key, None)
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("DELETE FROM key_findings WHERE key = ?", (key,))
                    conn.commit()
            except Exception as e:
                logger.warning(f"Falha ao remover key_findings em SQLite: {e}")
        elif section == "file_summaries":
            self.state.get("file_summaries", {}).pop(key, None)
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("DELETE FROM file_summaries WHERE file_path = ?", (key,))
                    conn.commit()
            except Exception as e:
                logger.warning(f"Falha ao remover file_summaries em SQLite: {e}")
        else:
            self.state.pop(key, None)

    def clear(self) -> None:
        self.state.clear()
        self.state = {
            "project_map": {},
            "key_findings": {},
            "files_index": {},
            "todo": [],
            "notes": {},
            "analyzed_files": {},
            "file_summaries": {}
        }
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM key_findings")
                conn.execute("DELETE FROM file_summaries")
                conn.commit()
        except Exception as e:
            logger.warning(f"Falha ao limpar a memória SQLite: {e}")

    def backup_to_file(self, path: Optional[str] = None, max_backups: int = MAX_MEMORY_BACKUPS) -> None:
        """
        Cria uma cópia de segurança do arquivo de memória dentro da pasta MEMORY_BACKUP_DIR.
        Mantém apenas os últimos max_backups arquivos.
        """
        path = path or paths.MEMORY_FILE
        if not os.path.exists(path):
            return
        try:
            os.makedirs(MEMORY_BACKUP_DIR, exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = os.path.basename(path) + f".{timestamp}.bak"
            backup_path = os.path.join(MEMORY_BACKUP_DIR, backup_name)
            shutil.copy2(path, backup_path)

            all_backups = sorted(
                f for f in os.listdir(MEMORY_BACKUP_DIR)
                if f.startswith(os.path.basename(path)) and f.endswith(".bak")
            )
            while len(all_backups) > max_backups:
                oldest = all_backups.pop(0)
                os.remove(os.path.join(MEMORY_BACKUP_DIR, oldest))
        except Exception as e:
            logger.warning(f"Não foi possível criar backup da memória: {e}")

    def save_to_file(self, path: Optional[str] = None) -> str:
        path = path or paths.MEMORY_FILE
        self.backup_to_file(path)
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            # Evita duplicação desnecessária entre o JSON de backup e o
            # banco SQLite que já persiste `key_findings` e `file_summaries`.
            payload_state = {
                k: v for k, v in self.state.items()
                if k not in ("key_findings", "file_summaries")
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload_state, f, ensure_ascii=False, indent=2)
            return f"Memória salva em {path}."
        except Exception as e:
            return f"Erro ao salvar memória: {e}"

    def load_from_file(self, path: Optional[str] = None) -> str:
        path = path or paths.MEMORY_FILE
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for section in self.state:
                if section in loaded and section not in ("key_findings", "file_summaries"):
                    if isinstance(self.state[section], dict) and isinstance(loaded[section], dict):
                        self.state[section].update(loaded[section])
                    elif isinstance(self.state[section], list) and isinstance(loaded[section], list):
                        self.state[section].extend(loaded[section])
            return f"Memória carregada de {path}."
        except FileNotFoundError:
            return f"Arquivo {path} não encontrado."
        except Exception as e:
            return f"Erro ao carregar memória: {e}"

    def stringify(self) -> str:
        try:
            # Filtra apenas chaves que possuem algum valor (não vazias)
            active_state = {k: v for k, v in self.state.items() if v}
            if not active_state:
                return "{}"
            return json.dumps(active_state, ensure_ascii=False, indent=2, default=str)
        except Exception:
            return str(self.state)

    def get_context_for_prompt(self, objective: str = "", budget_tokens: int = 800) -> str:
        """
        Retorna um contexto de memória enxuto para ser injetado no system prompt.
        Nunca retorna a memória inteira - apenas o necessário para a tarefa atual.

        Estratégia:
        1. Inclui SEMPRE analyzed_files (índice leve, resumos de 150 chars cada).
        2. Se o orçamento permitir, inclui file_summaries APENAS dos arquivos
           mencionados no objetivo ou que tenham hash armazenado.
        3. NUNCA inclui file_hashes, timestamps, ou metadados internos.
        """
        parts: list[str] = []
        index_text, budget_used = self._analyzed_files_context(budget_tokens)
        if index_text:
            parts.append(index_text)
        summary_text = self._relevant_summaries_context(objective, budget_tokens - budget_used)
        if summary_text:
            parts.append(summary_text)
        return "\n\n".join(parts)

    def _analyzed_files_context(self, budget_tokens: int) -> tuple[str, int]:
        analyzed = self.state.get("analyzed_files", {})
        if not isinstance(analyzed, dict) or not analyzed:
            return "", 0
        lines = [f"- {path}: {summary}" for path, summary in list(analyzed.items())[:30]]
        text = "\n".join(lines)
        if len(text) // 4 > budget_tokens * 0.6:
            text = "\n".join(lines[:15])
        return f"--- ARQUIVOS JÁ ANALISADOS ---\n{text}", len(text) // 4

    def _relevant_summaries_context(self, objective: str, remaining_budget: int) -> str:
        if remaining_budget <= 100 or not objective:
            return ""
        import re

        mentioned = set(re.findall(r'[\w\-/]+\.\w+', objective))
        summaries = self.state.get("file_summaries", {})
        relevant = [
            (path, summary)
            for path, summary in summaries.items()
            if path in mentioned or path.split("/")[-1] in mentioned
        ]
        lines = [f"- {path}: {str(summary)[:300]}" for path, summary in relevant[:5]]
        text = "\n".join(lines)
        return f"--- RESUMOS DETALHADOS ---\n{text}" if text and len(text) // 4 <= remaining_budget else ""
