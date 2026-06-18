import json
from typing import Any, Dict

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
    
    def save_to_file(self, path: str = "agent_memory.json") -> str:
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
