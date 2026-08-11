# Runtime de execução e lifecycle de processos

> **STATUS: CURRENT — PRIMARY HOME.** Paths e operação ficam em
> [operação standalone](../operacao-standalone.md); orchestration lógica em
> [orchestration.md](orchestration.md).

## Fronteiras

`AppPaths` separa config, data, state, cache e logs. `WorkspacePaths` particiona
memória, checkpoint, métricas, relatórios, scratch, artifacts e locks por
workspace. Construtores de paths não escrevem; criação ocorre após configuração
e workspace serem validados.

`ConfigRepository` combina defaults empacotados, arquivo, ambiente allowlisted
e overrides, valida schema e materializa o perfil selecionado. Config inválida
falha antes de diretórios, lock, modelo ou tools.

## Timeout, cancelamento e terminalidade

O gateway reserva um `invocation_id` durante uma tentativa. Timeout ou
cancelamento publica um único resultado terminal e sinaliza o token/evento ao
adapter. Um worker Python genérico pode terminar tardiamente, mas sua conclusão
não publica segundo resultado, evento ou record. Isso é terminalidade
observável, não preempção universal do código do worker.

Skills de subprocesso recebem cancelamento cooperativo e possuem seus próprios
limites. Falha, timeout, cancellation, denial, protocol error e unverified não
são convertidos em sucesso.

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
Checkpoint v2 persiste IDs/estados de passos; `running` volta a `pending`, e
somente `failed`/`skipped` podem ser reabertos por flags explícitas. Estados
`completed`, `blocked` e `unverified` permanecem terminais. O lock por workspace
é conservador e um lock abandonado pode exigir inspeção manual.

## Não garantias

Não há sandbox forte, isolamento uniforme de rede/filesystem, rollback de
efeitos arbitrários de processos, exactly-once histórico, `attempt_id`, hot
reload de extensions ou cancelamento preemptivo universal.
