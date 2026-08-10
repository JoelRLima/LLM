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
→ trivial ou route persona
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

## Identidades e resultados

Uma task mantém objective, persona, plano e eventos. Cada passo possui
`_step_id` estável e `StepExecutionRecord`. Cada chamada canônica possui
`invocation_id`; retry ainda não possui `attempt_id` próprio.

`AgentRunResult` preserva `succeeded`, `failed`, `blocked`, `cancelled`,
`unverified`, `timed_out`, `permission_denied`, `protocol_error` ou
`unavailable`. Somente `succeeded` produz `success: true`. Texto final ou output
do modelo não promove status terminal de uma tool.

## Execução e ownership

`PlanExecutor` coordena dependências, budgets, lotes, checkpoint e replan.
`StepExecutor` finaliza exatamente um passo. `ToolExecutor` cria
`ToolInvocationRequest` e exige o gateway canônico; não possui fallback
model-actionable para chamadas diretas.

No lote paralelo, o gateway continua owner do enforcement. O finalizer do
`PlanExecutor` é owner único do recording, registra cada slot uma vez em ordem
lógica e só então aplica summarização ou decisão de replan. Completion física
fora de ordem não altera a semântica sequencial.

## Falha, rollback e retomada

Checkpoint v2 revalida o plano no resume e não persiste authority efetiva.
Passos concluídos não repetem; `running` volta a `pending`; retry de failed ou
skipped é opt-in. Cancelamento salva checkpoint. Falha da tarefa aciona
rollback do `WorkspaceManager`; no domínio `code_task`, o `ChangeSet` possui
seu próprio commit/validation/rollback transacional.

Rollback cobre arquivos conhecidos pela operação, não efeitos arbitrários de
processos ou rede. Falha ao persistir memória torna a tarefa falha e preserva
o checkpoint.
