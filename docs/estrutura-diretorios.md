# Estrutura de diretórios

> **STATUS: CURRENT — LAYOUT REFERENCE.** Ownership conceitual está no
> [índice técnico](README.md).

Esta é a árvore lógica atual. Arquivos de cache, ambiente virtual e artifacts
gerados foram omitidos.

```text
LLM/
├── agent/
│   ├── application.py              # composição/lifecycle independente de UI
│   ├── approval.py                 # porta e decisões de consentimento
│   ├── code/                       # domínio de engenharia de código
│   │   ├── languages/              # adapters Python e textual genérico
│   │   ├── changes.py              # fachada pública do ChangeSet
│   │   ├── change_models.py        # modelos imutáveis de mudança
│   │   ├── change_parsing.py       # parsing e validação de payload
│   │   ├── change_transaction.py   # staging, commit e rollback
│   │   ├── application.py          # entrada única para CLI e skill
│   │   ├── commands.py             # parser puro de /code
│   │   ├── context_selection.py    # contexto por target/símbolo/import
│   │   ├── contracts.py            # perfil, análise e diagnósticos
│   │   ├── diagnostics.py          # classificação determinística de falhas
│   │   ├── discovery.py            # descoberta do projeto
│   │   ├── intelligence.py         # análise e índice incremental
│   │   ├── multitask.py            # workflows em TaskGraph
│   │   ├── path_safety.py          # confinamento canônico do domínio
│   │   ├── policy.py               # confiança e confirmação de proposta
│   │   ├── task_templates.py       # grafos determinísticos de código
│   │   ├── validation.py           # perfis e agregação de validação
│   │   ├── validation_python.py    # comandos Python embutidos
│   │   ├── validation_process.py   # subprocesso limitado/cancelável
│   │   ├── workflow_proposal.py    # proposta estruturada via modelo
│   │   ├── workflow_application.py # aprovação, commit e rollback
│   │   └── workflows.py            # fachada dos casos de uso
│   ├── evaluation/                 # cenários e oráculos herméticos
│   ├── interfaces/
│   │   └── cli/
│   │       ├── app.py              # parsing e dispatch
│   │       ├── bootstrap.py        # composição chat/headless
│   │       ├── approval.py         # consentimento interativo
│   │       ├── maintenance.py      # doctor e configuração/estado
│   │       └── ...                 # comandos, handlers e apresentação
│   ├── llm/
│   │   ├── providers/              # adapters de protocolo/modelo
│   │   ├── contracts.py            # ModelGateway e contratos normalizados
│   │   ├── context_manager.py
│   │   ├── grammars.py
│   │   ├── model_client.py          # compatibilidade legada
│   │   ├── prompts.py
│   │   ├── router.py
│   │   ├── session.py              # histórico e gateway da sessão
│   │   └── structured_output.py
│   ├── memory/
│   │   ├── memory.py               # estado persistente e SQLite
│   │   ├── json_persistence.py     # promoção JSON atômica
│   │   ├── prompt_context.py       # projeção enxuta para prompts
│   │   └── semantic_memory.py      # camada semântica opcional
│   ├── orchestration/              # ciclo da tarefa e composição do facade
│   ├── planning/
│   │   ├── execution_gateway.py
│   │   ├── plan_builder.py
│   │   ├── plan_executor.py
│   │   ├── plan_optimizer.py
│   │   ├── plan_validator.py
│   │   ├── step_executor.py
│   │   ├── step_contracts.py       # portas e resultados de execução
│   │   ├── step_policies.py        # schema, cache e pós-processamento
│   │   ├── hierarchical_planner.py
│   │   ├── hierarchical_executor.py
│   │   ├── task_graph.py            # DAG, recursos, estados e checkpoint
│   │   ├── task_graph_validation.py # invariantes e detecção de ciclos
│   │   ├── task_scheduler.py        # concorrência limitada
│   │   └── ...                      # fallback, replan, complexidade, metadata
│   ├── reporting/                  # builders separados de renderização
│   ├── health/
│   │   ├── standalone.py           # composição/renderização do doctor
│   │   ├── standalone_checks.py    # checks sem bootstrap do agente
│   │   └── state_integrity.py      # integridade read-only da memória
│   ├── resources/                  # defaults empacotados no wheel
│   ├── runtime/
│   │   ├── config_effective.py     # overrides no perfil selecionado
│   │   ├── config_repository.py    # init/load/migrate versionados
│   │   ├── config_schema.py        # schema estrito da configuração
│   │   ├── paths.py                # AppPaths e WorkspacePaths
│   │   ├── workspace_context.py    # raiz explícita e identidade estável
│   │   ├── state_migration.py      # migração conservadora do legado
│   │   ├── instance_lock.py        # exclusão por workspace
│   │   └── ...                     # contexto, logging e perfis
│   ├── security/                   # scanner e padrões
│   ├── skills/
│   │   ├── catalog.py               # fonte canônica dos descritores
│   │   ├── descriptor.py            # SkillSpec e capacidades
│   │   ├── registry.py              # construção e validação
│   │   ├── policy.py                # capacidades por persona
│   │   ├── code_task.py             # fachada dos workflows novos
│   │   ├── process_environment.py   # ambiente reduzido de subprocessos
│   │   ├── process_paths.py         # parsing e confinamento de argumentos
│   │   ├── process_safety.py        # allowlist e efeitos de comandos
│   │   └── ...                      # skills locais existentes
│   ├── checkpoint_manager.py
│   ├── contracts.py
│   ├── execution_state.py
│   ├── orchestrator.py
│   ├── state.py
│   ├── tool_executor.py
│   └── workspace.py
├── docs/                            # documentação técnica por domínio
├── quality/
│   └── baseline.json                # limites globais; listas de exceção vazias
├── scripts/
│   ├── benchmark.py                 # benchmark com backend real
│   ├── check_quality.py             # limites, arquitetura, links e encoding
│   ├── clean_runtime.py             # limpeza dry-run do estado antigo
│   └── verify_installed_package.py  # aceitação black-box do wheel
├── tests/
│   ├── fixtures/capabilities/       # cenários de capacidade
│   ├── fixtures/journeys/           # jornadas executáveis da plataforma
│   ├── fixtures/regression/         # planos de regressão
│   ├── unit/
│   │   ├── code/
│   │   ├── interfaces/
│   │   ├── llm/
│   │   ├── orchestration/
│   │   ├── planning/
│   │   ├── runtime/
│   │   ├── scripts/
│   │   └── skills/
│   ├── integration/                # composição e capacidades ponta a ponta
│   ├── policy/                     # gates do próprio repositório
│   ├── regression/
│   └── ...                          # testes agrupados por responsabilidade
├── .github/workflows/               # gates de CI
├── benchmark.py                     # entry point compatível
├── cli.py                           # entry point compatível
├── commands.py                     # alias compatível
├── config.py                       # alias compatível
├── config.example.json
├── paths.py                        # alias compatível
├── session.py                      # alias compatível
├── pyproject.toml
├── requirements-core.txt
├── requirements-ml.txt
├── requirements-dev.txt
├── requirements.txt
├── requirements.lock               # ambiente completo congelado
├── CONTRIBUTING.md                  # padrões permanentes de contribuição
├── README.md
└── EstruturaProjeto.md
```

## Regras de localização

- lógica de protocolo externo pertence a `agent/llm/providers/`;
- regras de código pertencem a `agent/code/`, não às skills;
- a skill valida/adapta argumentos e delega ao domínio;
- coordenação de dependências pertence a `agent/planning/`;
- configuração efetiva, cancelamento, eventos e limites pertencem a
  `agent/runtime/`;
- configuração, dados, estado, cache e logs pertencem aos paths da aplicação;
- memória, checkpoint, scratch e artifacts são particionados pelo workspace.

`runtime/` e `.temp_analysis/` na raiz são localizações legadas. Código novo
usa `AppPaths`/`WorkspacePaths`; o scratch fica no cache da aplicação. Dados
legados só são importados com migração explícita.

## Fontes de verdade

| Assunto | Fonte |
| :--- | :--- |
| provider/modelo | `agent/llm/contracts.py` e `agent/llm/providers/` |
| composição e lifecycle | `agent/application.py` |
| consentimento local | `agent/approval.py` e adapters em `agent/interfaces/cli/` |
| configuração versionada | `agent/runtime/config_repository.py`, `agent/runtime/config_schema.py`, `agent/runtime/config_effective.py` e `agent/resources/default_config.json` |
| hardware e limites | `agent/runtime/hardware.py` e configuração validada |
| skills | `agent/skills/catalog.py` |
| capacidades por persona | `agent/skills/policy.py` |
| contratos de código | `agent/code/contracts.py` |
| tarefas e dependências | `agent/planning/task_graph.py` |
| paths e workspace | `agent/runtime/paths.py` e `agent/runtime/workspace_context.py` |
| diagnóstico standalone | `agent/health/standalone.py` |
| persistência JSON de memória | `agent/memory/json_persistence.py` |
| interfaces de terminal | `agent/interfaces/cli/` |
| compatibilidade temporária | `docs/legado.md` |
| padrões de contribuição | `CONTRIBUTING.md` |
| gates de qualidade | `scripts/check_quality.py`, `quality/baseline.json`, `pyproject.toml` e `.github/workflows/` |
