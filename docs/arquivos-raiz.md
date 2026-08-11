# Fachadas da raiz e pontos canônicos

> **STATUS: CURRENT — ROOT-FILES REFERENCE.** Este arquivo não define contratos
> de subsistemas; veja o [índice técnico](README.md).

A raiz mantém somente entry points, aliases de compatibilidade, configuração
de empacotamento e documentação. Implementações novas pertencem ao pacote
`agent/`; consulte também o [inventário de legado](legado.md).

## `cli.py`

Fachada executável de `agent.interfaces.cli.app`. O módulo canônico adapta chat,
execução headless, diagnóstico e manutenção de configuração/estado para
`AgentApplication`; não recompõe sessão e orquestrador por conta própria.
`--help`, `--version` e `config path` não inicializam recursos.

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

## `config.py`, `agent/runtime/config_repository.py` e `config.example.json`

`config.py` e `agent/runtime/config.py` são fachadas legadas.
`ConfigRepository` é a fronteira standalone: carrega o default empacotado,
exige `schema_version`, rejeita chaves desconhecidas e versões futuras, e
aplica a precedência CLI, ambiente allowlisted, arquivo e default.
`config_effective.py` materializa no perfil selecionado os overrides explícitos
de endpoint, modelo, temperatura, tokens, timeout e GBNF; assim doctor,
`ChatSession` e provider observam a mesma configuração efetiva.

`llm-agent config init` cria o arquivo de maneira atômica; `path` apenas mostra
o destino; `validate` resolve e valida; `migrate --from` copia uma origem
explícita sem removê-la. `config.example.json` documenta o schema, mas não
precisa ser copiado para o workspace.

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

Consumidores diretos de compatibilidade podem usar as chaves legadas quando
`default_model_profile` não existir. O `ConfigRepository` standalone, porém,
valida o profile selecionado e falha para configuração inválida; ele não faz
fallback silencioso. O adapter rejeita provider desconhecido. Capacidades devem
refletir o endpoint real; não são inferidas pelo nome do modelo.

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

`checkpoint_file` não é preferência persistida no schema standalone. A
aplicação injeta o checkpoint correspondente ao workspace. Estados concluídos
não voltam a executar; retry de estados terminais é opt-in.

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
não concede sucesso nem ignora validators. Na aplicação standalone, a
confirmação do efeito passa por `ApprovalPort`: chat pergunta ao usuário,
headless bloqueia e `run --yes` fornece approval somente para a execução
corrente. Authority, capabilities e grants são verificados separadamente.

### Outros campos

- `default_system_prompt`: prompt da conversa direta;
- `auto_confirm`: compatibilidade para consumidores que constroem skills
  diretamente; não substitui a autoridade explícita da aplicação standalone e
  deve permanecer `false` em uso manual;
- `task_report`: habilitação e formato; o diretório é definido pelos paths do
  workspace.

## `paths.py` e `agent/runtime/paths.py`

O arquivo da raiz é um alias. `AppPaths` separa configuração, dados, estado,
cache e logs sem efeitos no construtor. `WorkspacePaths` particiona memória,
checkpoint, métricas, relatórios, artifacts, histórico, benchmark, restore
points e scratch pelo identificador estável do workspace.

`LLM_AGENT_HOME` fixa uma raiz portátil. Sem ele, Windows usa
`APPDATA`/`LOCALAPPDATA` e Unix usa XDG. `AGENT_RUNTIME_DIR` permanece somente
como compatibilidade. O scratch standalone fica no cache da aplicação;
`.temp_analysis/` é fallback das skills antigas.

## `logger.py` e `agent/runtime/logging.py`

O arquivo da raiz é um alias. Importar o módulo canônico não abre arquivo.
`AgentApplication` chama `setup_logger()` após resolver os paths, e
`teardown_logger()` libera o lease no lifecycle. Instâncias compatíveis
compartilham handlers por contagem de referências; destinos simultâneos
incompatíveis são rejeitados.

## `benchmark.py`

Fachada de `scripts.benchmark`, que executa o fluxo completo contra o backend
configurado, mede duração/passos e grava o resultado no estado do workspace. É
um teste de integração com modelo real, não uma avaliação hermética. As tarefas
de benchmark podem criar os arquivos de exercício que declaram. Timeout é
cooperativo: o benchmark solicita cancelamento e aguarda a chamada em voo
terminar antes de liberar a aplicação.

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

`README.md` cobre instalação e uso, `EstruturaProjeto.md` é um mapa secundário
da estrutura e das responsabilidades do código, e os guias permanentes ficam em
`docs/`. `docs/README.md` é o índice authoritative de ownership documental.
Artefatos intermediários de análise, roadmap, tasks e revisão não fazem parte
da documentação versionada.
