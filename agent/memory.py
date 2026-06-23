import datetime
import json
import os
import shutil
from typing import Any, Dict

from logger import logger

MAX_MEMORY_BACKUPS = 5
MEMORY_BACKUP_DIR = "memory_backups"

class AgentMemory:
    def __init__(self):
        self.state: Dict[str, Any] = {
            "project_map": {},
            "key_findings": {},
            "files_index": {},
            "todo": [],
            "notes": {},
            "analyzed_files": {},   # { "caminho/arquivo.py": "resumo de uma linha" }
            "file_summaries": {}    # { "caminho/arquivo.py": "resumo detalhado" }
        }

    def remember(self, key: str, value: Any, section: str = "key_findings") -> None:
        if section in self.state and isinstance(self.state[section], dict):
            self.state[section][key] = value
        else:
            self.state[key] = value

    def forget(self, key: str) -> None:
        self.state.pop(key, None)

    def clear(self) -> None:
        self.state.clear()
        # Restaura a estrutura básica
        self.state = {
            "project_map": {},
            "key_findings": {},
            "files_index": {},
            "todo": [],
            "notes": {},
            "analyzed_files": {},
            "file_summaries": {}
        }

    def backup_to_file(self, path: str = "agent_memory.json", max_backups: int = MAX_MEMORY_BACKUPS) -> None:
        """
        Cria uma cópia de segurança do arquivo de memória dentro da pasta MEMORY_BACKUP_DIR.
        Mantém apenas os últimos max_backups arquivos.
        """
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

    def save_to_file(self, path: str = "agent_memory.json") -> str:
        self.backup_to_file(path)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
            return f"Memória salva em {path}."
        except Exception as e:
            return f"Erro ao salvar memória: {e}"

    def load_from_file(self, path: str = "agent_memory.json") -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for section in self.state:
                if section in loaded:
                    if isinstance(self.state[section], dict):
                        self.state[section].update(loaded[section])
                    elif isinstance(self.state[section], list):
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
        parts = []
        budget_used = 0

        # Camada 1: Índice leve (sempre incluído, truncado se necessário)
        analyzed = self.state.get("analyzed_files", {})
        if analyzed:
            index_lines = []
            for fpath, summary in list(analyzed.items())[:30]:  # no máximo 30 arquivos
                line = f"- {fpath}: {summary}"
                index_lines.append(line)
            index_text = "\n".join(index_lines)
            # Estima tokens (1 token ≈ 4 chars)
            estimated = len(index_text) // 4
            if estimated > budget_tokens * 0.6:  # no máximo 60% do orçamento
                index_text = "\n".join(index_lines[:15])  # trunca para 15 arquivos
                estimated = len(index_text) // 4
            parts.append(f"--- ARQUIVOS JÁ ANALISADOS ---\n{index_text}")
            budget_used += estimated

        # Camada 2: Resumos detalhados APENAS dos arquivos relevantes
        remaining_budget = budget_tokens - budget_used
        if remaining_budget > 100 and objective:
            # Extrai menções a arquivos no objetivo
            import re
            mentioned = set(re.findall(r'[\w\-/]+\.\w+', objective))
            summaries = self.state.get("file_summaries", {})
            relevant = []
            for fpath, summary in summaries.items():
                fname = fpath.split("/")[-1] if "/" in fpath else fpath
                if fname in mentioned or fpath in mentioned:
                    relevant.append((fpath, summary))

            if relevant:
                summary_lines = []
                for fpath, summary in relevant[:5]:  # no máximo 5 resumos detalhados
                    truncated = summary[:300]  # cada resumo limitado a 300 chars
                    summary_lines.append(f"- {fpath}: {truncated}")
                summary_text = "\n".join(summary_lines)
                estimated = len(summary_text) // 4
                if estimated <= remaining_budget:
                    parts.append(f"--- RESUMOS DETALHADOS ---\n{summary_text}")

        if not parts:
            return ""

        return "\n\n".join(parts)
