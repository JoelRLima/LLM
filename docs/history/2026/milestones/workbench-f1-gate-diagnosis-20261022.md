# Diagnóstico de gates — Workbench F1

Data: 22/10/2026. Base observada: branch `feat/agent-workbench`, HEAD
`9b5a2ddc2645430142e3c1ecaf6ef95c41e40406`. Este diagnóstico foi registrado
antes das correções propostas abaixo; logs originais estão em
`prototypes/workbench-comparison/evidence/f1-integration-audit-20261022/`.

## W21

O gate retorna FAIL com 16 mensagens `AUTHORITY: W22 module has no unique owner
classification`, uma para `llm_agent.interactive` e cada módulo Python do
pacote. A projeção em `scripts/w21_architecture/source.py` já mapeia o namespace
legado `agent/interactive` ao novo `llm_agent.interactive`, mas
`quality/architecture_w22_policy.json` não classifica o pacote. O grafo congelado
reporta zero violações-alvo, zero novas, zero reintroduzidas e mantém o hash da
projeção do baseline.

Isso é uma política de owner incompleta após a extração compartilhada, não uma
violação de dependência. `InteractiveSession`, controller, disponibilidade e
mailbox são neutros de UI e coordenam os use cases de `llm_agent.application`;
não importam Tk nem CLI. Uma simulação em memória classificando `interactive`
como `Application` passou W21 com zero erros de autoridade e zero violações de
aresta. A atualização proposta é acrescentar `interactive` à família
`Application` em `quality/architecture_w22_policy.json` e
`quality/architecture_current_policy.json`. Isso registra o owner arquitetural
real do núcleo extraído; não altera baselines, exceções ou direções autorizadas.

## CURRENT

O gate reporta os mesmos 16 módulos sem owner e duas mensagens adicionais:

- `built-in CLI action registry has no _default_bindings function`;
- `built-in CLI handler declarations do not exactly match graph edges (0 targets, 5 edges)`.

`quality/architecture_current_policy.json` identifica corretamente o ponto de
import dinâmico em `llm_agent.interfaces.cli.action_registry.resolve_handler`,
mas a implementação do verificador supõe que `_default_bindings` fique nesse
módulo. A extração moveu a declaração única para
`llm_agent.interactive.commands._default_bindings`; o adapter CLI importa esse
registro (`_core.DEFAULT_CLI_ACTION_REGISTRY._bindings`) e continua resolvendo
os cinco módulos de handlers na camada CLI. Os handlers não foram movidos para o
núcleo compartilhado. A simulação com o owner `Application` deixou somente essas
duas mensagens, sem violação owner-edge.

As duas mensagens são uma suposição de localização desatualizada do checker após
a extração. A correção proposta é fazê-lo seguir a importação explícita do
adapter até a declaração compartilhada, e continuar comparando os mesmos cinco
alvos com as arestas dinâmicas originadas pelo resolver CLI. Não é necessário
alterar o contrato de registro, o catálogo de ações ou as direções de ownership.

## Ruff amplo

`ruff check src tests scripts prototypes/workbench-comparison` encontra quatro
erros em dois arquivos adicionados à árvore F1:

| Arquivo | Regra | Causa |
| --- | --- | --- |
| `prototypes/workbench-comparison/test_pre_pilot.py` | I001 | Falta de linha em branco após imports. |
| mesmo arquivo, linhas 12 e 23 | E701 (2 ocorrências) | `return` e chamada de loop na mesma linha do controle. |
| `prototypes/workbench-comparison/evidence/cli-reuse-20261022/probe_neutral_reuse.py` | I001 | Imports locais dentro do guard estão agrupados em formatação não canônica. |

São violações de estilo reais em scripts de evidência da árvore acumulada, não
falhas de produção. A correção proposta é somente formatar esses pontos. O Ruff
também avisa que não consegue ler diretórios pytest temporários protegidos pelo
Windows (`Acesso negado`, WinError 5); são resíduos de execuções anteriores,
foram preservados e não terão permissões alteradas. A reexecução ampla excluirá
somente esses diretórios temporários inacessíveis, mantendo os arquivos F1 e
evidências legíveis no escopo.

## Demais gates observados

W17 passa antes das mudanças. As correções planejadas não mudam sua superfície.
Os testes F1, CLI, Tk e disponibilidade serão repetidos após as alterações; os
logs originais e pós-correção ficarão separados no diretório de evidências.
