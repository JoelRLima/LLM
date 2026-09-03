# Documentação técnica

> **STATUS: CURRENT — DOCUMENTATION HUB.** O [README raiz](../README.md) é a
> entrada de produto; este arquivo é o mapa authoritative para manutenção.

## Classes documentais

- **CURRENT / PRIMARY HOME**: contrato vigente; existe um único home por
  conceito.
- **CURRENT / REFERENCE ou GUIDE**: uso transversal ou operacional; resume e
  aponta para primary homes.
- **ADR**: decisão e contexto histórico. Pode ser aceito, substituído ou
  parcialmente substituído; não é reescrito para fingir o estado atual.
- **HISTORICAL / CLOSED MILESTONE**: evidência de uma fase encerrada, nunca fonte
  do contrato CURRENT.

## Primary homes

| Quero entender… | Documento authoritative |
| --- | --- |
| contratos core, identidades e lifecycle | [agent/core.md](agent/core.md) |
| planners, contexto, discovery e replan | [agent/planning.md](agent/planning.md) |
| `AgentApplication`, executor e consumo de resultados | [agent/orchestration.md](agent/orchestration.md) |
| authority, grants, approval, eligibility e trust boundaries | [agent/security.md](agent/security.md) |
| tools, skills e exposição ao modelo | [agent/tools.md](agent/tools.md) |
| runtime task-scoped, stdio, timeout, processos e diferenças de plataforma | [agent/runtime.md](agent/runtime.md) |
| provider/modelo e structured output | [agent/llm.md](agent/llm.md) |
| memória persistente e camada semântica opcional | [agent/memory.md](agent/memory.md) |
| eval core, Capability/Regression Sets e evidência | [agent/evaluation.md](agent/evaluation.md) |
| measurement, projeção de progresso e relatórios | [agent/reporting.md](agent/reporting.md) |
| health e diagnósticos | [agent/health.md](agent/health.md) |
| observabilidade, inspector, replay e export | [observability-inspector.md](observability-inspector.md) |
| desenvolvimento de extensions | [guia-extensao.md](guia-extensao.md) |
| instalação, CLI e paths | [operacao-standalone.md](operacao-standalone.md) |
| estratégia de testes e quality gates | [testes.md](testes.md) |

## Matriz CURRENT de capabilities

“Implementada” significa que há código; “model-actionable” significa que pode
aparecer na view do planner/modelo. Em todos os casos, exposição não equivale a
permissão de executar.

| Capability | Implementada? | Model-actionable? | Exposure / boundary | Primary docs |
| --- | --- | --- | --- | --- |
| Read | sim | sim | workspace; persona/view e gateway | [tools](agent/tools.md) |
| Search/list | sim | sim | busca/listagem confinada ao workspace | [tools](agent/tools.md) |
| Modify + validate | sim | sim | `code_task` → `ChangeSet` → `ProjectValidator` | [tools](agent/tools.md), [agente de código](agente-codigo.md) |
| Low-level writer | sim | **não** | `file_writer`; admin/legado, excluído das views | [tools](agent/tools.md) |
| Shell | sim, reduzida | sim, condicional | somente `ruff check`, `git log`, `tree`; sem shell | [tools](agent/tools.md) |
| Git | sim, reduzida | sim via skills permitidas | somente histórico local (`log`); status/diff model-actionable removidos | [tools](agent/tools.md) |
| Ruff | sim | sim via ShellSkill | somente `ruff check`; validação, sem `--fix`/config arbitrária | [tools](agent/tools.md) |
| External stdio | sim | condicional | catálogo + workspace grants (incluindo `process`) + task authority + gateway | [tools](agent/tools.md), [runtime](agent/runtime.md) |
| Memory | sim | sim, conforme persona/fluxo | persistente por workspace; sem authority; semântica opcional | [memory](agent/memory.md) |
| MCP | **não fornecido** | não | nenhuma integração MCP no repo | [tools](agent/tools.md) |
| Web | sim (`web_search`) | sim para researcher | rede sujeita a policy/approval; não é browser/MCP | [tools](agent/tools.md) |

Não há arbitrary shell, package install model-actionable, sandbox universal de
filesystem/rede nem garantia de execução de qualquer extension descoberta.

## Fluxo arquitetural

[arquitetura-execucao.md](arquitetura-execucao.md) é a visão macro. Os detalhes
pertencem aos primary homes. Para extensions, o caminho CURRENT é:

```text
catalog/config → ApplicationExtensionBootstrap → task authority
→ planner visibility → ToolInvocationGateway → stdio adapter
→ external process → ToolResult
```
O planner orienta; o gateway aplica. Binding e `active_skills` são eligibility
estritamente redutiva, não fontes de authority. Veja o
[ADR 0014](adr/0014-ordem-parcial-eligibility-authority-approval-execucao.md). O
[ADR 0015](adr/0015-stdio-requer-process.md) torna `process` obrigatório para
transportes stdio.

## Guias e referências CURRENT

| Documento | Responsabilidade |
| --- | --- |
| [arquitetura-execucao.md](arquitetura-execucao.md) | overview do fluxo e navegação para contratos |
| [plataforma-standalone.md](plataforma-standalone.md) | escopo, jornadas e limites do produto |
| [operacao-standalone.md](operacao-standalone.md) | instalação, CLI, paths, config e migração |
| [skills.md](skills.md) | referência dos adapters builtin |
| [agente-codigo.md](agente-codigo.md) | domínio de código e ChangeSet/validation |
| [multitarefa.md](multitarefa.md) | TaskGraph e scheduler local |
| [modelos-providers.md](modelos-providers.md) | configuração operacional de providers |
| [guia-extensao.md](guia-extensao.md) | localização de mudanças e workflow de extension |
| [testes.md](testes.md) | pirâmide de testes e gates |
| [estrutura-diretorios.md](estrutura-diretorios.md) | árvore lógica |
| [arquivos-raiz.md](arquivos-raiz.md) | arquivos da raiz e pontos canônicos |
| [perfil-hardware.md](perfil-hardware.md) | profile de ambiente limitado, não contrato universal |
| [legado.md](legado.md) | inventário CURRENT de compatibilidade/deprecação |
| [pre-v1-freeze.md](pre-v1-freeze.md) | checkpoint arquitetural permanente do PRE-V1 |
| [EstruturaProjeto.md](../EstruturaProjeto.md) | mapa secundário do código; este índice prevalece para ownership documental |

## ADRs

Os [ADRs 0001–0012](adr/) registram pacote, visão standalone, bootstrap/paths,
stdio/Windows, catálogo/configuração/bootstrap de extensions e contexto/views
de planning. O [ADR 0013](adr/0013-fronteira-canonica-authority-approval-invocacao.md)
permanece aceito salvo sua ordem total de guards, parcialmente substituída pelo
[ADR 0014](adr/0014-ordem-parcial-eligibility-authority-approval-execucao.md).
[ADR 0015](adr/0015-stdio-requer-process.md) registra a exigência de `process`
para transportes stdio.

## Registros históricos

Estes arquivos preservam evidência e planos de fases anteriores; consulte os
primary homes para comportamento vigente:

- [gate-2.7b-authority-invocation.md](gate-2.7b-authority-invocation.md) — closed milestone;
- [gate-2.7b-self-review.md](gate-2.7b-self-review.md) — closed milestone;
- [marco-1-safe-execution-closure.md](marco-1-safe-execution-closure.md) — closed milestone;
- [phase_0_4_implementation_report.md](phase_0_4_implementation_report.md) — historical report;
- [plano-conclusao-fases-0-4.md](plano-conclusao-fases-0-4.md) — historical runbook;
- [plano-continuacao-fases-0-4.md](plano-continuacao-fases-0-4.md) — historical runbook.

`legado.md` não está nessa classe: ele é um inventário CURRENT de aliases e
condições de retirada.

## Routing de atualização

| Se alterar… | Atualize… |
| --- | --- |
| contratos/identidades/lifecycle core | `agent/core.md` |
| semantics de planning/discovery/replan | `agent/planning.md` |
| composition/executor/result consumption | `agent/orchestration.md` |
| authority/approval/eligibility/trust boundary | `agent/security.md`; ADR somente se houver nova decisão durável |
| capability ou exposição de tool | `agent/tools.md`; esta matriz se a linha mudar |
| subprocess/stdio/timeout/cancelamento | `agent/runtime.md` |
| provider/model contract | `agent/llm.md`; `modelos-providers.md` se mudar uso/config |
| memória | `agent/memory.md` |
| eval core/sets/grading | `agent/evaluation.md`; `testes.md` se a estratégia transversal mudar |
| measurement/report/export | `agent/reporting.md` |
| extension developer/admin workflow | `guia-extensao.md` |
| install/startup/CLI/paths | `operacao-standalone.md` e, se afetar escopo, `plataforma-standalone.md` |
| observabilidade/inspector/replay/export/redaction | `observability-inspector.md` |
| TaskGraph/scheduler | `multitarefa.md` e `agent/planning.md` |
| layout | `estrutura-diretorios.md`; `arquivos-raiz.md` para arquivos da raiz e pontos canônicos |

## Estado de maturidade

```text
Execution and planning core = CLOSED
Evaluation core = GREEN LOCAL
Real-model acceptance = GATED / NOT RUN
Additional evaluation work = NOT COMPLETED
Standalone V1 = NOT YET DECLARED
```
