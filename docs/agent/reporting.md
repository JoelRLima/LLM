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
ausentes. O resultado projetado (`last_result`/status) é a evidência primária de
sucesso; o builder legado ainda possui fallback para o último step ou para uma
resposta final não vazia quando não há resultado projetado. Esse fallback é
compatibilidade de relatório, não prova independente de execução, autoridade ou
sucesso a partir de texto livre.

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
