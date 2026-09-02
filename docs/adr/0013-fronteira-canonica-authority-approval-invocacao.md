# ADR 0013 — Fronteira canônica de authority, approval e invocation

- Status: parcialmente substituído pelo ADR 0014
- Substituição parcial: somente a prescrição de ordem total do item 5; as
  decisões sobre authority, approval, identidade, grants, gateway e
  `invocation_id` permanecem aceitas.
- Escopo: Gate 2.7b
- Data: 2026-08-08

> **ATUALIZAÇÃO PÓS-W8 (2026-09-02).** O parágrafo sobre o Gate 2.7c abaixo é
> uma fotografia da decisão histórica e não descreve o estado operacional atual.
> A Wave 8 publicou as disposições de ownership e compatibilidade, mantendo a
> boundary stdio explícita e o `ToolInvocationGateway` como gateway canônico.
> Para o estado atual, consulte o [hub técnico](../README.md) e o
> [checkpoint PRE-V1](../pre-v1-freeze.md).

## Contexto

O bootstrap publica um `ToolRegistry` congelado e uma
`ApplicationAuthoritySnapshot` derivados do mesmo `RuntimeSnapshotIdentity`.
Antes deste gate, o gateway aceitava filtros fornecidos pelo caller, criava um
`ToolInvocation` diretamente e não usava `ToolInvocationRequest`. Isso
permitia que uma extension com o nome correto fosse executada sem autoridade
de tarefa ou grant da aplicação.

## Decisão

1. `ToolInvocationGateway` é a fronteira canônica para chamadas do runtime
   standalone. A forma histórica `run(name, args)` continua apenas como
   wrapper que constrói um `ToolInvocationRequest` imutável; callers confiáveis
   também podem fornecer o request diretamente.
2. A origem é derivada do `ToolDescriptor` do registry. Claims do caller,
   `active_skills`, persona, prompt e metadados do planner não concedem acesso.
   `active_skills` e `allowed_capabilities` só podem restringir chamadas
   históricas.
3. Builtins preservam o comportamento sem grants de extension. Extensions
   exigem registry/authority do mesmo runtime, grant da aplicação,
   `TaskAuthoritySnapshot` não nulo e todas as capabilities exigidas presentes
   nos dois snapshots. `None` continua diferente de snapshot explicitamente
   vazio; não há default permissivo.
4. `TaskAuthoritySnapshot` pode carregar opcionalmente o mesmo
   `RuntimeSnapshotIdentity`, permitindo rejeitar uma autoridade de tarefa de
   outro bootstrap/workspace. A ausência dessa identidade mantém a autoridade
   como um snapshot lógico fornecido por uma camada confiável, não como uma
   permissão automática.
5. A ordem é request estrutural → binding/runtime → descriptor/origem →
   application authority → task authority/grants → filtros restritivos →
   schema → approval → adapter. Approval nunca amplia capabilities.
6. Toda decisão terminal retorna `ToolResult` com o mesmo `invocation_id` e
   emite `tool_denied` para negações. Approval emite
   `approval_requested`/`approval_approved`; falhas de approval são
   `APPROVAL_FAILED`. O caminho de adapter emite um único par
   `tool_start`/`tool_end`, sem payload integral de argumentos.
7. O gateway valida o tipo e o `invocation_id` do resultado. Um adapter que
   retorne ID diferente produz `PROTOCOL_ERROR` e não é promovido a sucesso.
   `ToolRegistry.invoke` bloqueia execução direta de extensions; apenas a
   fronteira interna do gateway pode alcançar o adapter materializado.

## Compatibilidade e limites

O protocolo stdio `1.0`, o launcher/cleanup do Gate 1, o registry congelado e
os contratos de planning do Gate 2.6 permanecem inalterados. A aplicação
standalone continua sem `TaskAuthoritySnapshot` por padrão, portanto
extensions seguem invisíveis no planning e negadas no invocation até uma
camada confiável fornecer esse snapshot.

`LegacyToolInvoker`, `load_tool_registry` e chamadas diretas a
`StdioToolAdapter` permanecem APIs legadas/low-level fora do composition root;
o registry direto já falha fechado para extensions. A migração/hardening
global de callers legados, ownership de timeout/deadline/cancelamento e
prevenção de bypass fora do runtime canônico ficam deferidos ao Gate 2.7c.

## Consequências

Planning continua sendo uma projeção e não uma concessão de execução. Uma
extension apresentada ou validada não executa sem authority canônica. Eventos
de invocation são correlacionáveis por ID e não gravam argumentos completos
por padrão. A decisão não cria `attempt_id`, retry novo ou um protocolo
stdio alternativo.
