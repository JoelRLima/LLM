# Measurement e reporting

> **STATUS: CURRENT — PRIMARY HOME.** Evaluation consome projeções descritas
> aqui; veja [evaluation.md](evaluation.md).

## Fontes e projeções

O sistema mantém fatos em suas fontes operacionais e os projeta para relatórios:

- histórico/estado da tarefa fornece steps, status e erros;
- eventos de replan fornecem tentativas e motivos;
- `MetricsRecorder` mantém JSONL e permite uma marca d'água por tarefa;
- resultados de invocation fornecem status e, quando o adapter os preserva,
  limites de saída; duração é uma métrica da tarefa/eval, não um campo garantido
  por invocation;
- `TaskReportBuilder` agrega essas fontes em JSON ou Markdown;
- o adapter de eval projeta um subconjunto de measurement para
  `ExecutionObservation` e exportação do cenário.

Projection não é uma segunda fonte de verdade. Campos ausentes permanecem
ausentes. `AgentRunResult.status/success` e o `canonical_outcome` fornecido ao
`TaskReportBuilder` representam o resultado operacional da execução; não são um
julgamento de satisfação semântica do objetivo. O receipt operacional separa a
resposta do modelo dos fatos observáveis: tools executadas ou negadas, arquivos,
validação, rollback, causa e caminho do relatório. A satisfação do objetivo
continua sendo responsabilidade de um avaliador externo.

## Artefatos

- `TaskReportBuilder`: relatório final com task id, objetivo, steps, replans,
  métricas, erros e preview limitado da resposta.
- `TaskTracker`: JSON estruturado e renderização Markdown do progresso;
  persistência atômica best-effort para observabilidade.
- `IncrementalSummarizer`: limita conteúdo acumulado em execução hierárquica;
  seu resumo não substitui os resultados originais usados para controle.
- `MetricsRecorder`: apêndice JSONL e leitura a partir de watermark.

## Limites de dados

Relatórios truncam previews e normalizam estruturas para serialização, mas não
constituem um sistema geral de DLP ou secret scanning. O chamador continua
responsável por escolher paths e circulação dos artefatos. Reason codes são
diagnóstico observável; sua precedência, quando múltiplos guards negariam a
mesma invocation, não é estável salvo garantia explícita em contrato.
