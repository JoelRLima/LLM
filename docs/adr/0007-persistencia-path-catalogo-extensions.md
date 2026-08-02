# ADR 0007: representação persistida de paths do catálogo de extensions

- Status: aceito para a Etapa 2.2a
- Data: 2026-08-02

## Contexto

A Etapa 2.1 usa `Path` nativo somente em snapshots em memória. Esse tipo não
carrega informação suficiente para representar um path Windows quando o
catálogo é inspecionado em POSIX, nem para declarar a política de comparação
de case. A persistência do catálogo precisa separar o texto salvo, a chave de
comparação e o futuro path usado pelo runtime.

## Decisão da Etapa 2.2a

O documento persistido usará `manifest_path` absoluto e lexicalmente canônico,
com separadores `/`, acompanhado de `manifest_path_flavor`:

```json
{
  "manifest_path": "C:/Users/user/extensions/demo/manifest.json",
  "manifest_path_flavor": "windows"
}
```

ou:

```json
{
  "manifest_path": "/home/user/extensions/demo/manifest.json",
  "manifest_path_flavor": "posix"
}
```

O modelo recebe a forma já absoluta e canônica. Ele não transforma paths
relativos, não consulta cwd ou home, não lê variáveis de ambiente e não acessa
o filesystem.

### Semântica lexical

- Windows aceita drives absolutos e UNC;
- POSIX aceita somente uma raiz `/`;
- `.` e `..` são rejeitados, pois já deveriam ter sido eliminados na borda
  administrativa;
- separadores repetidos são rejeitados, exceto o prefixo `//` de UNC;
- backslash não é aceito no texto persistido;
- raiz de drive, raiz POSIX e raiz de share UNC têm formas próprias e não
  recebem uma barra final adicional;
- Unicode é preservado quando não introduz separador ou caractere de controle;
- a grafia persistida não é alterada por comparação.

Windows compara com a mesma equivalência lexical de `PureWindowsPath`: a
representação POSIX da pure path é comparada com `lower()`, sem a expansão
Unicode adicional de `casefold()`. Assim, o case ASCII de Windows é ignorado,
mas grafias como `Straße` e `Strasse` não colidem. POSIX compara com distinção
de case usando `PurePosixPath`. Flavors diferentes nunca são equivalentes.

`manifest_path_flavor` aceita somente as strings exatas `windows` e `posix`.
`PersistedManifestPath.__eq__`, `hash()` e `equivalent_to()` usam a mesma
identidade `(flavor, comparison_key)`; a grafia original continua preservada
em `persisted_value`.

No schema v1, Windows suporta somente drives absolutos comuns e UNC comum.
Namespaces de device com prefixos `//?/` ou `//./` são rejeitados.

Symlinks são preservados como texto. Nenhum `resolve()`, `realpath()`, stat ou
teste de existência é executado. O fingerprint posterior continuará sendo
calculado sobre os bytes que o path fornecer no momento da validação.

Paths de flavor estrangeiro podem ser representados e inspecionados para
diagnóstico. Não são convertidos para `Path` nativo nem usados
operacionalmente em um host incompatível.

### Separação de representações

```text
path persistido    = texto absoluto + flavor
chave de comparação = PureWindowsPath/PurePosixPath conforme o flavor
path runtime       = conversão posterior apenas quando o flavor do host for compatível
```

O tipo puro `PersistedManifestPath` implementa somente as duas primeiras
representações. A conversão runtime pertence a uma etapa posterior.

## Decisões reservadas para etapas posteriores

- `catalog.json` será armazenado ao lado do `registry.json` legado;
- a migração do registry legado será explícita, preservará a origem e será
  all-or-nothing;
- corrupção do documento persistido será tratada fail-fast;
- fingerprints não serão atualizados silenciosamente;
- writers precisarão de exclusão mútua;
- o mecanismo concreto de lock será definido na Etapa 2.2c;
- a representação JSON, codec, bytes, storage, migração e operações de catálogo
  ainda não estão implementados;
- mudança de máquina não é suporte operacional do schema v1, embora paths
  estrangeiros possam ser inspecionados.

## Alternativas rejeitadas

- persistir path relativo ao diretório global, porque extensions externas são
  permitidas;
- persistir URI `file://`, porque acrescenta complexidade de encoding, drive e
  UNC sem benefício necessário nesta etapa;
- resolver symlinks ou usar `Path.resolve()`, porque isso altera identidade e
  exige semântica do filesystem;
- converter path estrangeiro para `Path` nativo, porque produz interpretação
  operacional incorreta;
- normalizar case no texto persistido, porque esconderia a grafia original.

## Consequências

- o codec futuro poderá rejeitar formas não canônicas sem coerção silenciosa;
- Windows e POSIX terão comparação determinística e explicitamente distinta;
- um catálogo de outro flavor continua disponível para diagnóstico;
- o path runtime não será confundido com identidade persistida;
- o schema v1 permanece local à máquina e não promete relocação automática.

## Fora do escopo da Etapa 2.2a

JSON, bytes, codec, `catalog.json`, storage, lock, add/remove/replace,
fingerprint de manifest, drift, migração, CLI, bootstrap, workspace, grants,
adapters, ToolRegistry, planner, gateway e runtime.
