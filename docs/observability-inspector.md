# Observabilidade e Inspector

Este documento é o primary home da espinha de observabilidade da Wave 9.

## Ownership e fluxo

`RuntimeEvent` continua sendo a fonte semântica de verdade. A aplicação cria uma
`ObservationSession` por run depois de alocar a correlação e a conecta ao
`RuntimeEventDispatcher`. O TraceStore recebe essa projeção por uma fila bounded
e grava um `trace.jsonl` por run, acompanhado de metadata/index. Diagnósticos
seguem um `DiagnosticRecord` separado e nunca entram em `AgentState`, checkpoint,
outcome, autoridade ou decisão de execução.

```text
RuntimeEvent ──> RuntimeEventDispatcher ──> ObservationSession ──> TraceStore
       │                                      │                     │
       └──> state/checkpoint projection       └──> heartbeat         └──> read-only
                                                                    Presentation API
                                                                    ├── CLI/TUI
                                                                    ├── /inspect
                                                                    ├── replay/search
                                                                    └── diagnostic export
```

O trace é uma leitura operacional. Ele não é checkpoint, memória, autoridade,
outcome, mecanismo de resume ou fonte de decisão do Agent.

## Níveis e redaction

Existem exatamente quatro níveis de observabilidade: `NORMAL`, `VERBOSE`,
`DEBUG` e `TRACE`. O nível altera apenas o volume de diagnóstico; não altera
semântica, policy, aprovação, segurança ou resultado. `RuntimeEvent` permanece
presente como fato semântico. Todos os dados de diagnóstico, detalhe, replay e
export passam por redaction recursiva, limites de profundidade/tamanho e remoção
de credenciais, prompts irrestritos, completions, ambiente bruto e hidden
reasoning. TRACE não é uma autorização para capturar segredos.

## Completude, gaps e live status

Cada observação aceita recebe sequência inteira monotônica por run. Timestamp é
informativo; replay ordena por sequência. Pressão de fila descarta diagnósticos
antes de fatos semânticos quando possível, registra contadores e materializa
gaps. Um trace com perda é `PARTIAL`; fechamento sem dreno confirmado é
`UNCLEAN`; JSONL/metadata inválido é `CORRUPT`; somente um fechamento sem gaps
conhecidos é `COMPLETE`. Supressão por nível é contabilizada separadamente e não
é confundida com corrupção.

Heartbeat do observer e silêncio semântico são fatos diferentes. Heartbeat prova
apenas que a sessão conseguiu publicar seu pulso; não prova progresso da tarefa.
O inspector mostra os dois, o tempo de silêncio e o evento canônico `WATCHDOG`
separadamente, sem inferir “hung” só pelo tempo.

## CLI e uso

Retention defaults are 50 runs, 64 MiB, and 30 days. Automatic cleanup runs
only after observation finalization and only considers closed trace directories;
active and stale/uncertain markers remain fail-safe and exports are separate.

Um marker `active` persistido Ã© projetado como `closed`, `live`/`recently-live`
ou `stale` com incerteza; nunca Ã© exibido como prova definitiva de processo
vivo. Markers stale nÃ£o vencem a seleÃ§Ã£o active-first e nÃ£o sÃ£o candidatos de
retenÃ§Ã£o.

O comando `llm-agent inspect` não cria `AgentApplication`, não adquire lock e
não inicia nem retoma tarefa. Fora de TTY, use snapshots bounded/JSON:

```powershell
llm-agent inspect list --json
llm-agent inspect show --run-id RUN_ID --json --limit 100
llm-agent inspect replay --run-id RUN_ID --json --limit 100
llm-agent inspect export --run-id RUN_ID --output trace.zip
llm-agent inspect bookmark add --run-id RUN_ID --sequence 12 --note "verificar"
```

`--follow` requer TTY e não pode ser combinado com `--json` sem um contrato de
streaming bounded. O modo interativo exibe header, timeline, contexto corrente,
resumos de modelo/tools/validation/change/metrics, warnings/gaps e detalhe
redigido; Ctrl-C desanexa sem alterar o run. `/inspect` no chat reutiliza a
mesma Presentation API.

## Replay, filtros e bookmarks

Na CLI, a superfÃ­cie correspondente Ã© `--after`, `--sequence`,
`--sequence-start`, `--sequence-end`, `--source`, `--kind`, `--category`,
`--severity`, `--status`, `--task-id`, `--root-task-id`, `--step`,
`--correlation-id`, `--invocation-id`, `--time-start`, `--time-end`,
`--bookmarked-only` e `--search`.

Replay é determinístico, bounded e somente leitura. Filtros aceitam sequência,
fonte, kind/categoria, severity/status, task/root-task, step, invocation,
janela temporal, bookmark e busca na representação já redigida. Bookmark é uma
anotação limitada em sidecar atômico; não modifica ordenação ou completude.

## Export e retenção

Export cria um bundle determinístico com manifest, hashes, metadata, trace,
snapshot e environment/version seguro; bookmarks são opcionais. O destino não é
substituído sem `--force`. O bundle não inclui logs arbitrários, source,
checkpoint, memória, environment bruto, Task Definition não redigida, prompts,
completions ou hidden reasoning. Retenção só remove runs fechados dentro dos
diretórios de trace que pertencem ao workspace; runs ativos e artefatos de
export são tratados separadamente.
