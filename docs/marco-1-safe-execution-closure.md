# Marco 1 — Safe Execution Closure

Este documento registra somente as garantias efetivamente sustentadas pela
implementação local do Marco 1. Ele não declara sandbox de sistema operacional
nem inicia o Marco 2.

## Enforcement model-actionable

O runtime suportado é composto por `AgentApplication` e segue:

```text
modelo → plano validado → ExecutionGateway → ToolInvocationGateway
       → binding/authority → capabilities → approval → adapter
```

`ToolExecutor` não usa `LegacyToolInvoker` como fallback. O invocador legado,
`ToolRegistry.invoke` e chamadas diretas a adapters permanecem primitives de
compatibilidade/baixo nível e não são rotas model-actionable do runtime
standalone. Um `SkillRegistry` explícito pode fornecer metadata canônica para
skills customizadas; uma skill sem essa metadata falha fechado.

## Terminalidade e cancelamento

Cada tentativa concreta reserva seu `invocation_id` somente enquanto está ativa.
Uma segunda tentativa concorrente com o mesmo ID retorna
`DUPLICATE_INVOCATION_ID`; depois que a tentativa termina, o ID pode ser usado
por uma nova requisição concreta. O gateway não mantém conjunto histórico,
TTL, LRU ou semântica de exactly-once.

O estado/latch da tentativa garante um único resultado terminal observável.
Timeout e cancelamento publicam, respectivamente, `timed_out` e `cancelled`;
uma conclusão tardia do worker não publica sucesso, evento ou registro externo
adicional. Exceções inesperadas liberam o ID ativo.

Adapters de stdio, shell e `python_executor` recebem cancelamento cooperativo.
Timeout ou cancelamento sinaliza o canal e executa cleanup bounded. Adapters
in-process que não cooperem podem continuar trabalhando internamente; sua
conclusão tardia não muda o resultado externo. Retries continuam sendo novas
requisições com novos UUIDs; não existe identidade separada de retry neste
marco.

## Stdio e árvores de processo

O transporte stdio mantém leitores concorrentes com limites independentes de
stdout e stderr (1 MiB cada), Job Object com kill-on-close no Windows e
process-group/session com escalada SIGTERM/SIGKILL no POSIX. Se a associação do
launcher ao Job falhar depois de `Popen`, o processo é terminado antes de o
erro ser exposto. O launcher só cria a extension depois do envelope e o
cleanup permanece idempotente, bounded e observável.

## ShellSkill: capability reduzida

O nome factual da capability é **restricted validation/read-only command
runner**. A allowlist model-actionable exata é:

```text
ruff check [args...]
git status [args...]
git log [args...]
git diff [args...]
tree [args...]            # somente quando o executável existir
```

Isso vale tanto no Windows quanto no POSIX disponível; `tree` pode não existir
em uma instalação específica. `pytest` e `mypy` foram removidos porque um
workspace controlado poderia usá-los para executar código, plugins ou efeitos
transitivos. `echo`, `type`, `dir` e `ls` não são recriados como builtins de
shell quando não são executáveis reais.

Todos os subprocessos usam `shell=False`, `cwd` no workspace, ambiente
explicitamente construído e streams limitados independentemente a 1 MiB. A
tokenização Windows preserva barras invertidas e argumentos quoted antes de a
mesma lista ser validada e entregue a `Popen`; operadores de controle são
rejeitados. Paths explícitos são resolvidos contra o workspace, incluindo
symlinks.

`ruff` é forçado a `check --isolated --no-cache --no-fix`; configuração explícita
e modos mutantes são rejeitados. Git mantém somente status/log/diff, desabilita
pager, fsmonitor, untracked cache, diff externo, textconv e verificação de
assinaturas. Flags e formatos que solicitam verificadores de assinatura são
rejeitados. `tree -o` é rejeitado. Approval continua sendo uma policy de
produto para efeitos; não é tratada como boundary técnica.

O nome allowlisted não é uma garantia sobre qualquer binário com esse nome.
Cada runner resolve o caminho absoluto/canônico a partir de entradas absolutas
do `PATH` e rejeita executáveis cujo destino esteja dentro da árvore do
workspace controlado pelo modelo. Assim, `ruff` pode ficar indisponível quando
somente uma instalação local dentro do workspace estiver presente.

Essa capability **não é** arbitrary shell, workspace sandbox, network sandbox,
filesystem sandbox, isolamento de secrets, proteção universal contra TOCTOU ou
preempção universal. Filtrar o ambiente não garante isolamento de todo segredo
que um processo possa obter; validar um path não confina operações transitivas
de um programa; e não há isolamento técnico de rede. A evidência também não
promete execução segura de comandos não listados.

## Estado e limitações

```text
G1 model-actionable enforcement       = fechado nos caminhos suportados
G2 terminalidade                       = latch por tentativa, sem histórico
G3 cancelamento/cleanup proporcional  = fechado para subprocessos cooperativos
G4 process-tree safety                 = corrigida e validada no Windows/POSIX
G5 shell boundary                     = capability reduzida e factual
G6 claims <= evidence                 = revisado
```

Timeout/cancelamento POSIX, cleanup de descendentes após a saída do pai e
ausência de efeito tardio são exercitados no gate focal Linux do CI Ubuntu.
Falhas excepcionais pós-`Popen` também possuem regressões reais no runner.
Esses testes validam o lifecycle bounded declarado; não equivalem a sandbox de
sistema operacional ou preempção universal.

Ficam para trabalho posterior a migração de callers legados/low-level, uma
distinção entre invocação lógica e tentativa de retry, isolamento técnico de
rede e qualquer sandbox forte. Nenhum desses itens é iniciado neste marco.
