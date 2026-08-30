# Compatibilidade e retirada de legado

> **STATUS: CURRENT — DEPRECATION INVENTORY.** Este arquivo é atualizado quando
> aliases entram ou saem; não é um relato histórico de milestone.

Este inventário impede que fachadas temporárias se tornem arquitetura
permanente. Código novo deve importar somente o caminho canônico. O gate
arquitetural rejeita imports dos aliases da raiz dentro de `agent/`.

| Compatibilidade | Caminho canônico | Consumidor restante | Condição de retirada |
| :--- | :--- | :--- | :--- |
| `cli.py` | `agent.interfaces.cli.app` | scripts e uso manual antigo | instalação pelo comando `llm-agent` adotada |
| `commands.py`, `command_*.py`, `cli_*.py` | `agent.interfaces.cli/` | imports externos antigos | nenhuma integração externa conhecida depender da raiz |
| `config.py`, `config_validation.py` | `agent.runtime.config*` | configurações e testes de terceiros | ciclo de migração anunciado antes da versão 1.0 |
| `logger.py`, `paths.py` | `agent.runtime.logging`, `agent.runtime.paths` | extensões antigas | extensões usarem as portas canônicas |
| `session.py` | `agent.llm.session` | integrações antigas | consumidores usarem `ModelGateway` ou a sessão canônica |
| `benchmark.py` | `scripts.benchmark` | comando manual documentado | documentação usar somente o módulo |
| `ModelClient` | `ModelGateway` + `structured_output` | integrações externas, testes e planos antigos | janela pública de compatibilidade encerrada e consumidores externos migrados |
| `AutoCoder` | `agent.code`, `ChangeSetTransaction` e `code_task` | gerador de conteúdo legado e callers explícitos de correção | toda persistência passar pelo owner transacional canônico, com janela pública encerrada |
| alias `git` | skill `git_reader` | planos persistidos antigos | checkpoints incompatíveis anteriores deixarem de ser suportados |

## Disposições do Block 6

| Candidato | Disposição | Evidência e condição de fechamento |
| :--- | :--- | :--- |
| `ModelClient` e `ModelProviderError` reexportado | `RETAIN_COMPAT` | símbolos públicos antigos continuam disponíveis; `ModelClient` só traduz payload e delega parsing, fallback, retry e métricas ao boundary canônico; retirar quando consumidores externos migrarem |
| `LegacyPayloadGateway`, `ChatSession.build_payload`, `build_legacy_request` e `send_non_streaming_request` | `RETAIN_COMPAT` | CLI, integrações externas e adapters antigos ainda dependem das fachadas; essas superfícies traduzem para `ModelRequest`/`ModelResponse` canônicos |
| `ChatSession.send_request(stream=False)` | `DEFER_WITH_EVIDENCE` | contrato público retorna o objeto raw de `gateway.send_payload`; a boundary usa a mesma `TaskBudgetLedger`, contabiliza uma tentativa e registra falhas; retirar após migração dos consumidores públicos |
| `PendingStream`, `send_request(stream=True)` e `ChatSession.process_stream` | `DEFER_WITH_EVIDENCE` | contrato público de stream legado em duas fases ainda exige envelope raw e consumo posterior; retirar após migração da CLI e callers públicos |
| `AutoCoder` e correção direta | `DEFER_WITH_EVIDENCE` | o seam legado `_save_code` converte a correção em `ChangeSet`/`FileChange`, usa `ChangeSetTransaction` com raiz do workspace e `base_hash`, e registra a transação quando possível; novos writes model-actionable usam `code_task`/`ChangeSet` |
| `file_writer` de baixo nível | `RETAIN_COMPAT` | excluído do conjunto model-actionable; prepara no scratch e faz o commit final por `ChangeSetTransaction` para administração/callers explícitos, mantendo aprovação e limites de workspace |
| arestas implícitas `file_writer` → `file_reader`/`code_analyzer` no grafo/optimizer | `REMOVE` | removidas de `dependency_map` e `PlanOptimizer`; dependências causais exigem `ResultBinding` explícito |
| `check_inverted_dependencies` do `PlanValidator` | `RETAIN_COMPAT` | política de segurança que rejeita leitura anterior a uma escrita declarada; não cria aresta de execução nem substitui `ResultBinding` |
| reordenação de leituras por caminho no `PlanOptimizer` | `REMOVE` | removida; o optimizer mantém a ordem declarada e só deduplica operações cacheáveis sem bindings |
| alias de skill `git` | `RETAIN_COMPAT` | alias explícito para `git_reader`; retenção até expirar o suporte a planos/checkpoints históricos |
| `CheckpointManager` e schema de checkpoint | `RETAIN_COMPAT` | schema v2, bindings, cursor e estados terminais continuam públicos; não há novo formato introduzido |
| `compress_conversation` / `maybe_compress_context` | `DEFER_WITH_EVIDENCE` | cadeia viva de memória/contexto; usa request canônico sem alterar a semântica de resumo não confiável |
| `model_metadata` histórico e leitores de métricas | `RETAIN_COMPAT` | writer autoritativo é `model_call`; leitores históricos continuam necessários sem dupla contagem |
| `LegacyToolInvoker` e ponte de compatibilidade de invocação | `RETAIN_COMPAT` | reservado a callers administrativos/legados; standalone não o instala no caminho model-actionable |

## Regras de migração

1. Não adicione funcionalidade nova a uma fachada.
2. Migre primeiro consumidores internos e mantenha teste de compatibilidade.
3. Registre quebra pública no changelog antes da retirada.
4. Remova fachada, teste e linha deste inventário no mesmo PR.
5. Não mantenha duas implementações: aliases apenas encaminham ao módulo
   canônico.
