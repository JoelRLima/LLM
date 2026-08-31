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
- checkpoint v2 persiste plano/estado retomável e a `TaskDefinitionRef`
  compacta, mas não os corpos normativos nem a `TaskAuthoritySnapshot` de
  capabilities.

Identidades têm escopos diferentes e não são intercambiáveis. Restaurar um
checkpoint exige resolver novamente a definição durável, revalidar o plano e
reconstruir policy/authority de capabilities atuais.

Evidence de origem canônica permanece distinta de resumo, cache ou projeção
lossy. Um resultado derivado pode orientar a próxima decisão, mas não pode
criar nova authority, intenção de efeito, obrigação durável ou evidência exata;
checkpoint/reentry repete essa validação em vez de promover a fidelidade.

## Task Definition e tipos de authority

`TaskContract` e `TaskSpec` formam a autoridade normativa da tarefa. O
Contract fixa objetivo, requisitos, restrições, invariantes e critérios de
conclusão; a Spec é vinculada ao digest exato do Contract e descreve fases,
dependências e evidências. Ambos são value objects tipados, limitados e
imutáveis depois de publicados no repositório da workspace.

`TaskDefinitionRef` é apenas o binding determinístico compacto: task id,
versões, digests, estado da definição e, quando presente, `active_phase_id`.
Ela não contém os corpos da autoridade. `TaskAuthoritySnapshot`, por sua vez,
é authority de capabilities para tools/extensions; não é sinônimo de Task
Definition e não é criada pelo Contract ou pela Spec.

O `Plan` continua sendo o owner executável de steps e tool calls. A Task
Definition delimita normativamente o que deve ser realizado, mas não é um
plano executável, grant ou approval. `TaskProgress`, avanço autônomo de fases,
retry/replan de longo horizonte e policy dinâmica de runtime não fazem parte
do contrato atual.

## Lifecycle suportado

```text
create application
→ initialize workspace state
→ receive non-trivial objective and create root_task_id
→ compile/admit and persist immutable TaskContract
→ compile/admit and persist immutable TaskSpec bound to the Contract
→ bind complete TaskDefinitionRef
→ materialize trusted authority through ContextManager
→ create task/planning snapshot
→ plan and execute through gateways
→ persist/report terminal outcome
→ close owned resources
```

`AgentApplication.create()` é a composition root suportada. `close()` encerra
recursos da aplicação; falhas de task não transferem ownership ao modelo.
Cancelamento é sinalizado por `CancellationToken` e interpretado pelos pontos de
execução que o consultam; ele não constitui preempção universal de Python.
Execução aninhada usa um `TaskExecutionContext` filho no mesmo ownership tree:
budget, cancelamento, gates de modelo/processo e limites efetivos continuam
pertencendo ao contexto pai.

Na retomada, o checkpoint restaura somente a referência compacta. O repositório
da mesma workspace deve resolver Contract e Spec e validar task id, versões,
digests, estado e `active_phase_id` antes da materialização confiável. Binding
ausente, fase inválida, corrupção, divergência de workspace ou definição
incompleta impedem a continuação normal.

## Workspace e recovery

`WorkspaceManager` restringe paths, cria restore points e aplica rollback no
fluxo de mudança suportado. Checkpoints, backups de memória e restore points têm
propósitos diferentes. Nenhum deles equivale a transação distribuída ou sandbox
de sistema operacional.
O commit de observação canônica publica state/history como uma unidade; falha
de validação não deixa append parcial nem autoriza sucesso falso. Um efeito
físico acompanhado de falha de commit permanece reportado como falha de commit.

## Boundary ScannerCore

`agent/security/security_patterns.py` e `security_scanner.py` implementam
checagens locais usadas pelo Agent. Eles **não** são o ScannerCore científico do
TCC e não devem ser apresentados como implementação daquela arquitetura externa.
