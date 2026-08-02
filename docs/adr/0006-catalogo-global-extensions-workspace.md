# ADR 0006: catálogo global de extensions e habilitação por workspace

- Status: aceito
- Data: 2026-08-01

## Contexto

O Gate 1 tornou possível executar uma extension stdio por meio de um manifest
validado e do `ToolRegistry`, mas o estado administrativo ainda mistura duas
perguntas diferentes: quais extensions são conhecidas pela instalação e quais
estão habilitadas em um workspace. O `ExtensionRegistry` legado armazena um
path e uma flag global `enabled`; essa flag não pode representar corretamente
workspaces independentes.

Esta decisão define somente os modelos canônicos de estado. Persistência,
CLI, bootstrap, factory de adapters, planner, gateway e resolução de
entrypoint permanecem etapas posteriores do Gate 2.

## Decisão

### Catálogo global

O catálogo global representa extensions conhecidas pela instalação do usuário.
Cada entrada contém:

- `extension_id`, derivado exatamente de `manifest.id`;
- `manifest_path`, preservado como referência de origem;
- `manifest_sha256`, fingerprint SHA-256 dos bytes exatos do manifest validado.

O catálogo não habilita uma extension, não contém grants, secrets,
configuração arbitrária, processos ou adapters. A operação de registro é
idempotente quando ID, path e fingerprint são iguais. O mesmo ID com outra
origem ou fingerprint é rejeitado; o mesmo path sob outro ID também é
rejeitado. A substituição será uma operação administrativa explícita em etapa
posterior.

O modelo não verifica se o path existe e não lê o manifest. Essas operações
pertencem à camada de validação e persistência.

Na Etapa 2.1, `manifest_path` usa igualdade lexical do `Path` nativo. O modelo
não resolve `.` ou `..`, symlinks ou existência; essa semântica vale somente
para os objetos em memória. Antes da serialização da Etapa 2.2 será definida a
representação persistida e multiplataforma, incluindo a política de comparação
entre Windows e POSIX. Nenhuma persistência deve ser implementada enquanto
essa decisão estiver aberta.

### Identidade canônica

`manifest.id` é global, estável, ASCII, minúsculo, não vazio e não derivado do
path. A forma aceita é um identificador simples contendo caracteres
alfanuméricos minúsculos, ponto, sublinhado ou hífen, começando e terminando
com caractere alfanumérico. Ponto, sublinhado e hífen funcionam como
separadores simples; separadores consecutivos são rejeitados. Valores em
maiúsculas são rejeitados, sem normalização silenciosa.

O nome da tool continua sendo o nome declarado no manifest. Conflitos de nomes
de tools serão tratados pela factory/bootstrap, não por estes modelos.

### Estado do workspace

O workspace mantém somente seleções por `extension_id`. A presença de uma
seleção significa que a extension está habilitada naquele workspace. Cada
seleção contém `granted_capabilities`, como uma coleção ordenada e imutável de
strings não vazias e sem duplicatas.

Uma coleção de capabilities vazia é válida: ela representa uma extension
habilitada sem autoridade concedida. Isso preserva a distinção entre:

```text
extension registrada
!= extension habilitada
!= capability concedida
!= aprovação de uma invocação
```

O modelo permite referências a IDs ausentes do catálogo. Essas referências
órfãs não são removidas silenciosamente e poderão ser diagnosticadas quando o
catálogo e o workspace forem combinados.

Todos os métodos de alteração retornam novos snapshots. Não há hot reload nem
estado mutável compartilhado entre workspaces.

### Fingerprint e formatos futuros

O fingerprint usa SHA-256 sobre os bytes exatos recebidos. Whitespace ou
ordenação diferente no JSON pode produzir fingerprint diferente; isso é
intencional, pois o fingerprint representa o arquivo real.

Os documentos persistidos futuros usarão `schema_version: 1`, com formas
conceituais equivalentes a:

```json
{
  "schema_version": 1,
  "extensions": {
    "demo.extension": {
      "manifest_path": "...",
      "manifest_sha256": "..."
    }
  }
}
```

e:

```json
{
  "schema_version": 1,
  "enabled_extensions": {
    "demo.extension": {
      "granted_capabilities": ["read"]
    }
  }
}
```

Nenhum arquivo é lido ou escrito nesta etapa.

### Migração do registry legado

Quando a persistência for implementada, uma entrada do registry legado com
`enabled` global será preservada no catálogo, mas não habilitará
automaticamente a extension em nenhum workspace. O usuário deverá habilitá-la
explicitamente no workspace desejado. A migração não será apresentada como
entregue pela Etapa 2.1.

### Decisão obrigatória inicial da Etapa 2.2

Definir representação persistida canônica de `manifest_path` e política de
comparação Windows/POSIX.

### Decisões operacionais reservadas

As decisões abaixo ficam registradas para as etapas posteriores, sem alterar
os contratos nesta etapa:

- o timeout será propriedade operacional do adapter ou de uma interface
  interna, sem adicionar campo ao `ToolDescriptor` público sem necessidade;
- o cwd da extension continuará sendo o workspace;
- a resolução futura poderá substituir somente `${extension_dir}` pelo diretório
  do manifest e `${python}` por `sys.executable`; não haverá expansão geral de
  ambiente;
- cada `AgentApplication` usará snapshot imutável das extensions ativas, e
  mudanças administrativas terão efeito no próximo bootstrap;
- extension inválida não deverá derrubar builtins nem publicar tools
  parcialmente.

## Alternativas rejeitadas

- Uma única flag global `enabled`, porque vaza estado entre workspaces.
- Cópia integral do manifest em cada workspace, porque duplica a fonte de
  verdade.
- Habilitação automática durante o registro, porque registro não é
  autorização.
- Namespaces automáticos para tools neste Gate, porque alterariam prompts e
  planos antes de haver necessidade comprovada.
- Normalização silenciosa de IDs, porque esconderia erro de identidade.
- Leitura ou escrita de arquivos pelos modelos, porque mistura domínio puro
  com persistência.
- Hot reload, marketplace, hierarquia genérica de plugins ou sandbox, porque
  estão fora do escopo do Gate 2.

## Consequências

- O catálogo global e o workspace passam a ter fontes de verdade distintas.
- A migração futura poderá preservar registros antigos sem habilitar efeitos
  em todos os workspaces.
- Grants podem ser avaliados posteriormente pelo `AuthorizationContext` sem
  alterar a identidade da extension.
- Paths ausentes e referências órfãs continuam representáveis para
  diagnóstico.
- A factory futura poderá produzir um snapshot runtime sem mutar o catálogo ou
  o estado persistido.

## Fora do escopo desta decisão

Persistência, migração efetiva, CLI, bootstrap, factory de adapters, planner,
gateway, timeout, tokens de entrypoint, autorização operacional, hot reload,
conflitos de nomes de tools, artifacts duráveis e integração do TCC.
