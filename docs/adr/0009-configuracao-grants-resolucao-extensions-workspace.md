# ADR 0009: configuração, grants e resolução de extensions por workspace

- Status: patch residual implementado localmente — pendente de aprovação final do Gate 2.3
- Data: 2026-08-02

## Contexto

O Gate 2.2 consolidou o catálogo global persistente: ele conhece extensions,
paths e fingerprints, mas não deve decidir qual workspace pretende usar. O
Gate 2.3 adiciona configuração isolada por workspace, grants explícitos e uma
resolução derivada. Esta etapa não executa extensions nem registra adapters.

## Decisão

O catálogo global continua sendo a fonte de identidade e origem. A fronteira
operacional aceita somente `AppPaths`, `workspace_id` e o catálogo; o serviço
deriva `WorkspacePaths` exclusivamente de `AppPaths.for_workspace(workspace_id)`.
Assim, a garantia de identidade vale dentro de um mesmo perfil `AppPaths`: dois
serviços para o mesmo `workspace_id` nesse perfil compartilham exatamente o
mesmo arquivo e lock. Ela não pretende equivalência global entre perfis
`AppPaths` distintos. A construção operacional não aceita `WorkspacePaths`
arbitrário. Cada workspace possui um documento próprio em
`WorkspacePaths.workspace_extensions_file`, com lock adjacente em
`workspace_extensions_lock_file`. A fábrica privada `_for_testing` deriva os
caminhos canônicos e rejeita storage ou lock injetados que não coincidam
exatamente com eles, antes de qualquer I/O:

```json
{
  "schema_version": 1,
  "extensions": {
    "security.scanner": {
      "enabled": true,
      "grants": ["filesystem.read"]
    }
  }
}
```

O documento persiste somente intenção (`enabled`) e grants explicitamente
concedidos. Não persiste path, fingerprint, flavor, protocolo, observação,
diagnóstico, adapter ou comando.

`WorkspaceExtensionSelection` e `WorkspaceExtensionsState` continuam sendo os
modelos imutáveis canônicos. A presença da seleção é preservada mesmo quando
`enabled` é falso, permitindo disable sem perder grants. IDs de capability são
IDs abertos: reutilizam o validador de strings não vazias, sem um registry
paralelo. O manifest continua sendo a fonte das capabilities requeridas,
extraídas da união de `tools[*].capabilities`.

`enable` expressa intenção e nunca concede capability. `grant` é uma operação
independente, exige configuração existente e manifest observado como válido e
inalterado, e aceita somente capability declarada no manifest atual. Grants
repetidos, disable/revoke ausentes e demais repetições exatas são idempotentes
e não salvam novamente quando não há mudança. Grants que deixarem de ser
requeridos permanecem persistidos como warnings não bloqueadores até `revoke`.

## Observação e resolução

`observe_catalog_document` reutiliza o parser estrito do Gate 2.2, lê cada
manifest uma única vez e retorna uma observação imutável, validada e
defensivamente copiada, com status, fingerprint observado e resumo seguro das
capabilities. A API histórica
`validate_catalog_document` continua retornando os diagnósticos anteriores.

`resolve_workspace_extensions` é pura: recebe somente
`WorkspaceExtensionsState`, `ExtensionCatalogDocument` e observações. Não
acessa filesystem, não altera catálogo/workspace e não persiste diagnósticos.
Cada resultado mantém separadamente presença no catálogo, status do manifest,
grants configurados, grants efetivos, ausentes, excedentes e diagnósticos.

Os estados de ativação são:

- `disabled`: intenção desabilitada; diagnósticos de órfão ou drift continuam
  visíveis;
- `blocked`: órfão, manifest ausente/alterado/inválido/incompatível ou grant
  ausente;
- `ready`: extension habilitada, manifest `unchanged` e todos os grants
  requeridos explicitamente concedidos.

Referências órfãs são preservadas até `remove_configuration`. Drift não altera
`enabled` nem grants; a correção ocorre somente por `replace` explícito no
catálogo. Uma capability nova declarada pelo manifest bloqueia até grant
explícito, sem auto-grant.

Quando o catálogo está presente mas não há observação correspondente, o
resolver emite `WORKSPACE_EXTENSION_MANIFEST_UNAVAILABLE`. A ausência de
observação não permite inferir `missing_grants` ou `unused_grants`.

## Persistência e concorrência

O codec rejeita BOM, UTF-8 inválido, JSON inválido, constantes não finitas,
chaves duplicadas, versão diferente de 1, campos extras/ausentes, IDs
inválidos, tipos incorretos e grants duplicados. A ordem JSON é determinística,
sem BOM e com newline final.

O storage reutiliza as primitivas atômicas do Gate 2.2: tempfile no mesmo
diretório, flush/fsync, `os.replace`, fsync de diretório best effort e cleanup
sem mascarar causa primária. O arquivo ausente representa estado vazio sem
criar diretórios; arquivo presente inválido falha explicitamente.

Cada workspace usa lock próprio. Mutações seguem `lock → reload → validar →
aplicar → save atômico → release`. A concorrência é não bloqueante: um
`CatalogLockBusyError` é devolvido ao chamador, que pode repetir explicitamente
a operação; não há retry automático. O reload após o retry fornece semântica
serial. Workspaces diferentes não compartilham documento ou lock.

## Privacidade e limites

Diagnósticos e erros públicos contêm somente ID, código, severidade, mensagem
segura e capability quando necessária. Mensagens do codec não incluem nomes de
campos, valores, conteúdo do JSON ou outros dados controlados. Não incluem
descrição, schema, conteúdo do manifest, secrets, ambiente ou stack trace.

Esta etapa não integra CLI, bootstrap, `AgentApplication`, `ToolRegistry`,
planner, gateway, adapter, subprocessos, hot reload, marketplace, sandbox ou
TCC. O snapshot resolvido será consumido pelo Gate 2.4, que poderá construir
adapters e conjunto ativo em etapa separada.

## Consequências

- intenção e autoridade permanecem independentes;
- workspaces podem divergir com o mesmo catálogo global;
- referências removidas do catálogo continuam diagnosticáveis;
- mudanças de manifest não concedem autoridade implicitamente;
- a resolução é testável sem filesystem e sem execução;
- a configuração permanece local ao workspace e não altera o registry legado.

## Limitações conhecidas

Capabilities são IDs abertos e não possuem catálogo fechado; a validação atual
garante somente forma sintática e declaração no manifest. A resolução não
executa adapters nem verifica autorização operacional final. Atualizações
administrativas têm efeito somente quando o consumidor posterior carregar um
novo snapshot; hot reload permanece fora do escopo.
