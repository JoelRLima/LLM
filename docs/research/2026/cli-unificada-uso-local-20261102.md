# Pesquisa: CLI unificada, uso local e viabilidade — 02/11/2026

> Atualização posterior em 02/11/2026: `llm-agent local` foi implementado no worktree usando a mesma `InteractiveSession` do Workbench. `/llm on/off` controla a sessão sem enviar pedido; `/llm status` não faz rede; `/llm test` faz uma consulta limitada ao catálogo `/models`, sem geração. A CLI local também acompanha a saída durante a execução e preserva uma resposta final completa se o fluxo de exibição for limitado. A suíte de regressão não foi executada após essa integração.

## Pergunta

Como tornar o Workbench e a CLI úteis como ambientes de trabalho do Agent —
inclusive sem LLM — sem tentar transformar o produto em um terminal geral ou
duplicar regras de execução?

## Diagnóstico local

O propósito e os guias já declaram que o núcleo deve ser compartilhado. O
Workbench inicia em Uso local; consultas ao workspace, análise estática,
preparação de filas, inspeção e configuração local não exigem inferência. A
CLI também já tem comandos locais, como `doctor`, `commands`, `task status`,
`task context`, `inspect` e `extensions`.

Encontrei uma lacuna específica na jornada de testes: o Workbench chama
`TestQueueService`, e existe um adaptador CLI para a mesma camada, mas o usuário
precisava conhecer `Testes_Agent.cmd` ou `python -m llm_agent.interfaces.test_queue`.
O parser principal `llm-agent` não listava
essa capacidade. O wrapper `.cmd` usa o `.venv` do checkout, o que também o
torna menos conveniente para o pacote instalado.

## Ordem de prioridade

| Prioridade | Melhoria | Viabilidade e decisão |
| --- | --- | --- |
| **P1 — entregue nesta rodada** | Expor a fila de testes como `llm-agent queue`, mantendo menu, opções, códigos de saída e `--live` do adaptador existente | **Alta.** Foi incluído um encaminhamento para `interfaces.test_queue.main`; serviços, autorização e verificação continuam no `TestQueueService` já usado pelo Workbench. Não cria outro runtime nem consulta modelo para preparar ou inspecionar filas. |
| **P1 — implementado após esta análise** | Modo local interativo na CLI, com consultas de arquivos/Git/análise antes de configurar modelo e ativação explícita da LLM | **Alta.** `llm-agent local` e o Workbench usam a mesma factory de sessão e `InteractiveSession`; o adaptador CLI reutiliza catálogo e comandos compartilhados. O modo `chat` permanece compatível. A alteração ainda precisa de regressão automatizada. |
| **P1 — próximo experimento de usabilidade** | Encontrar capacidades pelo objetivo (“buscar arquivo”, “conferir execução”, “inspecionar extensão”) com nomes e exemplos consistentes entre `--help`, catálogo e Workbench | **Média/alta.** Catálogo interativo, paleta e descoberta já existem; falta confirmar que a CLI local expõe bem ações offline e o controle da LLM. Começar por entradas e aliases, sem criar um segundo catálogo. |
| **P2 — ciclo de vida** | Continuar uma bateria após encerrar o Workbench, com processo de execução independente da janela | **Baixa/média.** Hoje o Workbench informa que a aplicação deve permanecer aberta e que encerrar o app interrompe seu runner. A CLI pode manter o processo no terminal. Serviço em background exigiria protocolo de controle, coordenação entre processos, encerramento, recuperação e diagnósticos; a persistência do estado sozinha não mantém um processo vivo. Não é necessário para disponibilizar a CLI consolidada. |
| **P2 — interoperabilidade de plugins** | Considerar MCP como transporte adicional para extensões | **Média/baixa no curto prazo.** O projeto já tem protocolo stdio próprio com catálogo, grants e authority. Um adapter futuro deve passar pelo gateway atual e preservar origem/confiança; metadados de tools MCP são não confiáveis quando vêm de servidores não confiáveis. Não importar servidor ou annotation como permissão. |
| **P3 — capacidade de modelos menores** | Comparar comportamento real de modelos depois dos diagnósticos simulados | **Condicionada.** A campanha offline verifica o harness, não a qualidade de um modelo. Só uma execução real, opt-in, com tarefas fixas, critérios externos e orçamento explícito mede esse resultado. Não chamei modelo nesta rodada. |

## Mudança implementada

`llm-agent queue` encaminha para o adaptador de terminal existente. Sem
argumentos, abre seu menu; `llm-agent queue --help` exibe os comandos da fila.
Exemplos:

```powershell
llm-agent queue create .\examples\test-queue .\reports\minha-fila
llm-agent queue check .\reports\minha-fila
llm-agent queue status .\reports\minha-fila
llm-agent queue run .\reports\minha-fila --config .\config.json --live
```

Criar, conferir, inspecionar, exportar e controlar a fila são operações locais.
`run` exige `--live`, que mantém explícita a chamada ao provedor. O usuário
continua podendo iniciar a fila pelo painel Testes, pelo `.cmd` ou pelo módulo
Python. A CLI principal agora oferece um caminho direto e instalável.

## Evidência externa e interpretação

- A [referência oficial da CLI Claude Code](https://code.claude.com/docs/en/cli-usage)
  separa modos de permissão, execução interativa e execução de saída estruturada.
  Isso apoia tornar o modo e o formato de operação explícitos; não é evidência de
  que precisamos reproduzir o terminal ou permissões desse produto.
- O [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) documenta
  tools, guardrails, sessões, participação humana e tracing como recursos do
  runtime. A [referência de tracing](https://openai.github.io/openai-agents-python/tracing/)
  registra etapas da trajetória. Isso sustenta preservar aprovação e evidência
  como parte da experiência, em vez de apresentar apenas uma resposta final.
- A [especificação MCP de tools](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-06-18/server/tools.mdx)
  determina que clientes tratem annotations como não confiáveis salvo origem
  confiável. Portanto, compatibilidade de transporte não deve conceder
  autoridade ou substituir as grants do Agent.
- A [documentação Temporal sobre retries](https://docs.temporal.io/encyclopedia/retry-policies)
  distingue retry de atividades e de workflows, e recomenda atenção a
  idempotência. É uma referência para não confundir estado persistido com
  execução em background ou retry seguro; não justifica introduzir Temporal no
  projeto local.

As recomendações acima são decisões de engenharia inferidas a partir das fontes
e da arquitetura encontrada; as fontes descrevem outros runtimes e não medem a
experiência nem o desempenho deste Agent.

Na rodada seguinte, a pesquisa oficial reforça duas prioridades já compatíveis
com o desenho local: a OpenAI recomenda inspecionar traces para localizar falhas
e depois fixar critérios em datasets e execuções repetíveis; a Anthropic destaca
retorno de ground truth a cada etapa, ferramentas claras e avaliação em ambientes
controlados. O Agent já oferece traces, fila reproduzível e verificações locais;
o próximo ganho de UX é reduzir o esforço para localizar a ação certa e avançar
de uma falha observada para um caso de avaliação reaproveitável. Essa ligação
precisa ser feita por projeções/contratos existentes e manter revisão humana,
sem alterar autoridade ou executar reparos automaticamente.

- [OpenAI — Evaluate agent workflows](https://developers.openai.com/api/docs/guides/agent-evals)
- [OpenAI — Trace grading](https://developers.openai.com/api/docs/guides/trace-grading)
- [Anthropic — Building effective agents](https://www.anthropic.com/research/building-effective-agents)

## Verificação e limites

A mudança mantém o adaptador e o serviço já existentes. A análise desta rodada
confirmou estaticamente o caminho CLI → adaptador de fila → `TestQueueService`,
que também é chamado pelo Workbench. A ajuda da CLI foi conferida sem configurar
ou chamar um modelo. Não rodei a suíte de testes nem campanha com LLM; portanto,
essa revisão não declara regressão ausente ou ganho de qualidade de modelo.

A versão gerenciada foi atualizada para incluir a sessão local. O gerenciador
validou o candidato, os imports do pacote e `pip check`; o estado final registra
`update_available: false`. Não foi feita uma execução interativa com servidor
de modelo.

A próxima lacuna para aproximar as interfaces é confirmar por avaliação de
usabilidade se a descoberta por objetivo e os exemplos locais são consistentes
entre `llm-agent --help`, `/commands`, a paleta do Workbench e os guias.
