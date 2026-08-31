# Arquitetura de execução

> **STATUS: CURRENT — OVERVIEW.** Este documento é uma porta de entrada; os
> contratos normativos pertencem aos primary homes ligados abaixo.

## Composition e fluxo

```text
CLI / headless
→ AgentApplication.create()
→ non-trivial objective + root_task_id
→ TaskRuntimePolicy + TaskPolicyState da tarefa raiz
→ admit/persist immutable TaskContract
→ admit/persist immutable TaskSpec bound to Contract
→ bind/resolve TaskDefinitionRef
→ trusted authority materialization through ContextManager
→ planning context + persona/tool view
→ planner (linear, reactive ou hierarchical)
→ ExecutionGateway do plano (validate/optimize/revalidate)
→ PlanExecutor / StepExecutor
→ ToolInvocationGateway (eligibility/authority/schema/approval)
→ builtin adapter ou stdio process
→ ToolResult → state/checkpoint
→ TaskProgressProjection read-only → report/final response
```

Há dois gateways diferentes: `agent/planning/execution_gateway.py` valida o
plano; `agent/tools/invocation_gateway.py` aplica a fronteira de cada tool.
Nenhum plano ou tool apresentada cria authority. Veja
[planning](agent/planning.md), [orchestration](agent/orchestration.md) e
[security](agent/security.md). A `TaskRuntimePolicy` é o seam task-scoped que
coordena admissões e fatos de limite entre essas rotas, sem substituir os
owners quantitativos, de recovery, cancelamento, plano, semântica ou resultado
operacional.

Task Definition e capability authority são eixos distintos:
`TaskContract`/`TaskSpec` definem a autoridade normativa;
`TaskDefinitionRef` identifica essa definição; `TaskAuthoritySnapshot`
autoriza capabilities; e `Plan` mantém ownership da execução. Tarefa não
trivial sem definição completa bloqueia antes de routing/planning/tools.

## Estado e retomada

Steps têm `_step_id` estável, tentativas e estados `pending`, `running`,
`completed`, `failed`, `skipped`, `blocked` ou `unverified`. Checkpoint v2
preserva plano e records. No resume, `running` volta a `pending`, concluídos,
blocked e unverified permanecem terminais, e failed/skipped só voltam com flags
explícitas. O checkpoint restaura a `TaskDefinitionRef` compacta; Contract e
Spec são resolvidos no storage da mesma workspace e revalidados por identidade,
versão, digest e fase antes de materializar authority normativa. O plano é
revalidado e a capability authority é reconstruída do runtime atual; nenhuma
`TaskAuthoritySnapshot` é confiada ao checkpoint.

O checkpoint também preserva o estado task-scoped da policy: admissões lógicas e
duração ativa acumulada. Somente a duração acumulada é persistida; um timestamp
monotônico de segmento não sobrevive ao processo, e downtime não consome o
wall-time. Contextos aninhados reutilizam a policy e o cancelamento da tarefa
raiz. Um lifecycle hierárquico `running` interrompido é classificado de forma
fail-closed como `HIERARCHICAL_RESUME_UNSUPPORTED`; não há pseudo-resume de
microplano.

## Paralelismo

O scheduler pode executar fisicamente leituras independentes em paralelo. A
semântica de decisão, o recording terminal e a ordem consumida permanecem na
ordem lógica do plano, não na ordem em que futures terminam. Escritas e recursos
incompatíveis são serializados. A policy admite unidades lógicas antes do
dispatch e trunca atomicamente o prefixo paralelo restante. Detalhes:
[planning](agent/planning.md),
[orchestration](agent/orchestration.md) e [multitarefa](multitarefa.md).

## Recovery e limites

Timeout publica um único resultado terminal e descarta conclusão tardia para
efeitos de estado. Cancelamento é cooperativo no core; adapters de processo
possuem cleanup específico. A precedência da policy task-scoped é
cancellation → exaustão quantitativa → exaustão lógica → wall-time ativo →
watchdog/sem progresso → recovery; guards de invocation podem ter contratos
distintos. Restore points de workspace, checkpoint e backup de memória não são
equivalentes. Não há transação distribuída nem sandbox universal.
Processos/stdout/stderr/plataformas: [runtime](agent/runtime.md).
