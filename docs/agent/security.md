# Segurança, authority e trust boundaries

> **STATUS: CURRENT — PRIMARY HOME.** Este documento define o contrato atual de
> authority, grants, approval, eligibility e enforcement. ADRs registram por
> que as decisões foram tomadas; em especial, consulte os
> [ADRs 0013](../adr/0013-fronteira-canonica-authority-approval-invocacao.md) e
> [0014](../adr/0014-ordem-parcial-eligibility-authority-approval-execucao.md).

## Invariantes

```text
planner = guidance
gateway = enforcement
authority antes de approval e de execução
approval != authority
discovery, binding, active_skills e output de tool != authority
todos os guards obrigatórios devem passar antes do adapter
```

Approval pode consentir com um efeito já autorizado; nunca cria grant,
capability ou acesso a outra tool. Texto do modelo, descrição de extension,
resultado de tool e conteúdo do workspace são dados não confiáveis.

## Conceitos e fontes

- `ApplicationAuthoritySnapshot`: snapshot confiável criado no bootstrap, com
  a mesma `RuntimeSnapshotIdentity` do registry e os grants persistidos de cada
  extension.
- `TaskAuthoritySnapshot`: authority explícita fornecida por uma camada
  confiável para a tarefa. `None` é ausência, não um grant vazio.
- policy de persona: restrição estática de capabilities dos builtins; não
  aumenta task authority.
- `active_skills`: allowlist de eligibility/apresentação. É derivada da persona,
  do registry e do planning context e somente reduz o conjunto publicado.
- binding: valida coerência entre registry, descriptor, origem, workspace e
  identidade do snapshot. Não seleciona nem cria authority.
- `ApprovalPort`: decisão `approved`, `rejected` ou `required` para efeitos.

Builtins preservam a política histórica: `check_authority()` os reconhece como
builtins e não exige grants de extension. Eles continuam sujeitos à
eligibility, capabilities de persona, schema e approval de efeitos. Extensions
exigem application grant, `TaskAuthoritySnapshot` e todas as capabilities em
ambos os snapshots.

## Pipeline CURRENT

```text
ToolInvocationRequest
→ descriptor e binding/applicability
→ active-skill eligibility
→ authority e grants
→ outros filtros de capability
→ schema
→ approval, quando há write/vcs_write/process/network/package_install
→ adapter
→ ToolResult terminal
```

Essa é a ordem atual, não uma promessa de ordem total eterna. O contrato
normativo é parcial: eligibility pode rejeitar cedo, mas authority sempre
precede approval e execução. Se dois guards rejeitariam a mesma invocação, a
ordem pode mudar o reason code observado; essa precedência é diagnóstica e não
é estável salvo garantia explícita.

## Enforcement model-actionable

`ToolExecutor` exige `ToolInvocationGateway`; se o gateway não estiver montado,
falha e não chama `LegacyToolInvoker`. O gateway deriva origem e capabilities
do `ToolDescriptor`, valida o request e chama somente a entrada privada do
registry após os checks. `ToolRegistry.invoke()` bloqueia extensions diretas.

APIs diretas de skills, adapters, registry e `LegacyToolInvoker` existem para
testes, administração ou compatibilidade. Elas não são a superfície suportada
para decisões do modelo e não devem ser apresentadas como um segundo gateway.

## Falhas e observabilidade

Unknown tool produz `UNAVAILABLE`. Binding, authority, capabilities e approval
negados produzem status terminais correlacionados pelo mesmo `invocation_id` e
não chamam o adapter. Falha do provider de approval é fail-closed. O gateway
emite `tool_denied` com reason code; requests aprovados emitem
`tool_start`/`tool_end`. Logging e state/reporting legados ainda podem conter
mais dados que esses eventos reduzidos; redaction total não é garantida.

## Garantias e não garantias

O runtime garante checks restritivos antes do efeito, correlação de invocation
e ausência de promoção de falha para sucesso. Não fornece sandbox forte de
filesystem ou rede, proteção universal contra TOCTOU, isolamento de código
hostil, exactly-once histórico ou `attempt_id` de retry.

## Scanner interno e TCC

`agent/security/security_patterns.py` contém metadados de padrões e
`security_scanner.py` consolida findings determinísticos já produzidos. Eles
não são o ScannerCore científico do TCC. O LLM Agent e o ScannerCore externo
são sistemas distintos; uma integração futura deve usar a mesma fronteira de
extension, sem importar a arquitetura científica para este repositório.
