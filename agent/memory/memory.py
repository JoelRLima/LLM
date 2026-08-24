import json
import sqlite3 as sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from agent.memory.backup import copy_memory_backup
from agent.memory.json_persistence import (
    JsonObjectReadError,
    read_json_object,
    write_json_atomic,
)
from agent.memory.prompt_context import build_memory_prompt_context
from agent.memory.sqlite_store import (
    MemoryDatabaseError as MemoryDatabaseError,
)
from agent.memory.sqlite_store import (
    MemoryOperationCancelled as MemoryOperationCancelled,
)
from agent.memory.sqlite_store import (
    SqliteMemoryStoreMixin,
)
from agent.runtime import paths
from agent.runtime.logging import logger

MAX_MEMORY_BACKUPS = 5


class MemoryLoadError(RuntimeError):
    """Falha ao restaurar a parcela JSON da memória."""


class AgentMemory(SqliteMemoryStoreMixin):
    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        default_file: str | Path | None = None,
        backup_dir: str | Path | None = None,
    ) -> None:
        self.db_path = str(db_path or paths.MEMORY_DB_FILE)
        self.default_file = str(default_file or paths.MEMORY_FILE)
        self.backup_dir = str(backup_dir or paths.MEMORY_BACKUP_DIR)
        self._initialized = False
        self.state: Dict[str, Any] = self._empty_state()

    @staticmethod
    def _empty_state() -> Dict[str, Any]:
        return {
            "project_map": {},
            "key_findings": {},
            "files_index": {},
            "todo": [],
            "notes": {},
            "analyzed_files": {},   # { "caminho/arquivo.py": "resumo de uma linha" }
            "file_summaries": {},   # { "caminho/arquivo.py": "resumo detalhado" }
            "file_hashes": {},
            "file_cache_entries": {},
        }

    def remember(
        self,
        key: str,
        value: Any,
        section: str = "key_findings",
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> None:
        self.initialize(
            cancellation_token=cancellation_token,
            cancellation_event=cancellation_event,
        )
        if section == "key_findings":
            self._write_database(
                "persistir key_findings",
                ((
                    "INSERT OR REPLACE INTO key_findings (key, value) VALUES (?, ?)",
                    (key, json.dumps(value, ensure_ascii=False)),
                ),),
                cancellation_token=cancellation_token,
                cancellation_event=cancellation_event,
            )
            self.state.setdefault("key_findings", {})[key] = value
        elif section == "file_summaries":
            self._write_database(
                "persistir file_summaries",
                ((
                    "INSERT OR REPLACE INTO file_summaries (file_path, summary) VALUES (?, ?)",
                    (key, str(value)),
                ),),
                cancellation_token=cancellation_token,
                cancellation_event=cancellation_event,
            )
            self.state.setdefault("file_summaries", {})[key] = value
        elif section in self.state and isinstance(self.state[section], dict):
            self.state[section][key] = value
        else:
            self.state[key] = value

    def forget(
        self,
        key: str,
        section: str = "key_findings",
        *,
        cancellation_token: Any | None = None,
        cancellation_event: Any | None = None,
    ) -> None:
        self.initialize(
            cancellation_token=cancellation_token,
            cancellation_event=cancellation_event,
        )
        if section == "key_findings":
            self._write_database(
                "remover key_findings",
                (("DELETE FROM key_findings WHERE key = ?", (key,)),),
                cancellation_token=cancellation_token,
                cancellation_event=cancellation_event,
            )
            self.state.get("key_findings", {}).pop(key, None)
        elif section == "file_summaries":
            self._write_database(
                "remover file_summaries",
                (("DELETE FROM file_summaries WHERE file_path = ?", (key,)),),
                cancellation_token=cancellation_token,
                cancellation_event=cancellation_event,
            )
            self.state.get("file_summaries", {}).pop(key, None)
        else:
            self.state.pop(key, None)

    def clear(self) -> None:
        self.initialize()
        self._write_database(
            "limpar a memória",
            (
                ("DELETE FROM key_findings", ()),
                ("DELETE FROM file_summaries", ()),
            ),
        )
        self.state = self._empty_state()

    def backup_to_file(self, path: Optional[str] = None, max_backups: int = MAX_MEMORY_BACKUPS) -> None:
        """
        Cria uma cópia de segurança do arquivo de memória dentro da pasta MEMORY_BACKUP_DIR.
        Mantém apenas os últimos max_backups arquivos.
        """
        source = Path(path or self.default_file)
        try:
            copy_memory_backup(
                source,
                Path(self.backup_dir),
                max_backups=max_backups,
            )
        except Exception as e:
            logger.warning(f"Não foi possível criar backup da memória: {e}")

    def persist_to_file(self, path: str | Path | None = None) -> Path:
        """Persiste a memória ou propaga a falha ao chamador automático."""

        target = Path(path or self.default_file)
        self.backup_to_file(str(target))
        payload_state = {
            key: value
            for key, value in self.state.items()
            if key not in ("key_findings", "file_summaries")
        }
        write_json_atomic(target, payload_state)
        return target

    def save_to_file(self, path: str | Path | None = None) -> str:
        """Fachada amigável para comandos manuais de persistência."""

        try:
            target = self.persist_to_file(path)
            return f"Memória salva em {target}."
        except Exception as exc:
            return f"Erro ao salvar memória: {exc}"

    def restore_from_file(self, path: str | Path | None = None) -> Path | None:
        """Restaura JSON de forma fail-closed; ausência é um estado inicial válido."""

        self.initialize()
        target = Path(path or self.default_file)
        try:
            loaded = read_json_object(target, missing_ok=True)
        except JsonObjectReadError as exc:
            raise MemoryLoadError(str(exc)) from exc
        if loaded is None:
            return None
        staged = deepcopy(self.state)
        for section, incoming in loaded.items():
            if section in {"key_findings", "file_summaries"} or section not in staged:
                continue
            current = staged[section]
            if isinstance(current, dict) and isinstance(incoming, dict):
                current.update(incoming)
            elif isinstance(current, list) and isinstance(incoming, list):
                current.extend(incoming)
            else:
                raise MemoryLoadError(
                    f"Seção inválida em {target}: {section}"
                )
        self.state = staged
        return target

    def load_from_file(self, path: Optional[str] = None) -> str:
        """Fachada manual que traduz falhas para uma mensagem amigável."""

        try:
            loaded = self.restore_from_file(path)
            target = Path(path or self.default_file)
            if loaded is None:
                return f"Arquivo {target} não encontrado."
            return f"Memória carregada de {loaded}."
        except Exception as exc:
            return f"Erro ao carregar memória: {exc}"

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
        return str(build_memory_prompt_context(
            self.state,
            objective,
            budget_tokens,
        ))
