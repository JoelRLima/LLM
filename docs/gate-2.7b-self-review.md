# Gate 2.7b — implementacao e autoauditoria

## Estado inicial

- Baseline: `8959378f95acf95992d51207d0eb1d1fddb8860a` em `main`.
- `origin/main` coincidia com `HEAD`; worktree e staging estavam limpos.
- Snapshot externo: `C:\tmp\gate27b-preedit-20260808`.
- Nao houve `git add`, commit, push ou operacao Git destrutiva.

## Fluxo canonico

```text
AgentApplication -> ToolInvocationGateway -> ToolInvocationRequest
  -> runtime/registry binding -> descriptor/origin
  -> application authority -> task authority/grants
  -> schema -> ApprovalPort -> adapter -> ToolResult/eventos
```

O bootstrap publica o mesmo `RuntimeSnapshotIdentity` no registry e na
`ApplicationAuthoritySnapshot`. Builtins preservam a politica historica sem
grants de extension. Extensions exigem grant por `extension_id`, registry
congelado e `TaskAuthoritySnapshot` explicito. `None` nao vira snapshot vazio.
Um task snapshot pode declarar a identidade de runtime para rejeitar mistura de
bootstrap/workspace.

## Deliberacao aplicada

`run(name, args)` permanece como wrapper de compatibilidade, mas a fronteira
materializa `ToolInvocationRequest` antes de qualquer decisao observavel. A
origem e derivada do descriptor publicado; `active_skills`, persona, prompt e
metadados do planner nao concedem autoridade. Filtros historicos so podem
restringir uma decisao canonica.

A ordem e request estrutural, binding, descriptor/origem, application
authority, task authority/grants, filtros, schema, approval e adapter. Approval
positiva nao altera capabilities. Negaçoes emitem `tool_denied`; efeitos
emitem `approval_requested` e `approval_approved`; execucao aprovada emite um
unico par `tool_start`/`tool_end`, sem args ou resultado integral. ID divergente
do adapter produz `PROTOCOL_ERROR`.

## Arquivos desta tarefa

- `agent/application.py` e `agent/orchestrator.py`: propagacao explicita de
  task authority e application authority para planning/gateway.
- `agent/skills/policy.py`: descriptors de extension nao entram na lista de
  skills builtin da persona.
- `agent/tools/authority.py` e `agent/tools/contracts.py`: binding de runtime
  opcional no task snapshot e correlacao de task no request.
- `agent/tools/invocation_gateway.py`: boundary de request, authority,
  approval, lifecycle e resultado.
- `agent/tools/invocation_support.py`: checks puros de binding, grants,
  schema e result.
- `agent/tools/tool_registry.py`: invocacao publica de extension falha fechado.
- `tests/unit/tools/test_gate_27b.py`: testes adversariais e de side effects.
- `docs/adr/0013-fronteira-canonica-authority-approval-invocacao.md` e
  `docs/gate-2.7b-authority-invocation.md`: contrato e operacao.

## Bypass inventory

| Caminho | Classificacao | Estado |
| --- | --- | --- |
| AgentApplication -> gateway -> registry -> adapter | CANONICO | Corrigido |
| Security/CLI/plan -> ToolExecutor -> gateway | CANONICO | Corrigido |
| `ToolRegistry.invoke` publico | BYPASS POTENCIAL | Extension bloqueada |
| `_invoke_from_gateway` e `StdioToolAdapter.invoke` direto | LOW-LEVEL | Hardening 2.7c |
| `LegacyToolInvoker` e `load_tool_registry` | LEGADO | Hardening 2.7c |

Nenhum caller de producao do composition root executa extension sem o gateway.
Os hooks low-level restantes nao sao usados por esse caminho e foram
registrados explicitamente como dependencia do Gate 2.7c.

## Autoauditoria adversarial

- Sem application/task authority: `APPLICATION_AUTHORITY_MISSING` ou
  `TASK_AUTHORITY_MISSING`, sem adapter/processo.
- Task authority vazia para capability requerida:
  `TASK_AUTHORITY_DENIED`.
- Approval positiva com authority insuficiente continua negada.
- Approval rejeitada: `APPROVAL_DENIED`, sem `tool_start`.
- Approval com excecao: `APPROVAL_FAILED`, resultado estruturado.
- `vcs_write` e tratado como efeito e exige approval como `write`.
- Runtime/workspace/snapshot divergente: `RUNTIME_MISMATCH`.
- Mesmo nome em snapshots diferentes nao atravessa o binding.
- Resultado com ID divergente: `INVOCATION_ID_MISMATCH`.
- Falha de adapter, schema invalido e timeout produzem resultado e terminal
  observaveis sem duplicacao.
- Planning sem task authority nao apresenta nem executa extension.

Os tres subagentes de autoauditoria nao reproduziram bypass canonico. Eles
confirmaram apenas os hooks privados/low-level ja listados para 2.7c.

## Validacao

- `pytest -q`: **865 passed, 20 skipped**.
- `ruff check .`: **passou**.
- `mypy --platform linux --no-incremental`: **passou em 224 arquivos**.
- `mypy --platform win32 --no-incremental`: **passou em 224 arquivos**.
- `scripts/check_quality.py`: **passou**, complexity debt e oversized modules
  iguais a zero.
- `git diff --check`: **passou**.
- `scripts/verify_installed_package.py --no-build-isolation`: **passou**.
- Wheel semantico fora do checkout: `imports_from_installed_wheel=True` e
  probes de sucesso, denial, binding e registry direto passaram.

## Limitacoes deliberadas

O state recorder e logger historicos ainda podem preservar args/resultados
integrais; redaction de state/report e uma decisao posterior. O gateway
publica apenas reason codes e mensagens bounded; detalhes tecnicos ficam no
logger. O worker de timeout pode
continuar apos o terminal. Nenhuma dessas limitacoes altera protocolo stdio
1.0, cleanup do Gate 1 ou contratos de planning do Gate 2.6.

## Veredito

`LUA SELF-REVIEW: GATE 2.7b CONCLUÍDO NESTA BASELINE`

Gate 2.7b está concluído nesta baseline. Gate 2.7c permanece não iniciado.
