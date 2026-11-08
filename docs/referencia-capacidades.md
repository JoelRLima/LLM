# Capacidades implementadas e limites

> Referência CURRENT. Exposição ao modelo não equivale a permissão de execução. Para objetivos e critérios de produto, consulte [Propósito](proposito.md).

## Matriz CURRENT de capabilities

Registro de manutenção: [modernizações de 27/10/2026](history/2026/milestones/modernizacoes-20261027.md).
Os contratos vigentes estão reunidos no [índice principal](README.md#contratos-do-agent).

“Implementada” significa que há código; “model-actionable” significa que pode
aparecer na view do planner/modelo. Em todos os casos, exposição não equivale a
permissão de executar.

| Capability | Implementada? | Model-actionable? | Exposure / boundary | Primary docs |
| --- | --- | --- | --- | --- |
| Read | sim | sim | workspace; persona/view e gateway | [tools](agent/tools.md) |
| Search/list | sim | sim | busca/listagem confinada ao workspace | [tools](agent/tools.md) |
| Modify + validate | sim | sim | `code_task` → `ChangeSet` → `ProjectValidator` | [tools](agent/tools.md), [agente de código](agente-codigo.md) |
| Low-level writer | sim | **não** | `file_writer`; admin/legado, excluído das views | [tools](agent/tools.md) |
| Shell do modelo | sim, reduzida | sim, restrito | somente `ruff check`, `git log`, `tree`; nunca recebe um shell genérico | [tools](agent/tools.md) |
| Comando local da pessoa | sim | **não** | `/exec` ou texto enviado com LLM desligada; um comando pontual no workspace, sob a conta do sistema | [Workbench](agent-workbench.md), [uso local](guides/uso-local-e-llm.md) |
| Git | sim, reduzida | sim via skills permitidas | somente histórico local (`log`); status/diff model-actionable removidos | [tools](agent/tools.md) |
| Ruff | sim | sim via ShellSkill | somente `ruff check`; validação, sem `--fix`/config arbitrária | [tools](agent/tools.md) |
| External stdio | sim | condicional | catálogo + workspace grants (incluindo `process`) + task authority + gateway | [tools](agent/tools.md), [runtime](agent/runtime.md) |
| Memory | sim | sim, conforme persona/fluxo | persistente por workspace; sem authority; semântica opcional | [memory](agent/memory.md) |
| MCP | **não fornecido** | não | nenhuma integração MCP no repo | [tools](agent/tools.md) |
| Web | sim (`web_search`) | sim para researcher | rede sujeita a policy/approval; não é browser/MCP | [tools](agent/tools.md) |

`/exec` é uma ação escrita pela pessoa e não aparece na view do planner. No modo
local, o texto sem `/` também é interpretado como comando; a interface desktop
exibe uma confirmação nativa. Cada envio inicia um novo processo de shell no
workspace ativo, com limite de tempo e saída, mas sem sandbox. `cd` não permanece
entre envios; troque o workspace para alterar a pasta inicial.

Não há shell genérico model-actionable, package install model-actionable,
sandbox universal de filesystem/rede nem garantia de execução de qualquer
extension descoberta.

