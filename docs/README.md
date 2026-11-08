# Documentação do LLM Agent

> Este é o índice principal. **CURRENT** define produto e contratos vigentes; guias explicam uso; pesquisas guardam análises; histórico preserva decisões e entregas antigas.

## Comece aqui

- [Propósito e critérios para avaliar mudanças](proposito.md)
- [Guia para começar no Workbench](guides/uso-basico.md)
- [Uso local e ativação da LLM](guides/uso-local-e-llm.md)
- [Paridade de sessão entre app e CLI](guides/paridade-app-cli.md)
- [Dados locais, privacidade e retenção](guides/privacidade-dados-locais.md)
- [Fila automatizada de testes](guides/fila-de-testes.md)
- [Pesquisa: CLI unificada, uso local e viabilidade](research/2026/cli-unificada-uso-local-20261102.md)

## Produto e arquitetura atuais

- [Plataforma standalone: escopo, jornadas e limites](plataforma-standalone.md)
- [Visão geral da arquitetura de execução](arquitetura-execucao.md)
- [Capacidades implementadas e seus limites](referencia-capacidades.md)
- [Mapa detalhado da arquitetura e responsabilidades](arquitetura-detalhada.md)
- [Domínio de engenharia de código](agente-codigo.md)
- [Workbench: uso e fronteiras](agent-workbench.md)
- [Plugins locais](plugins-locais.md)
- [Visão técnica e árvore do repositório](estrutura-diretorios.md)

## Contratos do Agent

| Tema | Referência vigente |
| --- | --- |
| Core, identidades e ciclo de vida | [agent/core.md](agent/core.md) |
| Planejamento, discovery e replanejamento | [agent/planning.md](agent/planning.md) |
| Aplicação, orquestração e execução | [agent/orchestration.md](agent/orchestration.md) |
| Segurança, autoridade e aprovação | [agent/security.md](agent/security.md) |
| Tools, skills e exposição ao modelo | [agent/tools.md](agent/tools.md) |
| Processos, stdio, timeout e cancelamento | [agent/runtime.md](agent/runtime.md) |
| Providers e modelos | [agent/llm.md](agent/llm.md) |
| Memória | [agent/memory.md](agent/memory.md) |
| Avaliação | [agent/evaluation.md](agent/evaluation.md) |
| Relatórios e evidências | [agent/reporting.md](agent/reporting.md) |
| Saúde e diagnósticos | [agent/health.md](agent/health.md) |
| Inspector, replay e exportação | [observability-inspector.md](observability-inspector.md) |

## Guias, pesquisa e histórico

- [Todos os guias de uso e operação](guides/README.md)
- [Pesquisas e avaliações, organizadas por tema](research/README.md)
- [Histórico de implementação e marcos](history/README.md)
- [ADRs — decisões arquiteturais numeradas](adr/)

Pesquisas e registros de histórico ajudam a entender por que uma mudança aconteceu; os contratos CURRENT acima definem o comportamento vigente.

## Referências operacionais

| Tarefa | Documento |
| --- | --- |
| Instalação, CLI e paths | [operacao-standalone.md](operacao-standalone.md) |
| Providers e configuração | [modelos-providers.md](modelos-providers.md) |
| Desenvolver plugins/extensions | [guia-extensao.md](guia-extensao.md) |
| Skills builtin | [skills.md](skills.md) |
| Estratégia de testes e quality gates | [testes.md](testes.md) |
| TaskGraph e scheduler | [multitarefa.md](multitarefa.md) |
| Perfil para hardware limitado | [perfil-hardware.md](perfil-hardware.md) |
| Compatibilidade e retirada de legado | [legado.md](legado.md) |
| Arquivos canônicos da raiz | [arquivos-raiz.md](arquivos-raiz.md) |
| Estrutura de pastas | [estrutura-diretorios.md](estrutura-diretorios.md) |

## Onde atualizar a documentação

Atualize o primary home correspondente quando mudar um contrato. Registre um ADR quando decidir uma fronteira duradoura; não use um relatório histórico como especificação.

| Mudança | Atualizar |
| --- | --- |
| Core/lifecycle | [agent/core.md](agent/core.md) |
| Planejamento e discovery | [agent/planning.md](agent/planning.md) |
| Authority, aprovação ou confiança | [agent/security.md](agent/security.md) |
| Tools ou model-actionable exposure | [agent/tools.md](agent/tools.md) e [referência de capacidades](referencia-capacidades.md) |
| Processo, stdio e cancelamento | [agent/runtime.md](agent/runtime.md) |
| Provider/model | [agent/llm.md](agent/llm.md) |
| Memória | [agent/memory.md](agent/memory.md) |
| Eval e grading | [agent/evaluation.md](agent/evaluation.md) |
| Reports e observabilidade | [agent/reporting.md](agent/reporting.md) e [observability-inspector.md](observability-inspector.md) |
| Operação e instalação | [operacao-standalone.md](operacao-standalone.md) |
