# Módulo `agent/` — security

> Parte da documentação técnica do projeto. Veja o [índice](../README.md).

---

## 4.33. [security_patterns.py](../../agent/security/security_patterns.py) 🆕
Banco de dados de padrões de segurança. NÃO contém lógica — apenas metadados.
* **`PATTERN_DATABASE`**: dicionário com 12 padrões (execução, desserialização, criptografia fraca, segredos, path traversal, injeção, misconfig).
* As chaves `PY001`–`PY012` são os `pattern_id`; os valores possuem `pattern`, `family`, `cwe`, `owasp`, `why_interesting` e `default_priority`.
* **`lookup(pattern_id) -> dict`**: retorna os metadados do padrão ou `{}` se não encontrado.

---

## 4.34. [security_scanner.py](../../agent/security/security_scanner.py) 🆕
Consolidador de fatos de segurança. NÃO usa LLM, NÃO executa ferramentas.
* **`Finding` (dataclass)**: `pattern_id`, `pattern`, `location`, `start_line`, `end_line`, `symbol`, `snippet` (máx 120 chars), `detection_method`, `metadata`.
* **`consolidate(code_analyzer_result, grep_results) -> List[Finding]`**: normaliza, trunca snippets, remove duplicatas e enriquece com metadados do `security_patterns.py`.
* Nenhuma inferência de severidade ou risco — apenas fatos.
* Este módulo normaliza findings já produzidos por skills internas; não é um
  detector científico completo nem o `ScannerCore` do TCC externo.
* A fronteira é deliberada: os helpers internos do Agent podem preparar e
  consolidar evidências, enquanto um futuro `ScannerCore` externo permanece
  outro projeto e não é uma dependência deste repositório.
* **`_TYPE_TO_PATTERN` unificado:** este dicionário (símbolo → `pattern_id`) é derivado em tempo de importação do registro canônico `agent.skills.security_symbols.SECURITY_SYMBOL_REGISTRY`, via `get_pattern_id_map()`. `code_analyzer.py` apenas o reexporta para compatibilidade.
* `security_scanner.py` importa `get_pattern_id_map` diretamente de `agent.skills.security_symbols`. As chaves `PY001`–`PY012` são IDs de findings; os diagnósticos AST `PYSEC001`–`PYSEC003` são códigos distintos.
