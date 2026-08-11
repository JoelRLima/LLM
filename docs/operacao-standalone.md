# Operação standalone

> **STATUS: CURRENT — OPERATION GUIDE.** Escopo e limites do produto ficam em
> [plataforma-standalone.md](plataforma-standalone.md); contratos internos no
> [índice técnico](README.md).

## Instalação e primeiro uso

O runtime básico requer Python 3.10 ou superior:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install .
llm-agent config init
llm-agent config path
llm-agent doctor
```

Edite o arquivo exibido por `config path`, principalmente
`model_profiles.local_8gb.base_url` e `model`. O diagnóstico é offline e não
faz uma requisição ao backend.

## Comandos

```text
llm-agent [chat]
llm-agent run [--task-authority CAPABILITY]... [--json] [--yes] OBJETIVO
llm-agent doctor [--json] [--write-report]
llm-agent config init
llm-agent config path
llm-agent config validate
llm-agent config migrate --from ARQUIVO
llm-agent state migrate --from DIRETÓRIO
llm-agent extensions list|register|enable|disable|grant|revoke|inspect
```

As flags comuns `--home`, `--config`, `--workspace` e `--profile` são aceitas
antes ou depois do subcomando. Sem `--workspace`, a interface usa o diretório
atual; automações devem sempre informar a raiz explicitamente.

`--help`, `--version` e `config path` não constroem a aplicação nem escrevem no
filesystem. `doctor` também não constrói modelo, skills ou orquestrador.

### Execução headless e aprovação

`run` nunca lê `stdin`. O fluxo model-actionable de escrita usa `code_task`;
`file_writer` permanece low-level/admin e é excluído das views do modelo.
Efeitos e validadores que exigem consentimento passam por approval; a política
headless padrão devolve `blocked` e o efeito não ocorre, inclusive para
propostas de alta confiança. `--yes` concede aprovação somente naquela execução
e não cria grants ou authority:

```powershell
llm-agent run --workspace C:\projetos\app --json "Revise o código"
llm-agent run --workspace C:\projetos\app --yes "Aplique a alteração solicitada"
```

Estados públicos:

| Status | Significado | `success` |
| :--- | :--- | :---: |
| `succeeded` | objetivo concluído e verificado conforme o caso de uso | `true` |
| `blocked` | policy de execução ou aprovação requerida impediu o efeito | `false` |
| `unverified` | efeito aplicado sem validator disponível | `false` |
| `cancelled` | cancelamento confirmado | `false` |
| `failed` | bootstrap, execução, validação ou persistência falhou | `false` |
| `timed_out` | limite de tempo encerrou a invocation | `false` |
| `permission_denied` | authority, capability ou eligibility recusou a invocation | `false` |
| `protocol_error` | adapter/protocolo retornou envelope inválido | `false` |
| `unavailable` | capability ou provider não está disponível | `false` |

Saída `--json` contém exatamente um documento em stdout. Logs ficam fora desse
stream. Exit code `0` representa sucesso, `1` um estado operacional não
concluído e `2` erro de uso, configuração, workspace ou migração.

### Diagnóstico

`doctor` verifica versão do pacote/Python, paths da aplicação, schema e perfil
efetivos, provider suportado, workspace, tamanho do estado e integridade da
memória JSON/SQLite. O check é somente leitura e usa `PRAGMA quick_check`;
estado corrompido torna `offline_ready` falso. Ele declara conectividade como
`not_checked`.

O relatório distingue `read_write`, `read_only` e `unavailable`. Um workspace
somente leitura permite inicialização e análise, mas não alteração. O relatório
só é persistido quando `--write-report` é informado.

## Paths da aplicação

`AppPaths` separa configuração, dados, estado, cache e logs. `WorkspacePaths`
particiona dados e estado pelo identificador estável da raiz absoluta do
workspace.

Com `--home DIR` ou `LLM_AGENT_HOME=DIR`:

```text
DIR/
├── config/config.json
├── data/
│   ├── extensions/
│   └── workspaces/<workspace-id>/
├── state/workspaces/<workspace-id>/
├── cache/workspaces/<workspace-id>/
└── logs/agent.log
```

Sem override:

| Sistema | Configuração | Dados/estado/cache/logs |
| :--- | :--- | :--- |
| Windows | `%APPDATA%\local-llm-agent` | `%LOCALAPPDATA%\local-llm-agent\...` |
| Unix | `$XDG_CONFIG_HOME/local-llm-agent` | roots XDG de data, state e cache |

Fallbacks XDG seguem `~/.config`, `~/.local/share`, `~/.local/state` e
`~/.cache`. `AGENT_RUNTIME_DIR` existe apenas como ponte legada.

Memória, checkpoint, métricas, relatórios, artifacts, restore points,
histórico, benchmark, scratch e lock pertencem à partição do workspace.
Nenhum deles é gravado no pacote instalado ou inferido de um `runtime/` próximo.

## Configuração efetiva

O documento persistido usa `schema_version: 1`. Chaves desconhecidas, tipos
inválidos, versão ausente e versões futuras falham antes da construção do
modelo.

Precedência, da maior para a menor:

1. overrides explícitos da interface;
2. variáveis de ambiente allowlisted;
3. arquivo selecionado;
4. default empacotado no wheel.

Variáveis suportadas:

| Variável | Destino |
| :--- | :--- |
| `LLM_AGENT_API_URL` | endpoint do perfil selecionado |
| `LLM_AGENT_MODEL` | modelo do perfil selecionado |
| `LLM_AGENT_TEMPERATURE` | temperatura do perfil selecionado |
| `LLM_AGENT_MAX_TOKENS` | saída do perfil selecionado |
| `LLM_AGENT_TIMEOUT` | timeout do perfil selecionado |
| `LLM_AGENT_DEFAULT_MODEL_PROFILE` | perfil selecionado |
| `LLM_AGENT_HARDWARE_PROFILE` | perfil de hardware |
| `LLM_AGENT_MAX_MODEL_CONCURRENCY` | gate de inferência |
| `LLM_AGENT_MAX_IO_CONCURRENCY` | concorrência de I/O |
| `LLM_AGENT_MAX_PROCESS_CONCURRENCY` | concorrência de processos |
| `LLM_AGENT_MAX_MODEL_CALLS` | orçamento de chamadas |
| `LLM_AGENT_AUTO_CONFIRM` | compatibilidade de consumidores legados |
| `LLM_AGENT_ENABLE_GBNF` | modo estruturado do perfil selecionado |

As cinco primeiras são materializadas dentro do perfil selecionado; não ficam
como valores paralelos ignorados. `--profile` também é considerado por
`config validate`, `doctor` e pela aplicação.

Paths de memória, checkpoint ou relatório não são preferências persistidas.
Eles são injetados depois que o workspace foi identificado.

## Migração

Não existe adoção automática de `config.json` ou `runtime/` legado:

```powershell
llm-agent config migrate --from C:\legado\config.json
llm-agent state migrate --workspace C:\projetos\app --from C:\legado\runtime
```

As duas operações preservam a origem. A configuração remove paths internos
antigos. A migração de estado:

- aceita somente nomes allowlisted;
- valida JSON, JSONL e SQLite antes de promover arquivos;
- rejeita links simbólicos e conflitos;
- adquire o lock do workspace;
- reverte tudo o que promoveu se uma cópia falhar;
- é idempotente para conteúdo igual.

## Lifecycle e concorrência

`AgentApplication` carrega e valida a configuração sem efeitos, cria os
diretórios próprios, adquire um lock por workspace e então configura logging,
memória, skills e orquestração. `close()` é idempotente e sempre libera lock e
logging.

A persistência JSON de memória usa tempfile, `fsync` e `os.replace`; falha no
save automático transforma a tarefa em falha e preserva o checkpoint. Captura
de stdout é serializada dentro do processo. Logging permite múltiplos owners
somente quando usam o mesmo destino; configurações incompatíveis falham
explicitamente.

O lock atual é conservador: um arquivo abandonado após término abrupto não é
removido automaticamente, porque apenas PID não prova propriedade com segurança.
Recuperação auditável de lock é trabalho futuro.

O timeout do benchmark é cooperativo. Após solicitar cancelamento, o script
aguarda a tarefa em voo terminar e não reutiliza a aplicação.

Subprocessos recebem o workspace como `cwd`, mas essa propriedade isoladamente
não confina caminhos absolutos ou argumentos como `../`. Skills de subprocesso
validam cada path relevante contra a raiz antes de executar; tentativas de
acessar uma sentinela externa falham sem expor seu conteúdo.

Isso não é uma sandbox forte: `pytest`, por exemplo, executa código do projeto,
que continua sujeito às permissões do processo do usuário. A fase 1 fecha
escapes diretos de argumento; isolamento de processo permanece uma limitação
deliberada, enquanto a autorização unificada por capability já está aplicada
no gateway.

### Estado da capability de comandos no Marco 1

Nota de estado atual: a autorizacao unificada por capability nao e trabalho
futuro; ela ja e aplicada pelo `ToolInvocationGateway`, com grants de
aplicacao, tarefa, persona e ferramenta. Approval permanece uma camada de
efeito separada e nao substitui essa verificacao.

A `ShellSkill` model-actionable nao e um shell arbitrario. Ela aceita somente
`ruff check` com caminhos do workspace, metadados de historico local via `git log` e `tree` quando
disponivel, com `shell=False`, ambiente explicito, paths validados e streams
bounded. Em `git log`, o modelo so pode informar as formas lowercase exatas sem max-count, `-N`, `-n N`
ou `--max-count[=]N` com digitos ASCII de 1 a 1000; `-nN` anexado e rejeitado.
`git status` e `git diff` foram
reduzidos da superficie model-actionable para evitar a execucao transitiva de
filtros de conteudo configurados no workspace.
`pytest` e `mypy` foram removidos da allowlist porque podem executar codigo
controlado pelo workspace ou plugins. Nao ha sandbox tecnica de filesystem ou
rede; ambiente filtrado e validacao de paths nao garantem isolamento transitivo.
Os nomes permitidos nao selecionam qualquer binario: Git, Ruff e tree so sao
executados quando um caminho absoluto/canonico confiavel fora do workspace pode
ser resolvido, sem candidato textual ou destino final controlado pelo
workspace. Symlinks, junctions e cadeias equivalentes locais nao podem trocar a
identidade efetivamente executada. Uma instalacao local de Ruff dentro do
workspace pode, portanto, ficar indisponivel. Em `git log`, o runtime fixa
`--pretty=medium` e `--no-patch`, desabilita lazy fetch e merge remerging e
rejeita formatos, flags de diff, pathspecs, assinatura e helpers selecionados
pelo modelo, evitando dependencia de `format.pretty` e aliases `pretty.*` do
repositorio.

## Administracao de extensions

O fluxo de produto usa o catalogo moderno e a configuracao por workspace:

```powershell
llm-agent extensions register C:\ext\manifest.json --home C:\app --workspace C:\projeto
llm-agent extensions enable demo.extension --home C:\app --workspace C:\projeto
llm-agent extensions grant demo.extension read --home C:\app --workspace C:\projeto
llm-agent extensions grant demo.extension process --home C:\app --workspace C:\projeto
llm-agent extensions inspect --json --home C:\app --workspace C:\projeto
```

Cada comando `extensions grant` concede uma capability persistente no workspace;
conceda cada capability declarada pelo manifest separadamente, lembrando que uma
extension stdio sempre exige `process` (e `read` somente quando declarado). Isso
nao cria task authority nem approval.

Tambem estao disponiveis `extensions list`, `extensions disable` e
`extensions revoke ID CAPABILITY`. O estado persistente fica no catalogo
global e no arquivo de extensions do workspace; esses comandos nao escrevem no
registry legado de `llm-agent tools`.

Para executar uma tarefa com uma extension, forneca authority de tarefa de
forma explicita e independente do modelo:

```powershell
llm-agent run --workspace C:\projeto --task-authority read --task-authority process --yes --json "Execute a ferramenta"
```

Repita `--task-authority` para capabilities adicionais; o snapshot tem escopo
de toda a tarefa e nao direciona nem persiste grants ou persona. A flag `--yes`
e somente approval e nao substitui authority. A ausencia ou insuficiencia de
authority termina sem iniciar o processo stdio.

## Verificação do artefato

O gate completo cria um wheel, instala-o com dependências em venv limpo e roda
fora do checkout:

```powershell
python scripts\verify_installed_package.py
```

Esse comando usa build PEP 517 isolado e precisa de índice ou wheelhouse. Em um
ambiente local que já possui os build requirements:

```powershell
python scripts\verify_installed_package.py --no-build-isolation
```

`--offline-diagnostic` é propositalmente mais fraco: reutiliza packages do
interpretador base e não valida a completude das dependências. Ele não substitui
o gate de aceitação.

Além da CLI, o gate instalado executa review real de código, verifica um
diagnóstico `PYSEC001`, tenta escapar do workspace por file reader, shell e Git,
e compara sentinelas, o workspace, o diretório de execução e `site-packages`
antes/depois.

Estado atual: a autorizacao unificada por capability ja e aplicada pelo
ToolInvocationGateway, combinando grants de aplicacao, tarefa, persona e
ferramenta. Approval continua sendo uma camada separada de efeito; a frase
historica sobre essa autorizacao ser trabalho futuro nao descreve o runtime
atual. O runner paralelo tambem preserva correlacao por `step_id` e
`invocation_id`, registra cache no finalizer e nao deixa REPLAN abandonar
siblings iniciados.
