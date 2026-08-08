# Gate 2.7b — authority, approval e invocation lifecycle

O fluxo canônico do runtime standalone é:

```text
ToolInvocationRequest
  → binding do RuntimeSnapshotIdentity
  → descriptor/origin do ToolRegistry
  → ApplicationAuthoritySnapshot
  → TaskAuthoritySnapshot
  → grants/capabilities
  → schema
  → ApprovalPort
  → ToolInvocationGateway
  → adapter
  → ToolResult/evento
```

Extensions exigem grant da aplicação e um `TaskAuthoritySnapshot` explícito.
`None` significa ausência e falha fechado; `TaskAuthoritySnapshot()` é um
snapshot vazio explícito e só autoriza uma extension que não exija capabilities.
Builtins preservam a política histórica sem depender de grants de extensions.

O gateway nunca usa `active_skills`, persona, prompt ou output do planner como
concessão. Esses dados podem restringir visibilidade, mas a origem e as
capabilities são derivadas do descriptor e dos snapshots imutáveis. O registry
e a authority precisam pertencer ao mesmo bootstrap/workspace.

Negações retornam `ToolResult` correlacionado pelo mesmo `invocation_id` e
emitem `tool_denied` sem argumentos ou resultado integral. Effects emitem
`approval_requested`; aprovação positiva emite `approval_approved`; rejeição e
falha não chamam o adapter. Invocações aprovadas emitem exatamente um
`tool_start` e um `tool_end`. ID divergente no resultado é
`PROTOCOL_ERROR`.

O caminho canônico é montado por `AgentApplication`; seu `task_authority`
opcional deve vir de uma camada confiável. A aplicação não fornece esse
snapshot por padrão, mantendo extensions invisíveis no planning e negadas na
execução. `LegacyToolInvoker`, o loader legado de registry e chamadas diretas
ao adapter permanecem pendências de hardening do Gate 2.7c; o `ToolRegistry`
direto já bloqueia extensions por fail-closed.
