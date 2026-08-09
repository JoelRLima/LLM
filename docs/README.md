# Documentação

Índice técnico curado. Para instalação, veja o [README principal](../README.md),
para a visão canônica das responsabilidades, [EstruturaProjeto.md](../EstruturaProjeto.md),
e para os padrões de código, o [guia de contribuição](../CONTRIBUTING.md).

## Como ler o estado atual

Para entender o runtime atual, siga esta ordem:

1. [plataforma-standalone.md](plataforma-standalone.md) — escopo e jornadas do assistente;
2. [arquitetura-execucao.md](arquitetura-execucao.md) — gateway, planner, estado e retomada;
3. [skills.md](skills.md) — capabilities realmente model-actionable;
4. [marco-1-safe-execution-closure.md](marco-1-safe-execution-closure.md) — garantias e limites de execução;
5. [testes.md](testes.md) — evidências locais e gates de qualidade.

### Matriz de capabilities

| Capability | Estado atual |
| :--- | :--- |
| gateway canônico de tools | IMPLEMENTADO |
| authority, capabilities e approval | IMPLEMENTADO |
| extensions stdio 1.0 | IMPLEMENTADO |
| ShellSkill restrita | IMPLEMENTADO |
| metadados de histórico Git local | IMPLEMENTADO |
| `git status` e `git diff` model-actionable | REDUZIDO AWAY |
| shell arbitrário | NÃO FORNECIDO |
| execução paralela local | IMPLEMENTADO |
| sandbox forte de filesystem/rede | NÃO FORNECIDA |

Os relatórios e planos `phase_0_4_implementation_report.md`,
`plano-conclusao-fases-0-4.md` e `plano-continuacao-fases-0-4.md` são históricos
ou runbooks de fases anteriores; não substituem os documentos de estado acima.
ADRs preservam decisões no contexto em que foram tomadas e não devem ser
reescritos retroativamente.

| Documento | Conteúdo |
| :--- | :--- |
| [estrutura-diretorios.md](estrutura-diretorios.md) | Árvore de Diretórios do Projeto |
| [arquivos-raiz.md](arquivos-raiz.md) | Detalhamento dos Arquivos da Raiz (Root Files) |
| [skills.md](skills.md) | Mapeamento de Ferramentas (Skills) em `agent/skills/` |
| [testes.md](testes.md) | A Suíte de Testes (tests/) |
| [guia-extensao.md](guia-extensao.md) | Guia de Extensão e Solução de Problemas (Onde Alterar?) |
| [arquitetura-execucao.md](arquitetura-execucao.md) | Fluxo canônico, estados, checkpoint e retomada |
| [modelos-providers.md](modelos-providers.md) | ModelGateway, adapters, capacidades e configuração |
| [perfil-hardware.md](perfil-hardware.md) | Defaults para GTX 1070/8 GB e operação de baixo consumo |
| [agente-codigo.md](agente-codigo.md) | Descoberta, análise, ChangeSet, validação e workflows |
| [multitarefa.md](multitarefa.md) | TaskGraph, isolamento, checkpoint e scheduler de recursos |
| [legado.md](legado.md) | Fachadas compatíveis, consumidores e condições de retirada |
| [plataforma-standalone.md](plataforma-standalone.md) | Arquitetura-alvo, invariantes e jornadas do assistente standalone |
| [operacao-standalone.md](operacao-standalone.md) | Instalação, CLI, paths, configuração, aprovação, migração, lifecycle e gate do wheel |
| [plano-conclusao-fases-0-4.md](plano-conclusao-fases-0-4.md) | Runbook executável, tarefas, dependências e critérios para concluir as fases 0–4 |
| [plano-continuacao-fases-0-4.md](plano-continuacao-fases-0-4.md) | Continuação focada somente nas pendências confirmadas das fases 0–4 |
| [adr/0001-limites-do-pacote.md](adr/0001-limites-do-pacote.md) | Decisão sobre pacote, interfaces e aliases da raiz |
| [adr/0002-visao-do-assistente-standalone.md](adr/0002-visao-do-assistente-standalone.md) | Visão, vocabulário, trust model, fronteiras e definição verificável da v1 |
| [adr/0003-bootstrap-paths-e-ciclo-de-vida-standalone.md](adr/0003-bootstrap-paths-e-ciclo-de-vida-standalone.md) | Decisão sobre composição, paths, configuração, workspace, migração e ciclo de vida standalone |
| [adr/0004-invocation-id-protocolo-stdio-1-0.md](adr/0004-invocation-id-protocolo-stdio-1-0.md) | `invocation_id` obrigatório no protocolo stdio 1.0, framing e respostas tardias |
| [adr/0005-launcher-interno-contencao-stdio-windows.md](adr/0005-launcher-interno-contencao-stdio-windows.md) | launcher interno Windows-only, associação ao Job antes da extension e status privado |
| [adr/0013-fronteira-canonica-authority-approval-invocacao.md](adr/0013-fronteira-canonica-authority-approval-invocacao.md) | fronteira canÃ´nica de authority, approval e lifecycle de invocation |
| [gate-2.7b-authority-invocation.md](gate-2.7b-authority-invocation.md) | authority, approval e lifecycle de invocation |
| [marco-1-safe-execution-closure.md](marco-1-safe-execution-closure.md) | garantias de execucao, terminalidade, stdio e shell do Marco 1 |
| [gate-2.7b-self-review.md](gate-2.7b-self-review.md) | implementacao, autoauditoria e evidencias do Gate 2.7b |
| [agent/core.md](agent/core.md) | Arquivos de `agent/` |
| [agent/llm.md](agent/llm.md) | Arquivos de `agent/llm` |
| [agent/memory.md](agent/memory.md) | Arquivos de `agent/memory` |
| [agent/planning.md](agent/planning.md) | Arquivos de `agent/planning` |
| [agent/reporting.md](agent/reporting.md) | Arquivos de `agent/reporting` |
| [agent/security.md](agent/security.md) | Arquivos de `agent/security` |
| [agent/orchestration.md](agent/orchestration.md) | Arquivos de `agent/orchestration` |
| [agent/tools.md](agent/tools.md) | Arquivos de `agent/tools` |
| [agent/runtime.md](agent/runtime.md) | Arquivos de `agent/runtime` |
| [agent/evaluation.md](agent/evaluation.md) | Arquivos de `agent/evaluation` |
| [agent/health.md](agent/health.md) | Arquivos de `agent/health` |

---

Para o estado atual, use os guias acima, o README e `EstruturaProjeto.md`.
