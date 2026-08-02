# ADR 0010: materializacao runtime e bootstrap de extensions

- Status: Gates 2.4 e 2.5 concluidos, validados e publicados
- Data: 2026-08-02

## Decisao

Os Gates 2.4 e 2.5 mantem a cadeia explicita:

```text
catalogo global + configuracao do workspace
  -> resolucao do Gate 2.3
  -> materializacao runtime
  -> composicao atomica do ToolRegistry
  -> AgentApplication do workspace
```

O materializador consome exclusivamente `ResolvedWorkspaceExtensions`. Somente
entradas com `activation_status == "ready"` produzem binding. Disabled,
blocked, orfas, drifted, ausentes, invalidas ou sem grants permanecem fora do
runtime e geram diagnostico seguro de nao elegibilidade.

## Materializacao runtime

`ExtensionRuntimeMaterializer` rele o manifest aprovado uma unica vez por
extension. Os mesmos bytes sao usados para fingerprint e parser estrito; o
fingerprint precisa coincidir com o catalogo, o ID precisa coincidir com a
entrada resolvida, o protocolo precisa ser `1.0` e as capabilities observadas
precisam permanecer coerentes com a resolucao. Drift, ausencia, invalidade,
incompatibilidade, ID divergente ou descriptor invalido impedem a publicacao
da extension inteira.

Os unicos placeholders canonicos sao `${extension_dir}`, resolvido para o
diretorio absoluto do manifest, e `${python}`, resolvido para `sys.executable`.
A substituicao e textual e single-pass sobre a string original: o texto
introduzido por uma substituicao nunca e reprocessado. Placeholders desconhecidos
e tokens incompletos falham fechado; `$`, `$HOME` e texto sem `${` permanecem
literais. Nao ha shell interpolation, expansao de ambiente ou execucao durante
a expansao. O cwd futuro do adapter e sempre o workspace absoluto recebido pelo
bootstrap.

Entrypoints estruturalmente invalidos continuam classificados como
`EXTENSION_RUNTIME_MANIFEST_INVALID`, porque o parser canonico nao oferece uma
classificacao estruturada independente sem reabrir contratos anteriores.

`ExtensionRuntimeBinding` e um snapshot imutavel contendo ID, fingerprint
aprovado, um adapter stdio, todos os descriptors da extension e metadata segura.
Schemas e metadata sao armazenados em representacoes compostas e imutaveis,
sem herdar de `dict` ou `list`. Cada leitura publica reconstrói uma copia
defensiva mutavel compatível com `dict` e JSON; nenhuma alteracao nessa copia
afeta o snapshot interno. O adapter guarda uma configuracao privada read-only
do manifest e do cwd e reconstrói um `ExtensionManifest` novo em cada acesso.
Invocacao usa somente a configuracao privada. O congelamento aceita apenas
JSON estrito, rejeitando subclasses primitivas, floats nao finitos, ciclos e
objetos arbitrarios. A garantia cobre aliases e todas as operacoes sobre os
valores publicados; nao cobre manipulacao deliberada de estado privado. Nao
persiste binding, grants, manifest, handles ou processos. O construtor de
`StdioToolAdapter` continua sem processos, threads, sockets, handshake ou I/O.

## Composicao e colisoes

`WorkspaceToolRegistryComposer` cria um novo `ToolRegistry`, registra builtins
primeiro e faz preflight de todos os bindings antes de publicar extensions.
Uma extension e unidade atomica: falha de descriptor ou colisao remove todas
as suas tools. Builtins tem precedencia. Colisao entre extensions rejeita todas
as extensions declarantes, inclusive quando uma delas tambem colide com builtin
ou possui duplicidade interna; extensions nao envolvidas continuam elegiveis.
A ordem de IDs e nomes e deterministica.

O registry publicado e congelado apos a composicao. Nenhum registro posterior
ou alias de schema/configuracao altera o snapshot da aplicacao. Workspaces
diferentes constroem registries e bindings independentes, mesmo compartilhando
o catalogo global. Freeze repetido e idempotente; lookup e listagem continuam
funcionando.

## Bootstrap degradado

`ApplicationExtensionBootstrap` recebe `AppPaths`, `workspace_id` e workspace
explicito. Ele carrega catalogo, configuracao, observacoes e resolucao, depois
materializa e compoe. Arquivos ausentes produzem somente builtins. Corrupcao
esperada do catalogo ou workspace produz builtins e diagnostico estruturado;
corrupcao nao e convertida silenciosamente em configuracao vazia. Falha de uma
extension nao derruba builtins ou extensions validas. Erros internos inesperados
continuam propagando.

Os diagnosticos sao retornados no resultado do bootstrap e ficam disponiveis em
`AgentApplication.bootstrap_diagnostics`; logging nao e sua unica fonte.
Nenhuma etapa de bootstrap chama `Popen`, `run`, `os.system`, shell, launcher,
probe externo ou processo stdio.

## Evidencias permanentes

Os testes unitarios cobrem freeze, copias profundas, placeholders, colisoes
mistas e bugs inesperados. O acceptance integrado cobre catalogo, configuracao
do workspace, resolucao, materializacao, composicao, workspaces isolados,
drift e replace explicito, sem invocar a tool. O gate de packaging tambem
executa o fluxo extension-aware a partir do wheel, fora do checkout, verificando
descriptor, cwd, builtins, origem dos imports e zero subprocessos.

## Limites

Esta decisao nao implementa planner ou descoberta, autorizacao no gateway,
approval por tarefa, CLI administrativa, hot reload, timeout/cleanup,
marketplace, sandbox, download, registry legado, protocolo stdio ou
`invocation_id`. Gate 2.6 cobre descoberta pelos planners; Gate 2.7 cobre
autorizacao e invocacao pelo gateway; Gates 2.8 e 2.9 cobrem administracao,
diagnostico e acceptance instalada.
