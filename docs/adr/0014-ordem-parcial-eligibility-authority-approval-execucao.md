# ADR 0014 — Ordem parcial entre eligibility, authority, approval e execução

- Status: aceito
- Data: 2026-08-09
- Substitui parcialmente: ADR 0013, somente quanto à ordem total dos checks

## Contexto

O ADR 0013 descreveu o gateway com uma ordem total: authority e grants antes
de todos os demais filtros restritivos. O runtime evoluiu para avaliar
`binding` e a eligibility por `active_skills` antes de `check_authority()`.

A auditoria do runtime confirmou que esses dois guards são estritamente
redutores:

- o binding compara registry, descriptor e identidade do snapshot; ele não
  cria grants, capabilities ou authority;
- `active_skills` é uma allowlist de apresentação derivada do registry e da
  policy de persona; ela pode remover tools, mas não registrar tools, criar
  grants ou evitar a checagem subsequente de authority;
- `ToolExecutor`, a superfície model-actionable, exige
  `ToolInvocationGateway` e não faz fallback para o invocador legado;
- extensions diretas pelo registry são negadas antes do adapter.

O problema era, portanto, a força excessiva da ordem textual do ADR, não uma
ampliação de privilégio no runtime.

## Decisão

O contrato normativo é uma ordem parcial:

```text
eligibility / applicability pode rejeitar cedo
authority / grants deve preceder approval
authority / grants deve preceder execução
schema e todos os demais guards obrigatórios devem passar antes da execução
approval não cria nem amplia authority
```

Binding, descoberta, disponibilidade, apresentação ao planner, persona,
`active_skills`, prompt e output de tool não são fontes de authority.

A ordem da implementação CURRENT é:

```text
request e descriptor
→ binding/applicability
→ active-skill eligibility
→ authority/grants
→ filtros restritivos restantes
→ schema
→ approval, quando aplicável
→ adapter/execução
```

Essa sequência descreve o código atual. O contrato durável é apenas a ordem
parcial acima; filtros independentes podem ser reorganizados desde que
continuem estritamente restritivos e todos ocorram antes da execução.

## Reason codes

Quando mais de um guard rejeitaria a mesma invocação, o primeiro guard
avaliado determina o reason code observado. Essa precedência é diagnóstica e
não é estável, salvo quando outro contrato a garantir explicitamente. A
auditoria não encontrou consumer de execução ou segurança que altere authority,
approval ou efeito conforme essa precedência.

## Consequências

- eligibility pode short-circuitar antes de authority sem ampliar privilégios;
- uma tool elegível ainda pode ser negada por authority, capability, schema ou
  approval;
- uma tool descoberta ou apresentada não ganha permissão de execução;
- ausência de authority não pode ser compensada por approval;
- o ADR 0013 permanece aceito em todas as decisões não relacionadas à ordem
  total entre guards restritivos.

## Não objetivos

Este ADR não altera código, capabilities, approval, planners, descoberta,
protocolo stdio, reason codes ou fontes de authority. Também não congela uma
ordem desnecessária entre checks independentes.
