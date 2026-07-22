# Módulo `agent/` — planning

> Parte da documentação técnica do projeto. Veja o [índice](../README.md).

## Estado atual de dependências e multitarefa

`task_graph.py` define DAG, prioridade, recursos, políticas, estados e checkpoint
próprio. `task_scheduler.py` executa nós prontos com contextos filhos,
concorrência limitada, conflitos read/write e agregação determinística.
`MacroPlan.depends_on` deixou de ser informativo: o planner converte e valida o
grafo, e o executor hierárquico usa ordem topológica e bloqueia dependentes de
macro-passos falhos. O caminho hierárquico legado permanece sequencial por
compartilhar sessão/AgentState; workflows novos usam o scheduler isolado.

Consulte [TaskGraph e multitarefa](../multitarefa.md).

---

## 4.7. [plan_builder.py](../../agent/planning/plan_builder.py)
Interage com o modelo de linguagem especificamente para estruturar um plano de ações:
* **Construção do Prompt:** Junta as informações de objetivo, arquivos e descrições curtas das ferramentas.
* **Regras de Planejamento:** Exige que cada etapa tenha exatamente uma ferramenta. Instrui o modelo a usar `file_writer` para apagar arquivos comuns (com `content: ""`), mas proíbe esvaziar `analysis_notes.md`. Também proíbe o uso de `shell` para operações de arquivo.
* **Validação Inicial:** Valida e remove do plano passos cujos argumentos não correspondam às especificações exigidas pelas ferramentas.
* **`build_security_plan` removido:** este método existia paralelamente à lógica de análise de segurança do `Orchestrator` (`_handle_security_analysis`), duplicando quase integralmente a mesma lógica (rodar `code_analyzer` em modo `security`, consolidar achados via `SecurityScanner`). Era código morto confirmado — nenhum lugar do projeto o chamava. Removido; a versão que de fato roda é `orchestrator._handle_security_analysis`.

---

## 4.8. [plan_executor.py](../../agent/planning/plan_executor.py)
Coordena a sequência de passos já validada pelo `ExecutionGateway`. Desde as fases 1–3, o `PlanExecutor` não contém mais o ciclo unitário de ferramenta: ele seleciona apenas passos `pending` e delega execução, cache, pós-processamento e transição terminal ao `StepExecutor`:
* **Ponto de Restauração:** Antes de executar a lista de passos, solicita ao `WorkspaceManager` o backup preventivo de arquivos sob iminência de modificação.
* **Mecanismos de Segurança:**
  * **Verificação de Custo:** Delega ao `CostGuard` a verificação de limites de passos, tokens e chamadas de ferramentas.
  * **Hard Block:** Impede que ferramentas de análise/leitura sejam chamadas repetidamente com os mesmos parâmetros exatos no mesmo arquivo, mitigando loops redundantes.
  * **Preenchimento de Escrita:** Detecta se um passo de escrita de arquivo está sem o campo `content` (usando `is None` em vez de falsy, para permitir `content: ""` intencional) e solicita ao `AutoCoder` a geração inteligente do código de conteúdo.
  * **Diferencial (Diff):** Antes de persistir qualquer escrita, invoca a impressão do diff no console para transparência visual.
* **Cache Inteligente:** Se um arquivo a ser lido/analisado tiver o mesmo hash SHA256 do arquivo em cache na memória, o executor recupera o resumo do arquivo da memória instantaneamente, pulando a leitura direta.
* **Ciclo Pós-Execução:** Invoca verificação de testes automatizados e linters para validar modificações.
* **Dependência Explícita entre Passos:** Antes de executar cada passo, o executor analisa o plano e detecta dependências implícitas baseadas em arquivos (ex.: um `file_reader` que lê um arquivo gerado por um `file_writer` anterior). Se a dependência falhou, o passo atual é pulado automaticamente para evitar erros em cascata, com registro no histórico de ferramentas.
* **Integração com Replanejamento:** Em caso de falha de um passo, o executor consulta o `ErrorHandler`; se a ação for `"replan"`, aciona o `Replanner` e os novos passos são injetados no plano (substituindo o passo que falhou), dando continuidade à execução.
* **Responsabilidade atual:** coordena dependências, lote paralelo de leituras, limites, cancelamento e substituição de passos por replan.
* **Retomada:** usa os IDs e estados do `AgentState`; passos concluídos não voltam a executar.

### 4.8.1. [step_executor.py](../../agent/planning/step_executor.py)

Executa e finaliza exatamente um passo. Valida schema/permissões, consulta
cache, chama a ferramenta, executa pós-processamento e aplica `completed`,
`failed` ou `skipped`. Um resultado `ok: false` nunca é convertido em sucesso,
mesmo quando a política permite continuar o restante do plano. Suas
dependências são descritas por portas tipadas menores (`ExecutionStatePort`,
`WorkspacePort`, `CancellationPort`, `StepRuntimePort` e outras), facilitando
testes isolados.

---

## 4.9. [reactive_loop.py](../../agent/planning/reactive_loop.py)
Implementa o fluxo reativo antigo que atua como barreira de segurança secundária. Se o gerador de plano falhar, o loop reativo assume a liderança e decide passo a passo qual ferramenta chamar e com quais parâmetros, baseando-se no histórico recente de execuções. Também utiliza `CostGuard` para verificar limites de custo.
* **Validação via `ExecutionGateway` (correção de segurança):** antes deste caminho validava cada passo apenas com `validate_tool_args` (checagem de schema), sem o `PlanValidator` completo — não verificava, por exemplo, se o passo esvaziaria `analysis_notes.md`, nem dependências invertidas de arquivo. Agora, cada passo proposto pelo LLM é tratado como um plano de 1 passo e atravessa o mesmo pipeline de validação/otimização do `ExecutionGateway` usado pelos caminhos linear e hierárquico, antes de ser executado. Falhas de **runtime** (a ferramenta rodou mas retornou erro, ex.: `FileNotFoundError`) continuam tratadas pelo fluxo original de `classify_error` + `Replanner`, que é ortogonal a essa validação estrutural prévia.

---

## 4.19. [replan.py](../../agent/planning/replan.py) 🆕
Implementa o replanejamento automático quando uma ferramenta falha repetidamente (Fase 4C, item 5). Segue o fluxo: **classificar erro → heurística determinística → LLM (último recurso) → aborto**:
* **`ErrorCategory` (Enum):** classifica erros em `FILE_NOT_FOUND`, `SANDBOX`, `SCHEMA`, `TOOL_BLOCKED`, `TIMEOUT`, `UNKNOWN`.
* **`ReplanContext` (dataclass):** agrupa o estado completo do replanejamento (task, current_step, tool_history, retries, exceção, orçamento).
* **`ReplanAction` (dataclass):** representa um ou mais passos gerados pelo replanejador, com indicação da fonte (`heuristic` ou `llm`) e o motivo da substituição.
* **`RetryPolicy` (classe):** limites configuráveis de tentativas (`max_total=2`, `max_heuristic=2`, `max_llm=1`), preparada para evoluir por ferramenta.
* **`classify_error(message) → ErrorCategory`:** classificação determinística baseada na mensagem de erro.
* **`try_heuristic(category, tool, args) → Optional[ReplanAction]`:** heurísticas determinísticas. Atualmente cobre `FileNotFoundError` (gera `grep` + `directory_lister`). Heurísticas inseguras foram deliberadamente excluídas.
* **`ask_llm_for_alternative(step, error, orchestrator) → Optional[ReplanAction]`:** último recurso — consulta o LLM para sugerir um passo alternativo, apenas se a heurística falhar e a `RetryPolicy` permitir.
* **`replan(ctx, error_msg, orchestrator) → Optional[ReplanAction]`:** ponto de entrada único chamado por `PlanExecutor` e `ReactiveLoop`. Registra logs de cada replanejamento via `logger.info`.
* **Integração:** `error_handler.py` retorna `"replan"` para erros recuperáveis; `plan_executor.py` usa loop `while` e injeta novos passos no plano; `reactive_loop.py` chama o replanner quando uma ferramenta falha.
* **Telemetria de replanejamento:** o [`ExecutionGateway`](../../agent/planning/execution_gateway.py) emite `orchestrator._emit("replan", {...})` sempre que aciona o replanejador para um passo bloqueado pelo `PlanValidator`; [`task_report.py`](../../agent/reporting/task_report.py) consome esses eventos.

---

## 4.23. [complexity.py](../../agent/planning/complexity.py) 🆕
Detector de complexidade de objetivos. Decide se um objetivo deve ser tratado via planejamento hierárquico (MacroPlan) ou pelo fluxo linear padrão.
* **`is_hierarchical(objective) -> bool`**: Calcula uma pontuação heurística baseada em palavras‑chave, estrutura do texto e comprimento. Retorna `True` se a pontuação atingir o limiar configurável `HIERARCHICAL_SCORE_THRESHOLD`.
* **`compute_complexity_score(objective) -> float`**: Retorna a pontuação bruta para diagnóstico.
* **Overlap com keywords de segurança corrigido:** `_COMPLEXITY_KEYWORDS` incluía palavras puramente de segurança (`"segurança"`, `"vulnerabilidades"`, `"auditoria"`), que também disparavam a persona `security_auditor` em `router.py`. Um objetivo simples como "verifique vulnerabilidades neste arquivo.py" podia pontuar alto o bastante só pela palavra-chave e ser roteado ao modo hierárquico sem nenhum sinal real de amplitude/escopo. Essas palavras foram removidas desta lista — a detecção de segurança continua correta via `router.is_security_objective()`, mas deixou de inflar artificialmente a pontuação de complexidade.

---

## 4.24. [hierarchical_planner.py](../../agent/planning/hierarchical_planner.py) 🆕
Planejador hierárquico: decompõe um objetivo complexo em um `MacroPlan` (lista de `MacroStep`), usando o LLM.
* **`Priority` (Enum)**: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.
* **`MacroStep` (dataclass)**: `id`, `title`, `goal`, `priority`, `depends_on`
  (validado e executado via TaskGraph) e `estimated_tools`.
* **`MacroPlan` (dataclass)**: `objective`, `steps`, `schema_version`.
* **`HierarchicalPlanner(ask_model, valid_tools)`**: Recebe uma função `ask_model` injetada e a lista de ferramentas válidas. O método `build_plan(objective)` retorna um `MacroPlan` ou `None` (fallback para fluxo linear). Desacoplado do `Orchestrator`.
* **Gramática GBNF corrigida:** `grammars.MACRO_PLAN_GRAMMAR` (ver `grammars.py` em `docs/agent/llm.md`) forçava cada passo a ter só `id`, `title`, `goal`, `priority` — mas o prompt aqui instrui o modelo a também gerar `depends_on` e `estimated_tools`, que `_validate_step` lê ativamente. Com `ENABLE_GBNF=true` (padrão), a gramática impedia o modelo de produzir esses campos, contradizendo o próprio prompt. Corrigido: a gramática agora aceita os dois campos como opcionais.

---

## 4.25. [hierarchical_executor.py](../../agent/planning/hierarchical_executor.py) 🆕
Executor de `MacroPlan`: orquestra a execução de cada `MacroStep` como uma mini‑tarefa independente.
* Recebe por injeção: `plan_builder`, `plan_executor`, `final_responder`, `context_manager`, `session`, `tracker`, `summarizer` e **`execution_gateway`**.
* Para cada passo: gera um micro-plano via `plan_builder` e o executa **através do `ExecutionGateway`** (não mais chamando `plan_executor.execute()` diretamente), coleta resultados das ferramentas (sem chamar `FinalResponder`), atualiza o `TaskTracker` e alimenta o `IncrementalSummarizer`.
* Ao final, chama o `FinalResponder` **uma única vez** para gerar a resposta consolidada.
* **Fronteira de segurança:** todo sub-objetivo atravessa o `ExecutionGateway` antes da execução. Se o gateway abortar um microplano inseguro e irrecuperável, o `HierarchicalExecutor` interrompe o restante do `MacroPlan` para preservar o estado de falha.

---

## 4.30. [tool_metadata.py](../../agent/planning/tool_metadata.py) 🆕
Visão de custo e características das ferramentas, usada pelo `PlanValidator` e
`PlanOptimizer` e derivada do catálogo canônico de skills.
* **`ToolMetadata` (dataclass)**: `cost`, `reads_disk`, `writes_disk`, `modifies_workspace`, `cacheable`, `side_effects`, `category` (READ, WRITE, EXECUTE, SEARCH, ANALYZE, NETWORK).
* **`TOOL_METADATA`**: dicionário mapeando nome da ferramenta → `ToolMetadata` para todas as skills.
  Essa visão é derivada de `agent/skills/catalog.py`; custos não devem ser
  cadastrados novamente aqui.
* **`estimate_step_cost(tool, args) -> int`**: refina o custo para `file_reader` (parcial vs inteiro) e `file_writer` (por ação: patch, ast_patch, write).

---

## 4.31. [plan_validator.py](../../agent/planning/plan_validator.py) 🆕
Validador de planos que apenas diagnostica, nunca modifica. Executado antes e depois do `PlanOptimizer`.
* **`ValidationReport`**: `is_valid`, `errors`, `warnings`, `blocked_steps` (lista de `BlockedStep` com `index` e `reason`).
* **Validações**: schema e ferramentas, esvaziamento de `analysis_notes.md`, patch sem leitura prévia (aviso), escritas consecutivas (aviso), dependências invertidas (bloqueio).
* **Integração**: chamado pelo `Orchestrator` após a geração do plano e após a otimização. Passos bloqueados são encaminhados ao `Replanner`.

---

## 4.32. [plan_optimizer.py](../../agent/planning/plan_optimizer.py) 🆕
Otimizador de planos que aplica apenas transformações comprovadamente equivalentes.
* **`OptimizationReport`**: `optimized_steps`, `removed_duplicates`, `cost_before`, `cost_after`, `cost_details`, `transformations`, `changed`.
* **Otimizações seguras**: remoção de duplicatas exatas (apenas ferramentas `cacheable`), reordenação de leituras/buscas/análises independentes (nunca move ferramentas com `side_effects=True`).
* **Nunca** insere passos novos, converte ferramentas ou altera argumentos. Usa `ToolMetadata` para todas as decisões.

---

## 4.35. [execution_gateway.py](../../agent/planning/execution_gateway.py) 🆕 (componente central da refatoração)
`ExecutionGateway` — o **ponto único de entrada de execução** do agente. Resolve o achado arquitetural mais importante encontrado na análise de refatoração: antes deste componente, existiam **3 caminhos de execução de plano** (linear via `plan_executor.py`, hierárquico via `hierarchical_executor.py`, reativo via `reactive_loop.py`), cada um decidindo por conta própria se e como aplicava validação de segurança — o hierárquico, por exemplo, não validava nada; o reativo só validava schema, não o `PlanValidator` completo.
* **`ExecutionGateway(orchestrator)`**: recebe o `Orchestrator` (necessário porque `PlanExecutor` e `replan()` já dependem fortemente dele).
* **`execute_validated_plan(plan, objective, tool_usage_count) -> ExecutionResult`**: método público principal, usado pelos caminhos linear e hierárquico. Executa, sempre na mesma ordem: `PlanValidator.validate` → `PlanOptimizer.optimize` → `PlanValidator.validate` (pós-otimização) → replanejamento/descarte de passos bloqueados → `PlanExecutor.execute`.
* **`validate_and_optimize_plan(plan, objective) -> Optional[list]`**: método público que expõe só a etapa de validação/otimização, sem executar — usado pelo caminho reativo (`reactive_loop.py`), que precisa validar cada passo individualmente (como um plano de 1 item) antes de rodá-lo via `orchestrator._run_tool`, sem passar pelo modelo de execução multi-passo do `PlanExecutor`.
* **`ExecutionResult` (dataclass)**: `aborted` (bool), `final_answer` (resposta pronta se abortado ou se a execução já gerou uma resposta direta), `validated_plan` (o plano final que foi/seria executado).
* **Emite telemetria `"replan"`:** ao acionar o replanejador para um passo bloqueado; [`task_report.py`](../../agent/reporting/task_report.py) inclui esses eventos no relatório.
* **Conectado em:** `orchestrator.run()` (caminho linear — a validação prévia deixou de estar duplicada no `Orchestrator`), `hierarchical_executor._execute_step()` (caminho hierárquico), `reactive_loop.run_reactive()` (caminho reativo, validação por passo).
