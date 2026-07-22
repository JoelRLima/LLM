# Módulo `agent/` — memory

> Parte da documentação técnica do projeto. Veja o [índice](../README.md).

---

## 4.3. [memory.py](../../agent/memory/memory.py)
Implementa a classe `AgentMemory` para gerenciar informações persistentes e indexações de arquivos:
* **Estado de Memória:** Estruturado em seções como `project_map`, `key_findings` (lembretes manuais), `analyzed_files` (visão superficial dos arquivos lidos), `file_summaries` (resumos detalhados gerados por IA) e `file_hashes` (para validação de integridade de arquivos).
* **Backup de Memória:** Mantém um histórico das últimas cópias na pasta `runtime/memory_backups/` toda vez que salva o estado em `runtime/agent_memory.json` (caminhos centralizados em `paths.py`). Esta pasta é distinta de `runtime/restore_points/` (usada por `workspace.py` para rollback de arquivos) — antes da reorganização, os dois conceitos compartilhavam por coincidência o mesmo nome de diretório.
* **Injeção Dinâmica de Memória (`get_context_for_prompt`):** Evita inundar o prompt do modelo. Filtra os resumos com base nos arquivos explicitamente mencionados no objetivo do usuário e respeita um limite estrito de tokens.

---

## 4.20. [semantic_memory.py](../../agent/memory/semantic_memory.py) 🆕
Camada de busca semântica sobre a memória do agente. Usa o modelo `all-MiniLM-L6-v2` (via `sentence-transformers`) para gerar embeddings dos resumos de arquivos armazenados em `AgentMemory.state['file_summaries']`.
* **`SemanticMemory(memory, model_name)`**: Inicializa a camada com lazy loading do modelo.
* **`build_index()`**: Constrói o índice vetorial a partir dos resumos existentes.
* **`find_similar_files(query, top_k=5)`**: Retorna os arquivos mais relevantes semanticamente para uma consulta.
* **Integração**: Chamado por `ContextManager.get_file_hints()` para enriquecer o prompt com arquivos relacionados ao objetivo, mesmo quando o nome do arquivo não é mencionado literalmente.
