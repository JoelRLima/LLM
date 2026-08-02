# Módulo `agent/` — memory

> Parte da documentação técnica do projeto. Veja o [índice](../README.md).

---

## 4.3. [memory.py](../../agent/memory/memory.py)
Implementa a classe `AgentMemory` para gerenciar informações persistentes e indexações de arquivos:
* **Estado de Memória:** Estruturado em seções como `project_map`, `key_findings` (lembretes manuais), `analyzed_files` (visão superficial dos arquivos lidos), `file_summaries` (resumos detalhados gerados por IA) e `file_hashes` (para validação de integridade de arquivos).
* **Lifecycle explícito:** O construtor não abre SQLite nem cria diretórios. `initialize()` prepara a persistência somente durante o bootstrap da aplicação.
* **Paths injetados:** `AgentApplication` fornece arquivo JSON, banco SQLite e diretório de backup vindos de `WorkspacePaths`. Cada workspace possui sua própria memória; constantes relativas permanecem apenas para consumidores legados.
* **Restauração estrita:** inicialização de SQLite e leitura automática do JSON
  falham de forma fechada. Dados são montados em staging e só substituem o
  estado em RAM após validação completa; arquivo ausente representa memória
  inicial vazia, enquanto corrupção impede o bootstrap e libera seus recursos.
  `load_from_file()` permanece a fachada amigável para comandos manuais.
* **Persistência fail-closed:** O snapshot JSON é gravado em arquivo temporário,
  sincronizado e promovido com `os.replace`. Falha de promoção preserva a versão
  anterior, remove o temporário e faz a tarefa terminar como falha sem apagar o
  checkpoint. Cada conexão SQLite é encerrada deterministicamente.
* **Fonte canônica de summaries:** `file_summaries` é persistido no SQLite por
  meio da API de memória e recarregado entre processos; ele não depende do
  snapshot JSON.
* **Backup de Memória:** Mantém cópias no diretório de dados do workspace. Esse histórico é distinto dos restore points usados por `workspace.py` para rollback de arquivos.
* **Injeção Dinâmica de Memória (`get_context_for_prompt`):** Evita inundar o prompt do modelo. Filtra os resumos com base nos arquivos explicitamente mencionados no objetivo do usuário e respeita um limite estrito de tokens.

---

## 4.20. [semantic_memory.py](../../agent/memory/semantic_memory.py) 🆕
Camada de busca semântica sobre a memória do agente. Usa o modelo `all-MiniLM-L6-v2` (via `sentence-transformers`) para gerar embeddings dos resumos de arquivos armazenados em `AgentMemory.state['file_summaries']`.
* **`SemanticMemory(memory, model_name)`**: Inicializa a camada com lazy loading do modelo.
* **`build_index()`**: Constrói o índice vetorial a partir dos resumos existentes.
* **`find_similar_files(query, top_k=5)`**: Retorna os arquivos mais relevantes semanticamente para uma consulta.
* **Integração**: Chamado por `ContextManager.get_file_hints()` para enriquecer o prompt com arquivos relacionados ao objetivo, mesmo quando o nome do arquivo não é mencionado literalmente.
