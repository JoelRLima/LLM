# Arquitetura de execução

> **STATUS: CURRENT — OVERVIEW.** Este documento é uma porta de entrada; os
> contratos normativos pertencem aos primary homes ligados abaixo.

## Composition e fluxo

```text
CLI / headless
→ AgentApplication.create()
→ planning context + persona/tool view
→ planner (linear, reactive ou hierarchical)
→ ExecutionGateway do plano (validate/optimize/revalidate)
→ PlanExecutor / StepExecutor
→ ToolInvocationGateway (eligibility/authority/schema/approval)
→ builtin adapter ou stdio process
→ ToolResult → state/checkpoint/report/final response
```

Há dois gateways diferentes: `agent/planning/execution_gateway.py` valida o
plano; `agent/tools/invocation_gateway.py` aplica a fronteira de cada tool.
Nenhum plano ou tool apresentada cria authority. Veja
[planning](agent/planning.md), [orchestration](agent/orchestration.md) e
[security](agent/security.md).

## Estado e retomada

Steps têm `_step_id` estável, tentativas e estados `pending`, `running`,
`completed`, `failed`, `skipped`, `blocked` ou `unverified`. Checkpoint v2
preserva plano e records. No resume, `running` volta a `pending`, concluídos,
blocked e unverified permanecem terminais, e failed/skipped só voltam com flags
explícitas. O plano restaurado é revalidado e authority é reconstruída do
runtime atual; ela não é confiada ao checkpoint.

## Paralelismo

O scheduler pode executar fisicamente leituras independentes em paralelo. A
semântica de decisão, o recording terminal e a ordem consumida permanecem na
ordem lógica do plano, não na ordem em que futures terminam. Escritas e recursos
incompatíveis são serializados. Detalhes: [planning](agent/planning.md),
[orchestration](agent/orchestration.md) e [multitarefa](multitarefa.md).

## Recovery e limites

Timeout publica um único resultado terminal e descarta conclusão tardia para
efeitos de estado. Cancelamento é cooperativo no core; adapters de processo
possuem cleanup específico. Restore points de workspace, checkpoint e backup de
memória não são equivalentes. Não há transação distribuída nem sandbox universal.
Processos/stdout/stderr/plataformas: [runtime](agent/runtime.md).
