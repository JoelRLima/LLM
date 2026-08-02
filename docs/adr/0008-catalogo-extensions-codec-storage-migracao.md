# ADR 0008: codec, storage e migração do catálogo global de extensions

- Status: implementado localmente para as Etapas 2.2b–2.2f
- Data: 2026-08-01

## Contexto

O Gate 2.1 definiu snapshots imutáveis de catálogo e workspace. A Etapa 2.2a
definiu a representação persistida e multiplataforma de `manifest_path`. Esta
decisão completa a persistência do catálogo global sem conectá-la ao bootstrap,
CLI, workspace, planners ou runtime.

O registry legado continua em `extensions/registry.json`. O catálogo novo vive
em `extensions/catalog.json`; os arquivos podem coexistir e não há migração
automática.

## Modelo e responsabilidades

`ExtensionCatalog`/`ExtensionCatalogEntry` continuam sendo snapshots da Etapa
2.1. O documento persistido usa `ExtensionCatalogDocument` e
`PersistedCatalogEntry`, que exigem `PersistedManifestPath`. O documento não
possui operações de workspace nem conhece filesystem; duplicidade de ID e path
semântico é validada por seu construtor. O serviço administrativo coordena as
transformações, evitando dois catálogos públicos concorrentes.

Os módulos são separados por responsabilidade:

```text
extension_catalog_document.py  modelo imutável do documento
extension_catalog_codec.py     bytes UTF-8 ↔ documento validado
extension_catalog_storage.py   load/save atômico
extension_catalog_lock.py      exclusão cross-process entre writers
extension_catalog_service.py   add/remove/replace/validate/inspect
extension_catalog_migration.py migração explícita do registry legado
```

## Schema v1

```json
{
  "schema_version": 1,
  "extensions": {
    "demo.extension": {
      "manifest_path": "/opt/extensions/demo/manifest.json",
      "manifest_path_flavor": "posix",
      "manifest_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  }
}
```

`schema_version` é inteiro exato `1`; `bool` não é aceito. O ID é somente a
chave do mapa e deve seguir o validador canônico de `extension_state`. Entradas
exigem exatamente path, flavor e fingerprint. Campos desconhecidos, ausentes,
IDs inválidos, paths não canônicos, flavors inválidos, fingerprints inválidos e
paths semanticamente duplicados rejeitam o documento inteiro.

O codec rejeita BOM, UTF-8 inválido, arquivo vazio, JSON inválido, raiz
incorreta, constantes não finitas e chaves duplicadas em qualquer nível.
`encode_catalog` produz UTF-8 sem BOM, `ensure_ascii=False`, `indent=2`,
`sort_keys=True` e newline final. O round trip é determinístico.

## Storage e atomicidade

`ExtensionCatalogStorage` retorna documento vazio somente quando o arquivo não
existe; um arquivo presente ilegível é corrupção e falha explicitamente. O
save cria o diretório, codifica antes de tocar no destino, usa tempfile no
mesmo diretório, escreve todos os bytes, faz flush/fsync, promove com
`os.replace` e tenta fsync do diretório em POSIX. Em falhas antes da promoção,
o sistema tenta remover o tempfile sem mascarar a causa principal; uma falha
extrema do próprio cleanup pode deixá-lo no disco. Arquivos novos usam as
permissões restritivas do `mkstemp`; ao
substituir, as permissões existentes são copiadas quando possível. ACLs normais
do Windows permanecem sob responsabilidade do sistema.

O destino final não pode ser symlink. Reparse points Windows são rejeitados
quando a API expõe o atributo correspondente. Não há escrita parcial observável
por readers; falhas antes da promoção preservam o destino anterior.

## Exclusão entre writers

`ExtensionCatalogLock` usa um lock de arquivo persistente adjacente ao catálogo:
`fcntl.flock` em POSIX e `msvcrt.locking` em Windows. A posse é do kernel e é
liberada no encerramento do processo; o arquivo de lock não é removido por PID.
Cada mutação executa:

```text
lock → reload → validar/aplicar → save atômico → release
```

Lock ocupado e falhas de aquisição/liberação têm erros distintos. Não há retry
ou timeout implícito. A detecção de reparse points além de symlinks comuns e
fsync de diretório em sistemas POSIX que não o suportam são limitações
explicitamente tratadas como falhas/limitações de storage, não como segurança
silenciosa.

## Operações administrativas

- `add`: aceita path absoluto ou relativo com `base_dir` absoluto explícito,
  rejeita `~` e cwd implícito, lê os bytes uma vez, valida manifest, ID,
  protocolo e fingerprint, e rejeita conflito de ID/path ou drift.
- `remove`: remove somente a entrada, nunca o manifest físico ou workspaces;
  ID ausente é idempotente.
- `validate`: não altera o documento e classifica cada entrada como
  `unchanged`, `changed`, `missing`, `invalid` ou `incompatible`, retornando
  diagnósticos estruturados sem conteúdo do manifest.
- `replace`: exige ID e fingerprint observado anteriormente, valida o novo
  manifest antes do lock e recarrega o catálogo para rejeitar observações
  obsoletas. O mesmo resultado após sucesso é idempotente.
- `inspect`: separa catálogo válido de diagnóstico de corrupção, sem promover
  erro a catálogo vazio.

O path administrativo é convertido em absoluto somente na fronteira da
operação, sem resolver symlinks. O path persistido, a chave de comparação e o
path nativo de leitura permanecem representações distintas.

## Migração explícita

`migrate_legacy(legacy_registry_path, destination_catalog_path, base_dir=...)`
é uma operação separada. Os paths do registry e destino devem ser absolutos;
paths de manifest relativos exigem `base_dir` absoluto. O parser legado rejeita
JSON inválido, BOM, duplicatas, IDs inválidos, campos desconhecidos,
`manifest_path` ausente e `enabled` que não seja booleano real. `enabled=true`,
`enabled=false` e ausência de `enabled` produzem somente entradas globais; não
criam workspace nem grants.

Todas as entradas são lidas, validadas e fingerprintadas antes de qualquer
promoção. Diagnósticos múltiplos são retornados em erro e o destino permanece
intacto. O registry legado é preservado byte a byte. Destino semanticamente
igual é idempotente; destino diferente é conflito; destino corrompido falha.
Não há backup, remoção ou chamada automática no bootstrap/import/CLI.

## Compatibilidade e limites

Os consumidores atuais continuam usando `ExtensionRegistry` e
`extensions_registry_file`. `AppPaths` somente expõe os novos paths
`extensions_catalog_file` e `extensions_catalog_lock_file`; nenhum consumidor
runtime foi conectado ao catálogo. Protocolo stdio 1.0, `invocation_id`,
launcher, cleanup, ToolRegistry e AgentApplication permanecem sem alteração
comportamental. A API histórica por path do Gate 1 mantém seus tipos, mensagens,
imports e objeto público; a política estrita é exclusiva do catálogo.

Ficam para etapas posteriores: habilitação por workspace, grants, autorização,
loader/factory, CLI, bootstrap, runtime path expansion, hot reload,
marketplace, download, sandbox, artifacts gerais e scanner do TCC.

## Correção local posterior

O patch corretivo mantém duas políticas explícitas para o parser de manifests:
`legacy_stdio_compatibility`, usado pela API histórica por path do Gate 1, e
`strict_catalog`, usado pelo catálogo persistido. A primeira preserva duplicate
keys, constantes JSON aceitas pelo parser histórico, `UnicodeDecodeError` para
UTF-8 inválido e a exposição de `SUPPORTED_PROTOCOL`; a segunda rejeita esses
casos conforme o contrato estrito do catálogo e fornece erros tipados de
estrutura e protocolo.

Falhas de storage são tipadas, o descriptor retornado por `mkstemp` permanece
com ownership explícito até a transferência para `fdopen`, e write/flush/fsync
são causas primárias. Falhas de `close` e cleanup são tentadas e registradas
como secundárias sem substituir a causa; a coleção secundária também é
acessível sem depender de `BaseException.add_note`.

Depois de `os.replace` não existe rollback; falha de fsync de diretório apenas
marca durabilidade incerta. A troca concorrente do diretório-pai não é eliminada
por handles seguros e permanece fora do threat model atual. Proteções de
symlink/reparse são best effort. Dependências injetadas da migração precisam
apontar para o mesmo destino e lock declarados.

`inspect` captura somente erros esperados de catálogo/storage. Diagnósticos
públicos usam mensagens estáveis e não copiam conteúdo do manifest. Conflitos
de versão, ID, path e replace possuem classes tipadas. O replacement exato é
idempotente depois do sucesso, enquanto alterações concorrentes continuam sendo
rejeitadas pelo compare-and-swap. Writers usam lock nativo e reload sob lock;
a migração não sobrescreve um destino promovido por outro writer.
