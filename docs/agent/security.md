# Módulo `agent/` — security

> Parte da documentação técnica do projeto. Veja o [índice](../README.md).

---

## 4.33. [security_patterns.py](../../agent/security/security_patterns.py) 🆕
Banco de dados de padrões de segurança. NÃO contém lógica — apenas metadados.
* **`PATTERN_DATABASE`**: dicionário com 12 padrões (execução, desserialização, criptografia fraca, segredos, path traversal, injeção, misconfig).
* Cada padrão possui: `pattern_id`, `pattern`, `family`, `cwe`, `owasp`, `why_interesting`, `default_priority`.
* **`lookup(pattern_id) -> dict`**: retorna os metadados do padrão ou `{}` se não encontrado.

---

## 4.34. [security_scanner.py](../../agent/security/security_scanner.py) 🆕
Consolidador de fatos de segurança. NÃO usa LLM, NÃO executa ferramentas.
* **`Finding` (dataclass)**: `pattern_id`, `pattern`, `location`, `start_line`, `end_line`, `symbol`, `snippet` (máx 120 chars), `detection_method`, `metadata`.
* **`consolidate(code_analyzer_result, grep_results) -> List[Finding]`**: normaliza, trunca snippets, remove duplicatas e enriquece com metadados do `security_patterns.py`.
* Nenhuma inferência de severidade ou risco — apenas fatos.
* **`_TYPE_TO_PATTERN` unificado:** antes, este dicionário (símbolo → `pattern_id`) era mantido manualmente em sincronia com os conjuntos de símbolos declarados em `code_analyzer.py` (`agent/skills/code_analyzer.py`) — um símbolo novo adicionado só em um dos dois lugares caía silenciosamente em `"PY999"` (desconhecido), sem erro nem aviso. Agora é derivado em tempo de importação a partir de um único registro (`code_analyzer.SECURITY_SYMBOL_REGISTRY`), via `get_pattern_id_map()`.
* **Bug de import corrigido pela reorganização de pastas:** este módulo importa `get_pattern_id_map` de `code_analyzer.py`, que é uma **skill** (vive em `agent/skills/code_analyzer.py`), não um módulo central de `agent/`. O import `from agent.code_analyzer import ...` só passou a falhar de fato quando a estrutura de pastas real foi criada (antes, com tudo solto numa pasta só, o Python nunca chegava a resolver esse caminho da forma correta). Corrigido para `from agent.skills.code_analyzer import get_pattern_id_map`.
