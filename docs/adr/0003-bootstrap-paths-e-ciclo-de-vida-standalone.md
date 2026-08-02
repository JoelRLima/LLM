# ADR 0003: bootstrap, paths e ciclo de vida standalone

- Status: aceito
- Data: 2026-07-30

## Contexto

O pacote histórico assumia que configuração, estado e workspace estavam
próximos do checkout. Imports expunham constantes relativas a `runtime/`, alguns
componentes consultavam o diretório atual e o bootstrap era recomposto pela
CLI e por scripts. Esse desenho funcionava durante o desenvolvimento, mas não
atendia às jornadas 1 e 8 do
[guia da plataforma](../plataforma-standalone.md): executar um wheel fora do
repositório e manter o mesmo ciclo ao trocar interface ou provider.

A transição também precisa preservar dados existentes. Copiar automaticamente
um `config.json` ou um diretório `runtime/` encontrado por proximidade tornaria
a origem ambígua e poderia misturar estado de workspaces diferentes.

## Decisão

### Composição única

`AgentApplication` é a raiz de composição independente de interface. Ela recebe
workspace, paths, configuração e, opcionalmente, um gateway de modelo injetado;
constrói sessão, skills e orquestrador; e possui o ciclo de vida dos recursos.

CLI, execução headless, testes e futuras interfaces devem atravessar essa
fronteira. Construir manualmente uma combinação paralela de `ChatSession`,
`SkillRegistry` e `Orchestrator` é compatibilidade temporária, não uma segunda
arquitetura pública.

O bootstrap segue esta ordem:

1. resolver paths sem tocar o filesystem;
2. validar e identificar o workspace explícito;
3. carregar e validar a configuração, ainda sem criar diretórios;
4. criar somente os diretórios próprios da aplicação;
5. adquirir o lock do workspace;
6. configurar logging e dependências;
7. executar tarefas;
8. persistir o estado controlado e liberar recursos em `close()`.

Falhas intermediárias liberam lock e logging. `close()` é idempotente. Duas
instâncias vivas não podem possuir simultaneamente o mesmo estado de workspace.
Como logging e captura do stdout legado ainda são recursos de processo, leases
de logging são contabilizados e chamadas a `run()`/`close()` são serializadas.

### Separação de localizações

`AppPaths` representa paths globais da instalação lógica, e `WorkspacePaths`
representa dados associados a um workspace. Seus construtores não criam
arquivos.

| Classe de dado | Escopo | Exemplos |
| :--- | :--- | :--- |
| configuração | aplicação | `config.json` versionado |
| dados duráveis | aplicação/workspace | memória e histórico |
| estado operacional | aplicação/workspace | checkpoint, métricas, relatórios e lock |
| cache | aplicação/workspace | scratch descartável |
| logs | aplicação | log do processo |
| arquivos do usuário | workspace explícito | código e documentos manipulados pelas tools |

`LLM_AGENT_HOME` oferece um root único e previsível para testes, ambientes
portáteis e operação administrada. Sem esse override, Windows usa
`APPDATA`/`LOCALAPPDATA` e sistemas Unix usam os diretórios XDG. O diretório de
instalação e o diretório atual não são destinos implícitos de dados da
aplicação.

O workspace é resolvido para path absoluto, precisa existir e recebe um
identificador estável derivado de sua raiz. Esse identificador particiona
memória, estado e cache. Skills que acessam arquivos recebem a raiz ou o scratch
por injeção; não descobrem seu escopo com `os.getcwd()`.

### Configuração versionada

`ConfigRepository` é a fronteira de configuração standalone:

- o default viaja como resource dentro do wheel;
- todo documento persistido possui `schema_version`;
- chaves, tipos, intervalos e versões futuras são validados estritamente;
- a precedência é override explícito de CLI, ambiente allowlisted, arquivo e
  default empacotado;
- configuração ausente ou inválida falha antes da construção do modelo;
- paths internos de estado não são armazenados como preferência do usuário.

`config init` cria o default de forma atômica e idempotente. `config migrate`
recebe uma origem explícita, preserva o arquivo original e falha diante de um
destino diferente já existente.

### Migração de estado

Não há descoberta ou migração automática do antigo `runtime/`. O comando
`state migrate --from <diretório>` copia somente itens allowlisted, valida
JSON, JSONL e SQLite quando aplicável, rejeita links simbólicos e preserva a
origem. Repetir a mesma migração é seguro; conflitos de conteúdo falham
fechados. A migração adquire o mesmo lock do workspace, faz preflight de todos
os itens antes da promoção e remove todos os destinos recém-promovidos se
qualquer cópia falhar.

### Interfaces operacionais

O console script `llm-agent` oferece:

- chat interativo como comando padrão;
- `run` para uma tarefa headless, com saída humana ou JSON;
- `doctor` offline, sem construir modelo nem testar conectividade;
- comandos de inicialização, inspeção, validação e migração de configuração;
- migração explícita do estado legado.

O modo headless nunca consulta stdin. A porta `ApprovalPort` representa
consentimento local: por padrão, uma ação que precisa de autoridade retorna
`blocked`; `run --yes` concede aprovação somente àquela execução. Os estados
`succeeded`, `failed`, `cancelled`, `blocked` e `unverified` atravessam a
fronteira e somente `succeeded` produz exit code de sucesso. O relatório do
doctor só é persistido com `--write-report`.

`--help`, `--version` e `config path` não inicializam a aplicação nem escrevem
no filesystem. Saída JSON contém um único documento em stdout.

### Compatibilidade

Constantes relativas legadas e fachadas da raiz permanecem temporariamente para
consumidores existentes. Código novo recebe `AppPaths`, `WorkspacePaths` ou
`WorkspaceContext`. A existência da fachada não autoriza novas dependências em
paths globais.

## Evidência de aceitação

A fase é aceita quando:

1. testes demonstram ausência de efeitos durante import e descoberta de paths;
2. dois workspaces não compartilham memória, checkpoint, métricas ou scratch;
3. inicialização, validação e migrações são atômicas, idempotentes e preservam a
   origem;
4. o wheel executa fora do checkout e não escreve no diretório atual nem em
   `site-packages`;
5. o artefato instalado resolve suas dependências declaradas, executa
   `--version`, `config init`, `doctor --json`, uma tarefa headless e uma
   revisão real que detecta um achado conhecido, além de bloquear tentativa de
   leitura fora do workspace pelas bordas de arquivo e subprocesso;
6. a suíte roda em Linux e Windows com os mesmos contratos.

## Consequências

- O produto passa a ter uma fronteira executável própria, sem confundir pacote,
  workspace e perfil do usuário.
- Scripts e futuras interfaces podem reutilizar `AgentApplication`, reduzindo
  composições divergentes.
- Migração exige uma ação explícita do usuário, mas evita apropriação silenciosa
  ou perda de dados.
- Locks simples impedem corrupção concorrente local. Um lock abandonado após
  término abrupto precisa de inspeção e remoção manual; recuperação autenticada
  e auditável continua responsabilidade de uma fase posterior.
- A porta local de consentimento foi entregue para escrita e workflows de
  código. O contrato unificado de capacidades para ferramentas e extensões
  externas continua responsabilidade de uma fase posterior.
