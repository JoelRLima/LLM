# Módulo `agent/` — llm

> Parte da documentação técnica do projeto. Veja o [índice](../README.md).

## Estado atual

O transporte foi movido para `agent/llm/providers/`. `contracts.py` define
`ModelGateway`, requests/responses, stream, uso, erros e capacidades.
`structured_output.py` escolhe JSON Schema, GBNF ou JSON por prompt. O adapter
`openai_compatible.py` é o único lugar que conhece HTTP, `choices`, SSE,
`chat_template_kwargs`, `grammar` e `/tokenize`.

`session.py` é histórico e fachada de compatibilidade. `ModelClient` permanece
para o fluxo de planejamento legado; workflows de código usam `ModelGateway`
diretamente. Consulte [Modelos e providers](../modelos-providers.md).

---

## 4.5. [prompts.py](../../agent/llm/prompts.py)
Armazena a constante de prompt de sistema global do agente (`AGENT_SYSTEM_PROMPT`) que instrui o LLM sobre:
* A obrigatoriedade de planejar passos de forma estruturada.
* O formato estrito de saída em JSON.
* A necessidade de consultar informações e ler arquivos usando ferramentas adequadas em vez de deduzir seus conteúdos.
* Regras para o uso de memória de sessão.
* **Personas centralizadas**: Todas as personas (`CODER_PROMPT`, `RESEARCHER_PROMPT`, `GENERAL_PROMPT`, `SECURITY_AUDITOR_PROMPT`) são definidas como constantes neste módulo, permitindo manutenção centralizada.

---

## 4.6. [context_manager.py](../../agent/llm/context_manager.py)
Administra a janela de contexto e prepara prompts. O transporte pertence ao
`ModelGateway`; `ContextManager` não importa `requests` nem deriva endpoints:
* **Contexto do Projeto:** Constrói um sumário dos arquivos presentes no repositório listando arquivos rastreados via `git ls-files` ou scaneando o diretório raiz.
* **Compressão de Diálogo (`maybe_compress_context`):** Monitora a janela de tokens. Se o histórico estimado de conversas ultrapassar o limiar de compressão (80% do limite de 8192 tokens), o sistema gera um resumo condensado da conversa via chamada de modelo externa e limpa as mensagens intermediárias, mantendo o resumo no topo.
* **Compactação de Leituras (`build_compact_view`):** Quando o histórico atinge limites elevados, localiza leituras de arquivos passadas e as substitui por seus resumos técnicos extraídos da memória, poupando espaço útil no prompt.
* **Mapeamento de Linhas (`get_file_hints`):** Busca menções a arquivos no objetivo do usuário para expor o total de linhas de cada arquivo, ajudando o modelo a decidir a paginação de leitura.
* **Comunicação com o Modelo (`ask_model`):** Prepara o contexto completo e usa a fachada legada `ModelClient`, que por sua vez delega transporte à `ChatSession`/gateway.
* **Contagem de tokens:** usa `gateway.count_tokens()` quando o provider oferece a capacidade e faz estimativa local quando não oferece.
* **Perfil de hardware:** janela e orçamento de saída vêm de `agent/runtime/hardware.py`; `low_vram_8gb` usa 8192/2048 tokens.
* **Seleção automática de gramática:** O método `ask_model` aceita um parâmetro `grammar` que, por padrão (`AUTO_GRAMMAR`), seleciona automaticamente a gramática GBNF apropriada com base no `step_type`. Pode ser sobrescrito com uma string explícita ou desabilitado com `None`.

---

## 4.14. [router.py](../../agent/llm/router.py)
Executa a triagem inteligente de prompts e ferramentas:
* Identifica se uma solicitação de usuário é meramente trivial (saudações como "olá" ou "quem é você") para atribuir a persona `general` e evitar consumo de plano.
* Utiliza busca de palavras-chave para detectar listagens estritas (`general`), tarefas de código (`coder`) ou pesquisas web (`researcher`).
* Se houver ambiguidade, submete o objetivo ao LLM sob o prompt `ROUTER_PROMPT` para obter a persona final em formato JSON.
* Cada persona injeta regras de comportamento e recebe skills cuja coleção de capacidades é autorizada por `agent/skills/policy.py`; as listas não são mantidas manualmente no roteador.
* **Nova persona `security_auditor`**: Detectada por palavras-chave (segurança, vulnerabilidade, auditoria, etc.) e também disponível via LLM Router. Utiliza ferramentas de leitura/análise sem `file_writer`.
* **`SECURITY_KEYWORDS` e `is_security_objective()`:** fonte canônica única para detectar se um objetivo é de segurança, reunindo as keywords que antes estavam duplicadas (e levemente dessincronizadas) em `orchestrator.py` e `final_response.py`. Ambos os módulos agora importam e delegam para esta função.

---

## 4.17. [model_client.py](../../agent/llm/model_client.py) 🆕
Fachada legada para decisões estruturadas do planejador. A comunicação HTTP
pertence atualmente ao adapter de provider:
* **`request(session, payload, step_type, log_metric_callback, verbose) -> dict`:** Envia uma requisição ao modelo, processa a resposta (incluindo retry com mais tokens em caso de truncamento), coleta métricas (timestamp, step_type, tool, budget, tokens, duração, sucesso) e retorna a decisão parseada.
* **Fallback de tokens:** O teto histórico é 4096, mas o valor efetivo é limitado pelo perfil de hardware (2048 em `low_vram_8gb`), salvo configuração explícita de `agent_max_tokens`.
* **Separação de responsabilidades:** `ContextManager` não importa `requests` e
  delega contagem de tokens ao gateway.
* **Suporte a GBNF:** O método `request` aceita um parâmetro opcional `grammar`. Se fornecido e o backend suportar, o campo `"grammar"` é incluído no payload. Um fallback automático detecta backends incompatíveis (erro 400 com "grammar") e desabilita a funcionalidade para a sessão, com cache (`_backend_supports_grammar`) para evitar novas tentativas.

---

## 4.29. [grammars.py](../../agent/llm/grammars.py) 🆕
Infraestrutura de suporte a gramáticas GBNF (GGML BNF) para forçar o LLM a gerar JSON estruturalmente válido.
* **Gramáticas por `step_type`**: define strings GBNF para `plan`, `macro_plan`, `tool_decision`, `final`, `summarize` e `replan`.
* **Sentinela `AUTO_GRAMMAR`**: indica que a gramática deve ser escolhida automaticamente com base no `step_type`.
* **`get_grammar(step_type) -> str | None`**: retorna a gramática apropriada, respeitando a flag `ENABLE_GBNF` de `config.py`.
* **Integração**: usado por `ContextManager.ask_model()` para injetar o campo `grammar` no payload automaticamente. A validação semântica permanece como responsabilidade do `PlanValidator`.
