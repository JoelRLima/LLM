# Fachadas da raiz e pontos canônicos

A raiz mantém somente entry points, aliases de compatibilidade, configuração
de empacotamento e documentação. Implementações novas pertencem ao pacote
`agent/`; consulte também o [inventário de legado](legado.md).

## `cli.py`

Fachada executável de `agent.interfaces.cli.app`. O módulo canônico carrega a configuração, cria `ChatSession`,
registra as skills pelo catálogo, injeta `ModelGateway` e configuração nas
skills que precisam deles, conecta o `Orchestrator` e inicia o loop interativo.

## `commands.py`

Alias de `agent.interfaces.cli.commands`. O módulo canônico implementa os comandos da CLI, incluindo configuração do prompt e thinking,
histórico, modo agente, debug, memória, atalhos de leitura/busca, diagnóstico e
retomada de checkpoint. `/code` usa parser e camada de aplicação determinísticos,
sem router/planner, e mostra diff/confiança antes de pedir aprovação quando
necessário. Operações persistentes usam os caminhos de `paths.py`.

## `session.py`

Alias de `agent.llm.session`, que mantém mensagens, configuração efetiva e compatibilidade com consumidores
legados. Na construção, resolve um perfil e cria um `ModelGateway`. Os métodos
`build_payload`, `send_request`, `send_non_streaming_request` e
`process_stream` permanecem disponíveis para CLI e planejador antigo, mas
delegam payload, transporte e SSE ao adapter de provider.

Código novo deve depender de `agent.llm.contracts.ModelGateway`, e não de
`ChatSession` ou de objetos `requests.Response`.

## `config.py`, `agent/runtime/config.py` e `config.example.json`

`config.py` é um alias. O `carregar_config()` canônico valida tipos, intervalos e fallbacks seguros. A configuração
atual aceita perfis de modelo e preserva as chaves legadas.

### Modelo e hardware

| Chave | Tipo | Default | Função |
| :--- | :--- | :--- | :--- |
| `hardware_profile` | string | `low_vram_8gb` | Seleciona limites base definidos em `agent/runtime/hardware.py`. |
| `max_model_concurrency` | inteiro positivo | `1` | Gate compartilhado de chamadas ao modelo. |
| `max_io_concurrency` | inteiro positivo | `2` | Lote de leituras e scheduler local. |
| `max_process_concurrency` | inteiro positivo | `1` | Limite previsto para validadores. |
| `max_model_calls` | inteiro positivo | `20` | Orçamento compartilhado de chamadas ao modelo por tarefa. |
| `default_model_profile` | string | ausente/legado | Nome da entrada selecionada em `model_profiles`. |
| `model_profiles` | objeto | `{}`/legado | Perfis de provider, endpoint, modelo, limites e capacidades. |

Se `default_model_profile` não existir ou não apontar para um objeto, a factory
usa as chaves legadas. O adapter rejeita provider desconhecido. Capacidades
devem refletir o endpoint real; não são inferidas pelo nome do modelo.

### Compatibilidade legada

| Chave | Tipo | Default |
| :--- | :--- | :--- |
| `api_url` | string | `http://127.0.0.1:8080/v1/chat/completions` |
| `model` | string | `default` |
| `temperature` | número entre 0 e 2 | `0.6` |
| `max_tokens` | inteiro positivo | `4096` |
| `timeout` | número positivo | `300` |
| `ENABLE_GBNF` | booleano | `true` |

Novos workflows não leem diretamente essas chaves; a factory as converte em um
perfil interno `legacy`.

### Orçamento, watchdog e retomada

| Chave | Default |
| :--- | ---: |
| `max_task_steps` | 30 |
| `max_task_tokens` | 200000 |
| `max_task_tool_calls` | 60 |
| `max_task_wall_seconds` | 1800 |
| `max_repeated_no_progress` | 3 |
| `max_consecutive_same_error` | 3 |
| `resume_retry_failed` | `false` |
| `resume_retry_skipped` | `false` |

`checkpoint_file` aponta por padrão para
`runtime/agent_checkpoint.json`. Estados concluídos não voltam a executar;
retry de estados terminais é opt-in.

### Validação legada pós-escrita

`validation` mantém `enabled`, `ruff`, `mypy`, `pytest`, `pytest_dir` e
`fail_triggers_replan`. Ela atende ao `file_writer`/`WorkspaceManager` antigos.
Os workflows de `agent/code` usam `ProjectValidator` e resultados normalizados.

### Política de propostas de código

| Chave | Tipo | Default | Função |
| :--- | :--- | ---: | :--- |
| `code_policy.auto_apply_min_confidence` | número entre 0 e 1 | `0.85` | Limiar mínimo para commit sem confirmação. |
| `code_policy.max_auto_files` | inteiro positivo | `2` | Máximo de arquivos para aplicação automática. |
| `code_policy.require_target_alignment` | booleano | `true` | Penaliza paths não declarados nos targets. |

O score é calculado localmente a partir da estrutura do ChangeSet. Essa seção
não concede sucesso nem ignora validators. `auto_confirm` aprova prompts de
baixa confiança em execução headless e deve continuar `false` no uso manual.

### Outros campos

- `default_system_prompt`: prompt da conversa direta;
- `auto_confirm`: aprova confirmações de escrita das skills e propostas
  `code_task` de baixa confiança em execução headless; mantenha `false` em uso
  manual;
- `task_report`: habilitação, formato e diretório dos relatórios da tarefa.

## `paths.py` e `agent/runtime/paths.py`

O arquivo da raiz é um alias. O módulo canônico é a fonte única dos caminhos de runtime: log, memória JSON/SQLite, backup de
memória, checkpoint, métricas, relatórios, histórico, benchmark, health report
e restore points. `AGENT_RUNTIME_DIR` permite trocar a raiz em testes ou
instâncias isoladas.

`.temp_analysis/` não faz parte desse runtime: é relativo ao repositório que as
skills antigas estão editando.

## `logger.py` e `agent/runtime/logging.py`

O arquivo da raiz é um alias. O módulo canônico configura log em arquivo e console. Garante a criação de `runtime/` antes do
handler de arquivo.

## `benchmark.py`

Fachada de `scripts.benchmark`, que executa o fluxo completo contra o backend configurado, mede duração/passos e
grava `runtime/benchmark_results.json`. É um teste de integração com modelo
real, não uma avaliação hermética. As tarefas de benchmark podem criar os
arquivos de exercício que declaram.

## Dependências

- `pyproject.toml`: fonte de verdade para runtime e extras `dev`/`ml`;
- `requirements-core.txt`, `requirements-ml.txt`, `requirements-dev.txt` e
  `requirements.txt`: fachadas compatíveis para pip;
- `requirements.lock`: snapshot completo e congelado do ambiente usado para
  reprodução exata.

Nenhuma dependência nova é necessária para AST Python, `TaskGraph`, ChangeSet
ou validação básica.

## `pyproject.toml`

Define o pacote instalável, o comando `llm-agent`, dependências, extras, Ruff,
pytest e mypy. O mypy descobre todo o pacote `agent`, scripts e fachadas da raiz,
com `disallow_untyped_defs` e sem overrides por módulo.

## Política de qualidade

`CONTRIBUTING.md` define responsabilidades, direção de dependências, tipagem,
testes e a definição de pronto. `scripts/check_quality.py` aplica limites de
complexidade e tamanho, impede fontes Python ocultas pelo `.gitignore`,
verifica fronteiras arquiteturais, links locais e encoding UTF-8 sem BOM.
`quality/baseline.json` registra os limites globais e mantém vazias as listas de
exceção. O mesmo gate roda em `.github/workflows/ci.yml`.

## Documentação arquitetural

`README.md` cobre instalação e uso, `EstruturaProjeto.md` é a referência
canônica das responsabilidades e os guias permanentes ficam em `docs/`.
Artefatos intermediários de análise, roadmap, tasks e revisão não fazem parte
da documentação versionada.
