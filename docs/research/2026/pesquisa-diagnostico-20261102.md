# Pesquisa e diagnóstico de respostas — 02/11/2026

Os artefatos locais citados neste documento não são versionados nem publicados com o repositório. Seus caminhos são referências da investigação original e não comprovam a validação do candidato atual.

Foram abertas oito fontes primárias e as recomendações foram cruzadas com o código atual. Esta rodada prioriza tornar as falhas observáveis antes de alterar modelos, prompts ou política de contexto. A seleção guiada de projeto/perfil da rodada anterior já está instalada e foi considerada nesta análise.

## Prioridades

| Ordem | Lacuna | Decisão desta rodada |
| --- | --- | --- |
| P0 | Diagnóstico das respostas por contrato e sinal observado | Implementado nos relatórios de avaliação, com distinção entre inválido, não avaliado e informação ausente. |
| P0 | Conformidade de protocolo por perfil/runtime/template | Próximo candidato técnico. A configuração declarada não comprova o servidor e o modelo efetivamente usados. Mudanças de dialeto exigem identificar o alvo e testar o payload correspondente. |
| P1 | Progresso compreensível durante espera longa | O Workbench tem estado, atividade e cancelamento, mas pode apresentar melhor a duração e a ausência de atualização. Ausência de evento não deve ser apresentada como certeza de travamento. |
| P1 | Contexto enxuto com evidência preservada | Já há projeções por estágio. Ampliá-las requer comparação por perfil; reduzir contexto ou raciocínio sem medição pode prejudicar a qualidade. |
| P2 | Retomada de tarefas longas e formatos de edição | Aproveitar checkpoints, validação e rollback existentes. Comparar efeitos reais antes de acrescentar agentes ou outro formato de edição. |

A orientação da OpenAI é usar traces para localizar problemas ao longo do fluxo. Anthropic recomenda revisar transcrições e verificar os avaliadores, pois um resultado ruim pode vir do ambiente ou da avaliação. Minha inferência para este projeto é investir em diagnóstico local determinístico antes de aumentar a complexidade.

O postmortem de qualidade da Anthropic, publicado em 23/04/2026, documenta problemas distintos de configuração de raciocínio, gestão de contexto e instruções. É um exemplo histórico de por que uma queixa ampla de qualidade não identifica, sozinha, a camada responsável. Não demonstra a existência desses mesmos defeitos aqui.

## Lacuna local e implementação

O campo histórico `stage` do RecordingGateway era inferido por palavras como repair ou observation presentes no prompt. Esse campo ainda é usado por agrupamentos e oráculos antigos. Foi preservado e agora recebe `stage_source=prompt_heuristic_legacy`; ele não é uma identificação exata da fase de execução.

Cada chamada exportada recebe um diagnóstico separado, com o contrato carregado na solicitação, a origem dessa identidade, um código de observação e `task_success=null`. Não se deduz o contrato pelo conteúdo do pedido nem pela resposta. O diagnóstico usa o parser e o validador do owner canônico.

A cobertura desse owner é consultada por uma função pública. Contratos conhecidos que possuem validação em outro componente ficam como `MODEL_CONTRACT_NOT_ASSESSED`. Contrato ausente ou desconhecido fica como `MODEL_CONTRACT_UNAVAILABLE`. Esses casos não são classificados como formato inválido por suposição.

Os diagnósticos distinguem:

- JSON inválido e valor fora de um contrato coberto.
- Contrato válido na resposta bruta, sem comprovar autorização, execução ou conclusão da tarefa.
- Saída cortada e filtragem quando o finish_reason foi observado.
- Erro reportado no stream, ausência de DONE observado, falha da chamada e fechamento do consumidor.
- Resposta indisponível ou análise limitada por tamanho/recursão.

A análise de resposta é limitada a 64.000 caracteres antes do parsing. Não inclui raciocínio privado. Os eventos e as respostas continuam chegando ao consumidor original; os novos sinais de stream são observacionais. Tipos de evento serializados e enums são tratados como no consumidor canônico.

O export reúne contagens por chamada. A campanha inclui os diagnósticos no contexto de cada chamada e agrega as contagens por modo no resumo. A interface normal do chat não ganhou um novo veredicto automático: a entrega está nos relatórios de avaliação.

## Resultado da campanha simulada

Os 44 casos mantiveram os mesmos resultados, efeitos, códigos de falha, chamadas e contabilidade de tokens em relação a `research-repair-campaign-20261101.json`.

| Modo | Chamadas simuladas | Contrato válido | Fora do contrato coberto | Conhecido, não avaliado | Contrato indisponível |
| --- | --- | --- | --- | --- | --- |
| Padrão | 171 | 110 | 3 | 32 | 26 |
| Compacto | 173 | 113 | 3 | 32 | 25 |

As três respostas fora do contrato por modo aparecem nos casos `r4-h5-continuation` e `r7-h8-failure`, cujos resultados finais passaram. As contagens são observações de chamadas, não números de tarefas fracassadas. Respostas brutas podem passar por reparo ou normalização no fluxo existente.

Os 32 contratos não avaliados por modo e as identidades indisponíveis apontam limites de cobertura do diagnóstico. Não são evidência de incapacidade do modelo. Esta distinção evita transformar informação ausente em uma falsa regressão.

A campanha continua simulada: `model_used=false`, gate de capacidade real inconclusivo. Não foram feitas chamadas a modelos reais, sondagens do servidor configurado ou alterações de perfil. Os números não estimam acurácia, custo faturado ou latência de produção.

## Fontes abertas e limites

1. [OpenAI — Trace grading](https://developers.openai.com/api/docs/guides/trace-grading): avaliação da trajetória para localizar problemas de comportamento e orquestração. Não garante ganho de um modelo específico.
2. [Anthropic — Demystifying evals](https://www.anthropic.com/research/demystifying-evals-for-ai-agents): revisar transcrições, oráculos e resultados do ambiente. Avaliações de regressão não demonstram capacidade nova.
3. [Anthropic — Postmortem de qualidade](https://www.anthropic.com/research/april-23-postmortem): relato primário histórico de causas distintas por trás de queixas de qualidade. Não foi extrapolado como diagnóstico do nosso runtime.
4. [Anthropic — Context engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents): contexto relevante e ferramentas claras; não sustenta remover evidências obrigatórias nem reduzir raciocínio sem comparação.
5. [vLLM — Structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs/): formatos e versões do protocolo precisam ser explicitamente compatíveis. O servidor efetivo deste usuário não foi identificado por sondagem nesta rodada.
6. [LM Studio — Structured output](https://lmstudio.ai/docs/developer/openai-compat/structured-output): capacidade de saída estruturada depende do modelo. Não foram transferidas garantias entre modelos ou servidores.
7. [Aider — File editing problems](https://aider.chat/docs/troubleshooting/edit-errors.html): documenta dificuldades de aplicação e recomenda controlar o contexto. Limiares e recomendações históricas de modelos não foram adotados como regra universal.
8. [Microsoft Research — Human-AI Interaction](https://www.microsoft.com/en-us/research/project/guidelines-for-human-ai-interaction/): fundamenta controle, expectativas e recuperação na experiência com IA. Não substitui um estudo de usabilidade deste Workbench.

## Como consultar e repetir

```powershell
.venv/Scripts/python.exe scripts/run_weak_model_campaign.py --variant both --output reports/trace-diagnostics-repeat.json
```

Sem `--live`, esse comando usa fixtures determinísticas. Consulte `summary[modo].response_diagnostic_counts`, `results[].response_diagnostic_counts` e `results[].stage_context[].diagnostic`. Para relatórios antigos sem esses campos, a cobertura permanece desconhecida.

Evidências: campanha (artefato local: `reports/trace-diagnostics-20261102/campaign.json`), comparação (artefato local: `reports/trace-diagnostics-20261102/comparison.json`), testes (artefato local: `reports/trace-diagnostics-20261102/tests.xml`).

## Verificações desta entrega

- **593 testes aprovados** na suíte selecionada de avaliação, LLM e integração da avaliação, incluindo 28 casos focados de diagnóstico.
- Ruff passou nos arquivos alterados. Mypy passou nos cinco módulos de produção alterados. As funções novas e o recorder também passaram no limite de complexidade 10.
- Os 44 casos simulados preservaram as comparações documentadas. A igualdade inclui contabilidade de tokens; não inclui latência de execução dos testes, que varia e não representa produção.
- A checagem de arquitetura usa a política existente, sem alterações em baselines, gates ou oráculos.

A revisão instalada e a prova independente do pacote serão registradas em installed-proof.json (artefato local: `reports/trace-diagnostics-20261102/installed-proof.json`).

## Instalação verificada

Revisão atual: `61af1afa9087-20261102T122845Z-98f4290c`.
Anterior preservada: `b5cfe2173a77-20261102T121120Z-f4e7a121`.
Wheel SHA-256: `834e1f40237168f0ca986cc5f4a994f952e959ed90d4db94f769cd6567a3e68a`.

O Python isolado do pacote passou em dez episódios simulados, com uma chamada por episódio e zero chamadas adicionais de diagnóstico. Foram verificados fingerprints da origem e do pacote e a correspondência da origem com a campanha. A arquitetura final passou com 983 módulos e 3475 arestas. Não houve inferência real nem alteração da política de validação.
