# Gate 2.7b — authority, approval e invocation lifecycle

> **STATUS: CLOSED MILESTONE RECORD.** Preserva a evidência do Gate 2.7b. Para o
> contrato CURRENT, use [security](agent/security.md); a ordem total abaixo foi
> parcialmente substituída pelo [ADR 0014](adr/0014-ordem-parcial-eligibility-authority-approval-execucao.md).

O fluxo canonico do runtime standalone e:

```text
ToolInvocationRequest
  -> binding do RuntimeSnapshotIdentity
  -> descriptor/origin do ToolRegistry
  -> ApplicationAuthoritySnapshot
  -> TaskAuthoritySnapshot
  -> grants/capabilities
  -> schema
  -> ApprovalPort
  -> ToolInvocationGateway
  -> adapter
  -> ToolResult/evento
```

Extensions exigem grant da aplicacao e um `TaskAuthoritySnapshot` explicito.
`None` significa ausencia e falha fechado; `TaskAuthoritySnapshot()` e um
snapshot vazio explicito e so autoriza uma extension que nao exija capabilities.
Builtins preservam a politica historica sem depender de grants de extensions.

O gateway nunca usa `active_skills`, persona, prompt ou output do planner como
concessao. Esses dados restringem visibilidade, mas a origem e as capabilities
sao derivadas do descriptor e dos snapshots imutaveis. O registry e a
authority precisam pertencer ao mesmo bootstrap/workspace.

Negacoes retornam `ToolResult` correlacionado pelo mesmo `invocation_id` e
emitem `tool_denied` sem argumentos ou resultado integral. Effects emitem
`approval_requested`; aprovacao positiva emite `approval_approved`; rejeicao e
falha nao chamam o adapter. Invocacoes aprovadas emitem exatamente um
`tool_start` e um `tool_end`. ID divergente no resultado e `PROTOCOL_ERROR`.

O gateway reserva cada `invocation_id` somente enquanto a tentativa concreta
esta ativa. Uma tentativa concorrente com o mesmo ID e rejeitada antes do
adapter. Depois da terminalidade, uma nova requisicao concreta pode usar um
novo UUID ou reutilizar o ID sem que isso crie semantica de retry, deduplicacao
historica ou exactly-once. Timeout e cancelamento possuem resultado terminal
proprio; a conclusao tardia de um worker nao publica outro evento terminal. O
gateway nao mantem conjunto historico permanente de IDs.

`ToolExecutor` nao reconstrói um caminho model-actionable via
`LegacyToolInvoker`. Construcao direta com builtins usa o gateway canonico;
skills customizadas so sao adaptadas quando um `SkillRegistry` explicito
fornece descriptor, capabilities, schema e policy. Registro tardio sem
metadata nao torna uma skill executavel pelo modelo e falha fechado.

O caminho principal e montado por `AgentApplication`; seu `task_authority`
opcional deve vir de uma camada confiavel. A aplicacao nao fornece esse
snapshot por padrao, mantendo extensions invisiveis no planning e negadas na
execucao. Chamadas low-level a `LegacyToolInvoker`, registry ou adapters sao
compatibilidade interna e permanecem fora da superficie model-actionable.
