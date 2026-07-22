# Estrutura de diretórios

Esta é a árvore lógica atual. Arquivos de cache, ambiente virtual e artifacts
gerados foram omitidos.

```text
LLM/
├── agent/
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
│   │   ├── policy.py               # confiança e confirmação de proposta
│   │   ├── task_templates.py       # grafos determinísticos de código
│   │   ├── validation.py           # perfis e agregação de validação
│   │   ├── validation_process.py   # subprocesso limitado/cancelável
│   │   ├── workflow_proposal.py    # proposta estruturada via modelo
│   │   ├── workflow_application.py # aprovação, commit e rollback
│   │   └── workflows.py            # fachada dos casos de uso
│   ├── evaluation/                 # cenários e oráculos herméticos
│   ├── interfaces/
│   │   └── cli/                     # app, comandos, handlers e apresentação
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
│   ├── memory/                     # memória persistente e opcional semântica
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
│   ├── health/                     # checks de estado, runtime e relatório
│   ├── runtime/                    # contexto, config, logging, paths e perfis
│   ├── security/                   # scanner e padrões
│   ├── skills/
│   │   ├── catalog.py               # fonte canônica dos descritores
│   │   ├── descriptor.py            # SkillSpec e capacidades
│   │   ├── registry.py              # construção e validação
│   │   ├── policy.py                # capacidades por persona
│   │   ├── code_task.py             # fachada dos workflows novos
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
│   └── clean_runtime.py             # limpeza dry-run e arquivo de estado antigo
├── runtime/                         # artifacts gerados em execução
├── tests/
│   ├── fixtures/capabilities/       # cenários de capacidade
│   ├── fixtures/regression/         # planos de regressão
│   ├── unit/
│   │   ├── code/
│   │   ├── llm/
│   │   ├── planning/
│   │   ├── runtime/
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
- artefatos produzidos em execução pertencem a `runtime/` e não à raiz.

`.temp_analysis/` é uma exceção intencional: representa o workspace temporário
das skills antigas no projeto que está sendo analisado. Já `runtime/` guarda
estado do próprio agente, como logs, memória, checkpoints, métricas, relatórios
e pontos de restauração.

## Fontes de verdade

| Assunto | Fonte |
| :--- | :--- |
| provider/modelo | `agent/llm/contracts.py` e `agent/llm/providers/` |
| hardware e limites | `agent/runtime/hardware.py` e `agent/runtime/config.py` |
| skills | `agent/skills/catalog.py` |
| capacidades por persona | `agent/skills/policy.py` |
| contratos de código | `agent/code/contracts.py` |
| tarefas e dependências | `agent/planning/task_graph.py` |
| caminhos de runtime | `agent/runtime/paths.py` |
| interfaces de terminal | `agent/interfaces/cli/` |
| compatibilidade temporária | `docs/legado.md` |
| padrões de contribuição | `CONTRIBUTING.md` |
| gates de qualidade | `scripts/check_quality.py`, `quality/baseline.json`, `pyproject.toml` e `.github/workflows/` |
