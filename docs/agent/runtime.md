# Runtime de execução e lifecycle de processos

> **STATUS: CURRENT — PRIMARY HOME.** Paths e operação ficam em
> [operação standalone](../operacao-standalone.md); orchestration lógica em
> [orchestration.md](orchestration.md). A política task-scoped e a retomada
> operacional são descritas aqui.

## Fronteiras

`AppPaths` separa config, data, state, cache e logs. `WorkspacePaths` particiona
memória, checkpoint, métricas, relatórios, scratch, artifacts e locks por
workspace. Contract, Spec e manifest de Task Definition ficam em
`data/workspaces/<workspace-id>/task_definitions/<task-id>/`, separados do
checkpoint operacional. Construtores de paths não escrevem; criação ocorre
após configuração e workspace serem validados.

`ConfigRepository` combina defaults empacotados, arquivo, ambiente allowlisted
e overrides, valida schema e materializa o perfil selecionado. Config inválida
falha antes de diretórios, lock, modelo ou tools.

## Política de runtime por tarefa

`TaskRuntimePolicy` é a única seam canônica e estreita para decisões de runtime
no escopo da tarefa. Ela compõe fatos existentes e adapters de rota; não
substitui os owners abaixo:

- `RuntimeLimits` continua sendo a autoridade dos limites configurados;
- `TaskBudgetLedger` continua sendo a autoridade quantitativa de modelo, tools
  e tokens;
- `RecoveryBudgetState` continua sendo a autoridade de consumo de recovery;
- `CancellationToken` continua sendo a autoridade de cancelamento;
- `Plan` e `StepExecutionRecord` continuam sendo a autoridade do plano
  executável;
- `TaskSemantics` continua sendo a autoridade semântica e de evidência;
- `OperationalOutcome` continua sendo a autoridade operacional terminal;
- W4 continua sendo a espinha de eventos e correlação;
- a autoridade de Task Definition do W5.5 permanece inalterada.

`TaskPolicyState` possui somente os fatos task-scoped novos: admissões de
unidades lógicas de trabalho executável e duração ativa acumulada. A admissão é
escopada à tarefa raiz: mudar a forma da rota, replanejar ou limpar um
microplano não reinicia o consumo. Em batches paralelos, a admissão acontece
antes do dispatch e o prefixo é truncado pela quantidade ainda disponível.

A policy aplica a mesma verdade task-scoped, por adapters estreitos quando
necessário, nas rotas linear, paralela, reativa, hierárquica, de analyzer de
segurança, de nós de `TaskGraph`, de sessões/modelos aninhados e nos seams de
recovery, replan ou continuação que participem da tarefa. A implementação pode
variar por subsistema; a política efetiva não pode divergir.

Quando vários fatos negam uma operação, a precedência finita da policy é:

`cancellation` → exaustão quantitativa → exaustão de trabalho lógico → exaustão
de wall-time ativo → fatos de watchdog/sem progresso → exaustão de recovery.

Essa ordem é da policy task-scoped. Ela não generaliza os guards de invocation
que possuem contratos próprios.

## Timeout, cancelamento e terminalidade

O gateway reserva um `invocation_id` durante uma tentativa. Timeout ou
cancelamento publica um único resultado terminal e sinaliza o token/evento ao
adapter. Um worker Python genérico pode terminar tardiamente, mas sua conclusão
não publica segundo resultado, evento ou record. Isso é terminalidade
observável, não preempção universal do código do worker.

Skills de subprocesso recebem cancelamento cooperativo e possuem seus próprios
limites. Falha, timeout, cancellation, denial, protocol error e unverified não
são convertidos em sucesso.

Depois de publicar um resultado terminal, o gateway não aceita uma segunda
publicação, evento ou record de um worker tardio. Caminhos mutáveis devem
quiescer antes da publicação ou permanecer sob o owner da invocation; o
contrato é terminalidade observável e ausência de mutação canônica tardia, não
preempção universal do worker Python.

Contextos e sessões aninhados sob a mesma tarefa raiz reutilizam o
`CancellationToken` canônico. Isso garante uma decisão única de cancelamento na
árvore de execução, mas não implica preempção universal no provider: a
interrupção do transporte só existe onde o transporte oferece esse suporte.

## Stdio 1.0

Cada invocação externa usa um subprocesso e um request com `invocation_id` não
vazio. A resposta é exatamente uma linha JSON não vazia, UTF-8, com o mesmo ID.
`status` é opcional: quando omitido, o adapter assume `succeeded`; quando
presente, deve ser conhecido. ID ausente/divergente, JSON/framing inválido e
output acima de 1 MiB por stream são erros de protocolo. Exit code não zero é falha mesmo
que stdout contenha um JSON plausível. Stderr é diagnóstico limitado, nunca
resultado de sucesso.

O processo recebe `cwd` do workspace, `shell=False` e ambiente operacional
reduzido. Isso não confina efeitos transitivos nem constitui sandbox.

## Ownership e cleanup

O adapter possui processo, pipes, threads de drenagem e árvore da invocation.
Sucesso também encerra a árvore, pois descendentes podem sobreviver ao pai ou
reter pipes. Erros de terminação/drenagem são `CLEANUP_ERROR`; portanto a
garantia é que cleanup é obrigatório e sua falha é observável, não que o SO
nunca possa recusar uma terminação.

- POSIX: nova sessão/grupo; SIGTERM seguido de SIGKILL quando necessário.
- Windows: launcher interno é associado a Job Object antes de receber o
  envelope que libera a extension real. Se criação/associação falhar, a
  extension não inicia. `taskkill.exe` canônico é fallback de cleanup.

O launcher Windows é detalhe interno, não tool, protocolo público ou sandbox.
POSIX inicia a extension diretamente. Não se declara suporte de release para
plataformas além das exercitadas pela matriz do projeto.

## Memória, checkpoint e lock

Memória JSON/SQLite é inicializada explicitamente e persiste por workspace.
Promoções JSON usam tempfile, fsync e `os.replace`; corrupção falha fechado.
Checkpoint v2 persiste IDs/estados de passos e o estado task-scoped. Para passos
comuns, `running` volta a `pending`, e somente `failed`/`skipped` podem ser
reabertos por flags explícitas. Estados `completed`, `blocked` e `unverified`
permanecem terminais. O lock por workspace
mantém um guard advisory durante a vida do processo, registra PID e identidade
de início quando o sistema fornece essa prova e recupera registros stale após
uma morte anormal. JSON inválido ou identidade indeterminada falha fechado e
pode exigir intervenção manual.

O estado da policy persiste admissões lógicas e duração ativa acumulada. O
checkpoint armazena a duração acumulada, não um timestamp monotônico; o início
monotônico de cada segmento é transitório. Portanto, downtime enquanto o
processo está parado não consome o limite de wall-time. Em uma reentrada
suportada, o consumo task-scoped restaurado permanece e um novo segmento ativo
é iniciado. Owners quantitativos e de recovery não são duplicados por esse
estado.

O lifecycle hierárquico macro é explícito e também faz parte do checkpoint. Um
hierárquico interrompido no estado `running` não é pseudo-retomado: a reentrada
é bloqueada de forma determinística com
`HIERARCHICAL_RESUME_UNSUPPORTED`, preservando o checkpoint para auditoria.
Limpar um microplano individual não altera o consumo da tarefa raiz.

Task Definitions usam arquivos JSON canônicos e limitados, publicação atômica
do manifest e criação exclusiva dos corpos versionados. Contract e Spec
persistidos são imutáveis: conteúdo diferente para a mesma identidade é
mismatch, não overwrite. O checkpoint guarda somente a `TaskDefinitionRef`;
no resume, o repositório confere workspace, task id, versões, digests, arquivos
referenciados, estado completo e fase selecionada antes de devolver os corpos.
Link-like paths, arquivo ausente, corrupção ou identidade divergente falham
fechado.

O commit de observação valida a entrada canônica antes de publicar state e
history. Colisão ou rejeição deixa essas fontes coerentes; telemetria auxiliar
não pode promover uma falha de commit a sucesso.

## Não garantias

Não há sandbox forte, isolamento uniforme de rede/filesystem, rollback de
efeitos arbitrários de processos, exactly-once histórico, `attempt_id`, hot
reload de extensions ou cancelamento preemptivo universal.
