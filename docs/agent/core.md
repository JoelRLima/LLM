# Módulo `agent/` — core

> Parte da documentação técnica do projeto. Veja o [índice](../README.md).

---

## 4.1. [orchestrator.py](../../agent/orchestrator.py)
O coração da execução autônoma. Após a refatoração de modularidade, o `Orchestrator` atua como um coordenador central que instancia e conecta os subcomponentes especializados:

O arquivo público é uma fachada pequena. A implementação foi separada em
`agent/orchestration/`, sem alterar a API consumida pela CLI e pelos testes:

* `subsystems.py`: construção tardia e acesso aos subsistemas;
* `operations.py`: memória, checkpoint, métricas e adapters operacionais;
* `task_runner.py`: ciclo de vida de uma execução e limpeza final;
* `security_service.py`: preparação isolada de objetivos de segurança;
* `hierarchical_service.py`: planejamento e execução hierárquicos.

Essa divisão mantém coordenação, segurança, persistência e execução em
unidades testáveis, sem criar um segundo ponto de entrada para o agente.
* **Subcomponentes:** `ContextManager` (contexto e prompts), `PlanBuilder` (geração do plano), `ExecutionGateway` (ponto único de validação e execução), `PlanExecutor` (execução dos passos), `ReactiveLoop` (fallback reativo), `AutoCoder` (geração de código e testes), `ToolExecutor` (execução de ferramentas), `WorkspaceManager` (backup, rollback, diff, lint), `FinalResponder` (resposta final), `CheckpointManager` (persistência de checkpoint) e `MetricsRecorder` (telemetria de métricas).
* **Inicialização:** Registra ferramentas na inicialização e expõe endpoints utilitários que conectam as necessidades dos subcomponentes.
* **Mecanismo de Execução (`run`):**
  1. Limpa o estado temporário e registra o objetivo.
  2. Identifica se a pergunta é uma saudação ou dúvida trivial para responder diretamente.
  3. Consulta o roteador de persona para carregar o contexto restrito.
  4. Solicita a criação do plano estruturado ao `PlanBuilder` (ou o `HierarchicalPlanner`, para objetivos complexos).
  5. **Delega ao `ExecutionGateway`** (`execute_validated_plan`) a validação (`PlanValidator` → `PlanOptimizer` → `PlanValidator`) e a execução do plano — este é o único ponto de entrada de execução do sistema, atravessado obrigatoriamente pelos 3 caminhos (linear, hierárquico e reativo). Se o plano não gerar (`PlanBuilder` falhar), adota o fallback de decisões interativas de passo a passo (`ReactiveLoop`), que também passa pelo `ExecutionGateway` a cada passo proposto.
  6. Emite eventos telemétricos de controle a cada início/fim de execução de ferramenta.
  7. Se houver falha crítica, executa o rollback das mudanças via `WorkspaceManager`.
* **Streaming:** O método `run()` aceita um parâmetro opcional `stream_callback` que, se fornecido, é repassado ao `FinalResponder` para exibir a resposta final em tempo real.
* **Checkpointing:** Delegado ao `CheckpointManager`, com escrita atômica, versão de schema e validação estrutural. Na retomada, o plano persistido atravessa novamente o `ExecutionGateway`; o checkpoint não pode pular as regras atuais de validação. Ao final da tarefa (sucesso ou falha), ele é removido.
* **Métricas:** Delegado ao `MetricsRecorder` (`agent/reporting/metrics_recorder.py`), também extraído do `Orchestrator`.
* **Cancelamento cooperativo:** O método `run()` captura `KeyboardInterrupt` (Ctrl+C) e interrompe a execução de forma limpa, salvando o checkpoint e retornando uma mensagem amigável. O comando `/retry` permite retomar a tarefa posteriormente a partir do checkpoint.
* **Planejamento hierárquico:** Para objetivos complexos (detectados por `complexity.py`), o `Orchestrator` delega a execução ao `HierarchicalPlanner` (geração de macroplano) e `HierarchicalExecutor` (execução de sub‑objetivos, também via `ExecutionGateway` — ver 4.25), com tracking via `TaskTracker` e sumarização incremental via `IncrementalSummarizer`. Se o macroplano não puder ser gerado, o fluxo linear é usado como fallback.
* **Relatório da tarefa:** Ao final de cada execução, o `Orchestrator` gera um relatório estruturado de auditoria (`TaskReportBuilder`) com passos, métricas, erros, eventos de replanejamento e uma prévia da resposta final. Configurável via `task_report` em `config.json`.

---

## 4.2. [state.py](../../agent/state.py)
Define a estrutura de dados `AgentState` que encapsula o estado de execução global:
* `objective`: O objetivo em processamento.
* `plan` / `plan_step`: O plano ativo e o índice do passo sendo executado.
* `last_tool` / `last_args` / `last_result`: Detalhes da última ação executada pelo agente.
* `tool_history`: Histórico de chamadas a ferramentas da execução atual.
* `memory`: Instância de `AgentMemory` contendo a memória de longo prazo da sessão.
* `events`: Fila de telemetria de passos.
* `conversation_history`: Histórico de turnos anteriores de conversa.
* **`record_tool_result(tool_name, args, result)`:** (Adicionado na refatoração) Centraliza a mutação de estado após cada execução de ferramenta, atualizando `last_tool`, `last_args`, `last_result` e `tool_history` de forma atômica.
* **Estado explícito por passo:** cada item recebe `_step_id` estável e um `StepExecutionRecord` com status, tentativas e último erro.
* **Retomada seletiva:** `running` volta a `pending`; `completed` não é repetido; retry de `failed` e `skipped` é opt-in por configuração.
* **Mutações encapsuladas:** criação, inserção, substituição e transições do plano passam pelos métodos de `AgentState`.

## 4.2.1. [execution_state.py](../../agent/execution_state.py)

Define `StepStatus`, estados terminais e `StepExecutionRecord`. É a máquina de
estados persistida no checkpoint v2.

## 4.2.2. [contracts.py](../../agent/contracts.py)

Fonte dos contratos `TypedDict` compartilhados: `PlanStep`, `ToolResult`,
`ToolHistoryEntry`, `AgentEvent`, `ModelDecision` e `CheckpointData`. O formato
continua sendo dicionário/JSON em runtime, preservando compatibilidade com as
skills.

---

## 4.4. [parsers.py](../../agent/parsers.py)
Contém utilitários cruciais para processamento de saídas e garantia de contratos estritos:
* `extract_json`: Localiza o primeiro par de chaves `{}` e realiza o parseamento ignorando blocos de códigos markdown.
* `extract_json_from_end`: Varre o texto a partir do fim para encontrar o último objeto JSON fechado (útil caso o modelo escreva texto após o JSON).
* `validate_decision`: Valida se o JSON da decisão do agente possui estrutura obrigatória (ação `tool` ou `final`).
* `normalize_tool_result`: Garante que as ferramentas sigam a assinatura de retorno (chaves `ok`, `done`, `data`, `error`, `message`). Caso a ferramenta retorne uma string contendo padrões conhecidos de falha (ex.: "not found", "exception"), normaliza automaticamente a chave `ok` para `False`.
* `validate_tool_args`: Valida as chaves e tipos de argumentos enviados para uma ferramenta contra o schema JSON gerado pela classe da skill. Lida com tipos primitivos, enums, limites numéricos de mínimo/máximo e validações semânticas (ex.: linha inicial menor que a linha final).
* **`sanitize_error` removida deste módulo:** existia uma cópia idêntica desta função aqui e em `error_handler.py` (`ErrorHandler.sanitize_error`), mantidas manualmente em sincronia. A duplicata foi removida; `error_handler.py` (ver `error_handler.py` em `docs/agent/core.md`) é agora a única fonte canônica, e `auto_coder.py` foi atualizado para importar de lá.

---

## 4.10. [auto_coder.py](../../agent/auto_coder.py)
Componente autônomo de auxílio na programação:

> **Compatibilidade legada:** novos fluxos não devem ser adicionados ao
> `AutoCoder`. `agent/code/workflows.py` e a skill `code_task` substituem esse
> caminho com ChangeSet, validação real e dependências estreitas. O componente
> permanece enquanto o executor legado ainda chama sua fachada.
* **Geração de Testes Unitários (`generate_tests`):** Utiliza o LLM para escrever testes Python focados nos principais caminhos de execução do arquivo recém-criado/editado.
* **Ciclo de Correção Automatizado (`test_and_correct`):**
  1. Cria um arquivo temporário contendo o código gerado concatenado aos testes unitários propostos.
  2. Executa a suíte de testes em um subprocesso.
  3. Se ocorrerem erros (falha de asserts, sintaxe, exceções), submete o código, testes e a pilha de erros ao LLM para correção.
  4. Realiza esse ciclo por até 3 tentativas. Se os testes passarem, grava a alteração; se falhar, sinaliza falha da tarefa, disparando o rollback do estado original dos arquivos.
* **Geração de Conteúdo (`generate_content`):** Gera textos estruturados e arquivos limpos sem resquícios de tags markdown ou explicações conversacionais do LLM.

---

## 4.11. [tool_executor.py](../../agent/tool_executor.py)
Responsável por disparar a execução de cada skill cadastrada:
* Valida a persona ativa para impedir que um agente (ex.: `researcher`) utilize ferramentas não atribuídas à sua função.
* Bloqueia de forma proativa ações que esvaziem arquivos fundamentais como `analysis_notes.md`. **Nota:** esta checagem é hoje uma segunda camada de defesa — o mesmo bloqueio já acontece antes, na validação do `ExecutionGateway` (`PlanValidator`), para qualquer passo que faça parte de um plano. Este bloqueio em `tool_executor.py` continua relevante como salvaguarda para chamadas de ferramenta que não passem por um plano formal.
* **Pós-Processamento de Leituras (`maybe_summarize_and_store`):** Toda vez que um arquivo é lido ou analisado pela primeira vez, utiliza a ferramenta `summarize` para extrair um resumo compacto, que é armazenado na memória com o respectivo hash do arquivo para usos futuros de cache.

---

## 4.12. [workspace.py](../../agent/workspace.py)
Controla o ecossistema local do espaço de trabalho:
* **Pontos de Restauração (`create_restore_point`):** Copia os arquivos originais que serão alterados para a pasta técnica `runtime/restore_points/<timestamp>` (caminho de `paths.py`). Antes, esta pasta se chamava `memory_backups/restore/`, reaproveitando por coincidência o mesmo nome usado por `memory.py` para backups de memória — dois conceitos diferentes que compartilhavam nome. Agora `runtime/restore_points/` (rollback de arquivos) e `runtime/memory_backups/` (backup de memória) são diretórios distintos.
* **Rollback:** Se acionado, copia de volta os arquivos preservados e limpa a pasta de restore, devolvendo o projeto ao seu estado inicial limpo.
* **Diff Visível (`show_diff`):** Utiliza o módulo padrão `difflib` para exibir uma saída comparativa clara em formato unificado no console.
* **Lint Check (`lint_check`):** Roda compilação sintática nativa Python (`py_compile`) e, conforme configuração em `config.json` (`validation`), executa opcionalmente `ruff`, `mypy` e `pytest`. Se `fail_triggers_replan` for `true`, lança `ValidationFailedError` que aciona o replanejamento automático.

---

## 4.13. [final_response.py](../../agent/final_response.py)
Compila a resposta definitiva do agente:
* **Geração da Resposta:** Reúne o histórico de uso de ferramentas e as anotações geradas em `analysis_notes.md` para submeter um prompt final ao LLM sem o uso de ferramentas adicionais.
* **Auditoria de Menções:** Examina a resposta em linguagem natural por meio de expressões regulares à procura de menções a caminhos de arquivos. Caso o texto mencione arquivos que o agente não leu de fato através de suas ferramentas, ele anexa um aviso no final da resposta alertando que sugestões sobre aqueles arquivos específicos podem ser imprecisas.
* **Streaming na resposta final:** Se um callback `on_chunk` for fornecido pelo `Orchestrator`, a resposta final é gerada em streaming (token por token) em vez de esperar a geração completa. A CLI exibe o texto progressivamente no terminal.
* **Detecção de objetivo de segurança unificada:** `_is_security_objective` agora delega para `router.is_security_objective()` (ver `router.py` em `docs/agent/llm.md`) em vez de manter sua própria lista de keywords. Antes, existiam 3-4 listas quase idênticas e dessincronizadas (`orchestrator.py`, `final_response.py`, `router.py`), com pequenas divergências reais entre elas.

---

## 4.15. [error_handler.py](../../agent/error_handler.py)
Centraliza o tratamento, sanitização e logging de erros em todo o agente:
* **`sanitize_error(error_message)`:** recebe um stack trace ou mensagem de erro bruta e extrai apenas o tipo, a mensagem essencial e a linha relevante. Se o traceback for longo (>10 linhas), mantém apenas o início e o fim. Esta é a fonte canônica; [`parsers.py`](../../agent/parsers.py) a reutiliza.
* **`handle_step_failure(step_index, reason, tool, args, emit_callback)`:** Trata falhas na execução de um passo específico: sanitiza o erro, emite um evento telemétrico via `emit_callback` e registra no logger. Retorna a string `"continue"` para indicar ao executor que deve seguir para o próximo passo.
* **`purge_stale_context(session)`:** Limpa o histórico de mensagens da sessão em situações de erro grave, mantendo apenas o system prompt original, mensagens de sistema adicionais (como resumos de compressão) e a última mensagem do usuário — evitando acúmulo de contexto corrompido.

---

## 4.16. [cost_guard.py](../../agent/cost_guard.py) 🆕
Centraliza a política de limites de custo de execução do agente. Anteriormente, a verificação de custo (`max_steps`, `max_tokens`, `max_tool_calls`) e a montagem da mensagem de interrupção estavam duplicadas em `PlanExecutor` e `ReactiveLoop`, com valores de fallback divergentes. Este módulo é a única fonte de verdade para essas regras:
* **Constantes padrão:** Define `DEFAULT_MAX_TASK_STEPS = 20`, `DEFAULT_MAX_TASK_TOKENS = 25000` e `DEFAULT_MAX_TASK_TOOL_CALLS = 40`.
* **`check_limits(plan_step, tool_history, estimated_tokens, config) -> bool`:** Retorna `True` se algum limite de custo foi ultrapassado.
* **`build_limit_reached_event(...)`:** Monta o payload do evento de telemetria `cost_limit`.
* **`build_limit_summary(objective, tool_history, last_result) -> str`:** Monta a mensagem padronizada de "tarefa interrompida" exibida ao usuário.

---

## 4.18. [watchdog.py](../../agent/watchdog.py) 🆕
Monitora a execução de uma tarefa e decide quando abortar por segurança ou falta de progresso, sem nenhuma chamada adicional ao LLM. Atua como uma camada de proteção independente do `CostGuard` e dos hard blocks do `PlanExecutor`:
* **Timeout global da tarefa:** soma do tempo de parede de todos os passos (complementa o timeout individual do `python_executor` e `shell`). Configurável via `max_task_wall_seconds` (padrão: 300s).
* **Detecção de loop sem progresso:** mesma ferramenta chamada repetidamente com os mesmos argumentos e resultado idêntico, sinal de que o agente está "girando" sem avançar. Configurável via `max_repeated_no_progress` (padrão: 3).
* **Falhas consecutivas com o mesmo erro:** mesmo que os argumentos variem entre tentativas, se o erro for idêntico por N vezes seguidas, o agente é interrompido. Configurável via `max_consecutive_same_error` (padrão: 3).
* **Ponto de entrada único:** `Watchdog.check_all(start_time, tool_history, config)` — executado a cada passo pelo `PlanExecutor` e `ReactiveLoop`, do mesmo modo que `CostGuard.check_limits(...)`.
* **Telemetria:** `build_watchdog_event` e `build_watchdog_summary` padronizam a emissão de eventos e a mensagem ao usuário.

---

## 4.21. [health_check.py](../../agent/health_check.py) 🆕
Módulo de diagnóstico ("Doctor") do agente. Executável via `python -m agent.health_check` ou pelo comando `/doctor` na CLI.
* `health_check.py` é a fachada compatível; as verificações ficam em `agent/health/`.
* `state_checks.py` valida configuração, memória, hashes e diretórios; `runtime_checks.py` valida Python, permissões, skills e artefatos de runtime; `reporting.py` renderiza e persiste o relatório; `core.py` orquestra essas etapas.
* Verifica: versão do Python, validade do `config.json`, integridade da memória e backups, hashes de arquivos, diretórios órfãos, permissões de leitura/escrita, carregamento de skills, e tamanho de logs/métricas.
* Gera relatório visual no terminal e arquivo `runtime/health_report.json`.
* **Comando CLI**: `/doctor` ou `/diagnostico` (integrado em `commands.py`).
* **Caminhos centralizados:** importa de [`agent/runtime/paths.py`](../../agent/runtime/paths.py) os caminhos de runtime usados também por [`workspace.py`](../../agent/workspace.py), evitando definições paralelas para memória, logs e pontos de restauração.

---

## 4.22. [cancellation.py](../../agent/cancellation.py) 🆕
Utilitário simples de cancelamento cooperativo.
* **`CancellationToken`**: Classe com flag `cancelled`, usada para sinalizar cancelamento de tarefas de forma programática. Pode ser expandida para cancelamento futuro (ex.: via botão em interface web).
* **Integração ativa:** o `Orchestrator`, o `PlanExecutor` e o `StepExecutor` consultam o mesmo token. `cancel_task()` sinaliza o token e salva imediatamente o checkpoint.
* **Limite cooperativo:** uma ferramenta já em voo termina no próximo limite seguro; o executor não inicia o passo seguinte.

---

## 4.36. [checkpoint_manager.py](../../agent/checkpoint_manager.py) 🆕
`CheckpointManager` — extraído do `Orchestrator` para isolar a responsabilidade de I/O de checkpoint (leitura/escrita atômica em disco), que antes estava misturada com o resto da lógica de coordenação.
* **`save(agent_state)`**: serializa via `agent_state.to_checkpoint_dict()` e grava atomicamente (`arquivo.tmp` + `os.replace`).
* **`load() -> Optional[dict]`**: carrega o checkpoint salvo, retornando `None` silenciosamente se ausente ou corrompido.
* **`delete()`**: remove o arquivo de checkpoint ao final da tarefa.
* A serialização em si (`to_checkpoint_dict`/`from_checkpoint_dict`) continua em `state.py` — este módulo cuida apenas do I/O.
* **Schema v2:** persiste IDs, status, tentativas e último erro por passo. Checkpoints v1 são rejeitados de forma fail-closed.
* **Retry configurável:** `resume_retry_failed` e `resume_retry_skipped` controlam a reexecução de estados terminais na retomada; ambos são `false` por padrão.
