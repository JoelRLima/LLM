# Core contracts e lifecycle

> **STATUS: CURRENT — PRIMARY HOME.** Composition e fluxo de execução ficam em
> [orchestration.md](orchestration.md); enforcement em
> [security.md](security.md).

## Responsabilidade

Os módulos na raiz de `agent/` definem contratos compartilhados e casos de uso
que conectam planning, execução, memória, reporting e workspace. Eles não devem
absorver detalhes de provider, adapter stdio ou políticas específicas de skill.

## Identidades e estado

- cada tarefa recebe identidade própria e um `AgentState` com objetivo, plano,
  histórico, eventos e resposta final;
- cada invocation recebe `invocation_id`; extensions também devem devolvê-lo no
  protocolo stdio;
- `RuntimeSnapshotIdentity` vincula registry e application authority ao mesmo
  bootstrap;
- `ExecutionState`/`StepExecutionRecord` registra transições e terminalidade de
  steps; `ToolResult` é o resultado normalizado consumido pelo executor;
- checkpoint v2 persiste plano/estado retomável, mas não authority efetiva.

Identidades têm escopos diferentes e não são intercambiáveis. Restaurar um
checkpoint exige revalidar o plano e reconstruir policy/authority atuais.

## Lifecycle suportado

```text
create application
→ initialize workspace state
→ create task/planning snapshot
→ plan and execute through gateways
→ persist/report terminal outcome
→ close owned resources
```

`AgentApplication.create()` é a composition root suportada. `close()` encerra
recursos da aplicação; falhas de task não transferem ownership ao modelo.
Cancelamento é sinalizado por `CancellationToken` e interpretado pelos pontos de
execução que o consultam; ele não constitui preempção universal de Python.

## Workspace e recovery

`WorkspaceManager` restringe paths, cria restore points e aplica rollback no
fluxo de mudança suportado. Checkpoints, backups de memória e restore points têm
propósitos diferentes. Nenhum deles equivale a transação distribuída ou sandbox
de sistema operacional.

## Boundary ScannerCore

`agent/security/security_patterns.py` e `security_scanner.py` implementam
checagens locais usadas pelo Agent. Eles **não** são o ScannerCore científico do
TCC e não devem ser apresentados como implementação daquela arquitetura externa.
