# PRE-V1 Freeze

> **STATUS: PRE-V1 ARCHITECTURAL CHECKPOINT.** Este documento registra o estado
> congelado do product-core depois da Wave 8. Ele não é uma declaração de
> release final nem substitui os primary homes da documentação técnica.

## Identidade

- Product-core baseline: `3b21658021102ba69716c41e09dc0bf832db0ea4`
- Product-core tree: `7b10fea3bacb2566b520b1105ada4eb74ce187df`
- Last architectural wave: `Wave 8 — Residual Ownership Sweep`
- Wave 8 status: `PUBLISHED AND CLOSED`

O commit que contém este documento identifica a sincronização documental desta
freeze. Nenhum SHA do commit ainda não criado desta sincronização é embutido
antecipadamente.

## Significado do freeze

- A arquitetura core PRE-V1 até a Wave 8 está congelada.
- Esta etapa pós-W8 é somente sincronização documental e fechamento do freeze.
- Uma mudança arquitetural nova exige uma nova tarefa/wave explicitamente
  autorizada, com Contract e Spec próprios.
- HIST-1 e HIST-2 continuam sendo dívida histórica conhecida; este documento
  não declara CI ou a suíte pytest verde.
- O freeze não significa completude de funcionalidades.
- O freeze não significa release final V1.
- O freeze não autoriza automaticamente integração TCC ou plugins.

## Mapa arquitetural canônico

| Área | Owner/boundary atual |
| --- | --- |
| Modelo e sessão | `ChatSession` constrói requests tipados; `ModelCallService` coordena o lifecycle de completion/stream; `ModelGateway` é a porta para o provider. Payload e protocolo permanecem na boundary do provider. |
| Planning | `Plan` tipado, `PlanAdmissionService`, `ExecutionGateway` e os modelos de replan/repair mantêm admissão e execução separadas. |
| Falhas e recuperação | `FailureFact` e a política/taxonomia canônica classificam falhas e decisões de recovery; texto de diagnóstico não é owner de política. |
| Tools, authority e approval | `ToolInvocationGateway` aplica eligibility, authority, schema e approval. O transporte stdio é uma boundary explícita e exige o contexto de processo aplicável. |
| Mudanças de código | `CodingApplicationService` entra no caso de uso; `CodingWorkflowService` coordena o workflow; `ChangeSetTransaction` prepara, comita, valida e reverte mudanças. |
| Paths, processos e persistência | `agent.runtime.path_safety` e `WorkspaceManager` confinam paths; `ProcessRunner`/helpers stdio possuem lifecycle de processo; `agent.memory.json_persistence` possui escrita atômica. |
| Eventos, snapshots e resultado | A espinha canônica de eventos/correlação alimenta `CanonicalRunSnapshot`; `OperationalOutcome` é a autoridade operacional terminal. |
| Task Definition e política de tarefa | A autoridade de Task Definition da W5.5 e o estado/política task-scoped da W6 permanecem separados e tipados. |
| Compatibilidade e CLI | Compatibilidades retidas são boundaries explícitas de persistência/import/protocolo; não são owners alternativos. O CLI instalado e suportado é `llm-agent`. |

Este mapa é deliberadamente conciso; os detalhes pertencem aos primary homes
em [`docs/README.md`](README.md).

## Dívida histórica aceita

Estas duas ocorrências permanecem conhecidas e não são chamadas de corrigidas:

### HIST-1

`tests/integration/test_standalone_application.py::test_real_task_runner_defers_interrupt_terminal_and_cleanup_until_quiescent`

Sintoma histórico aceito:

`Failed: DID NOT RAISE InvocationLivenessError`

### HIST-2 — Linux only

`tests/unit/runtime/test_memory.py::test_memory_persistence_flushes_before_atomic_replace`

Actual:

`['fsync', 'replace', 'fsync']`

Expected:

`['fsync', 'replace']`

## Fronteira pós-freeze

Trabalho funcional, de plugins ou voltado ao TCC pode avançar em uma etapa
separadamente autorizada, mas somente como nova tarefa com Contract e Spec
próprios. A publicação, a integração de histórico e qualquer alteração da
arquitetura congelada exigem a autorização correspondente.
