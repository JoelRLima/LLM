# Orchestration e AgentApplication

> **STATUS: CURRENT — PRIMARY HOME.** Este documento descreve composição,
> ciclo da tarefa, execução e consumo de resultados.

## Composition root

`AgentApplication.create()` é a raiz suportada, independente de UI:

```text
workspace/config/paths
→ lock e logging
→ model session e builtin SkillRegistry
→ extension bootstrap e ToolRegistry congelado
→ TaskDefinitionRepository / TaskContextResolver / TaskDefinitionCompiler
→ Orchestrator
→ ToolInvocationGateway
```

CLI, modo headless, evals e acceptance instalado reutilizam essa fronteira.
`close()` é idempotente; execução e fechamento são serializados enquanto
stdout/logging legados forem recursos globais do processo.

## Ciclo da tarefa

```text
objetivo
→ TaskRunner
→ resposta trivial, quando aplicável
→ root_task_id para tarefa não trivial
→ TaskRuntimePolicy + TaskPolicyState da tarefa raiz
→ compile/admit Contract e persistir imutavelmente
→ compile/admit Spec vinculada e persistir imutavelmente
→ bind TaskDefinitionRef completa
→ materializar authority confiável no ContextManager
→ route persona
→ planning context
→ hierárquico | segurança | linear | fallback reativo
→ ExecutionGateway de plano
→ PlanExecutor / StepExecutor
→ ToolExecutor
→ ToolInvocationGateway
→ tool result
→ FinalResponder
→ AgentRunResult
```

`AgentSubsystems` constrói serviços sob demanda. `TaskRunner` possui prepare,
resume, dispatch e cleanup. `OrchestratorOperations` concentra checkpoint,
memória, eventos, métricas e task reports. O caminho especializado de
segurança também invoca `code_analyzer` pelo gateway quando ele existe.

Os componentes de Task Definition são invariantes da
`AgentApplication` associada a uma workspace, não uma capability opcional do
provider. Para uma tarefa nova não trivial, `TaskRunner` só alcança routing,
planner ou tools depois que Contract e Spec completos foram admitidos,
persistidos, resolvidos e ligados ao estado. Compiler/resolver indisponível,
resposta malformada ou oversized, falha de provider/admissão/persistência,
binding ausente, mismatch ou fase inválida terminam em bloqueio explícito.

## Identidades e resultados

Uma task mantém objective, persona, plano e eventos. Cada passo possui
`_step_id` estável e `StepExecutionRecord`. Cada chamada canônica possui
`invocation_id`; retry ainda não possui `attempt_id` próprio.

`AgentRunResult` preserva `succeeded`, `failed`, `blocked`, `cancelled`,
`unverified`, `timed_out`, `permission_denied`, `protocol_error` ou
`unavailable` quando o resultado terminal está projetado no `AgentState`.
Somente `succeeded` deve produzir `success: true`; texto final ou output do
modelo não promove status terminal de uma tool. `TaskProgressProjection` é uma
projeção read-only dos records de execução, fatos semânticos e resultado
operacional. `TaskTracker`, builders e renderizadores consomem essa projeção,
mas não definem sucesso. A autoridade operacional continua em
`OperationalOutcome`, e a autoridade semântica/evidencial continua em
`TaskSemantics`.

O lifecycle macro hierárquico é representado explicitamente. Se uma execução
hierárquica for interrompida enquanto `running`, o resume não restaura
silenciosamente um microplano: ele é bloqueado de forma determinística com
`HIERARCHICAL_RESUME_UNSUPPORTED`. Isso não declara suporte a resume no meio de
um MacroPlan.

## Execução e ownership

`PlanExecutor` coordena dependências, budgets, lotes, checkpoint e replan.
`StepExecutor` finaliza exatamente um passo. `ToolExecutor` cria
`ToolInvocationRequest` e exige o gateway canônico; não possui fallback
model-actionable para chamadas diretas.

`TaskRuntimePolicy` é o seam task-scoped estreito que aplica admissões lógicas,
wall-time ativo e a precedência dos fatos de runtime. Ele delega limites
configurados, budget quantitativo, recovery, cancelamento, plano, semântica,
resultado operacional e eventos aos owners existentes. As rotas usam adapters
locais para preservar essa mesma verdade; não criam uma autoridade paralela.

No lote paralelo, o gateway continua owner do enforcement. O finalizer do
`PlanExecutor` é owner único do recording, registra cada slot uma vez em ordem
lógica e só então aplica summarização ou decisão de replan. Completion física
fora de ordem não altera a semântica sequencial.

O caminho nested de produção não cria ledger, token de cancelamento, estado de
policy ou gate de concorrência independente. O contexto filho pode ter
identidade de unidade de trabalho própria, mas reutiliza a policy da tarefa
raiz, o `TaskBudgetLedger`, o `RecoveryBudgetState` e o `CancellationToken`.
A árvore de ownership, os limites efetivos e o resultado operacional continuam
ligados à task pai; falhas de uma multitask interna não podem desaparecer na
projeção externa.

Essa paridade task-scoped cobre execução linear, slots paralelos, reativa,
microplanos hierárquicos, analyzer de segurança, nós de `TaskGraph`, caminhos
de modelo/sessão aninhados e recovery/replan/continuação quando aplicável.
Adapters não precisam compartilhar implementação, mas precisam produzir a
mesma verdade de tarefa raiz.

## Falha, rollback e retomada

Checkpoint v2 revalida o plano no resume. Ele persiste a
`TaskDefinitionRef` compacta, não Contract/Spec nem a
`TaskAuthoritySnapshot` efetiva. A retomada resolve a referência no
repositório durável da workspace, valida identidade, versões e digests,
preserva o `active_phase_id` confiável quando presente e só então materializa
a autoridade normativa. Passos concluídos não repetem; `running` volta a
`pending`; retry de failed ou skipped é opt-in. Cancelamento salva checkpoint.
O checkpoint também preserva admissões lógicas e duração ativa acumulada da
policy raiz; downtime não consome wall-time, e o timestamp monotônico de um
segmento não é persistido. Um lifecycle hierárquico `running` interrompido é
classificado como `HIERARCHICAL_RESUME_UNSUPPORTED` e bloqueado de forma
fail-closed, em vez de ser pseudo-retomado.
Falha da tarefa aciona
rollback do `WorkspaceManager`; transações registradas, inclusive as criadas
por `code_task` ou `FileWriter`, são revertidas antes dos restore points
legados. No domínio `code_task`,
`ChangeSet`/`FileChange`/`ChangeSetTransaction` possuem o próprio
commit/validation/rollback transacional.

Rollback cobre arquivos conhecidos pela operação, não efeitos arbitrários de
processos ou rede. Falha ao persistir memória torna a tarefa falha e preserva
o checkpoint.
