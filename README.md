# LLM Agent

LLM Agent é um agente local de desenvolvimento, instalável por Python, com CLI,
planning linear/hierárquico, leitura e busca no workspace, workflows de mudança
com validação e execução multitarefa local. Um único runtime coordena as tarefas;
o projeto não é uma plataforma distribuída de multiagentes.

## O que está disponível

- leitura e busca confinadas ao workspace;
- alterações suportadas por `code_task` → `ChangeSet` → `ProjectValidator`;
- Shell/Git/Ruff em superfície reduzida e allowlisted, sem shell arbitrário;
- memória persistente por workspace; busca semântica é opcional;
- extensions stdio 1.0 condicionadas a catálogo, configuração e authority
  explícita da tarefa;
- web search para a persona apropriada, sujeita a política/aprovação de rede.

`file_writer` ainda existe para consumidores low-level/admin, mas não é
model-actionable. MCP, sandbox universal de sistema operacional e instalação de
pacotes pelo modelo não são fornecidos. Discovery ou aprovação não concedem
authority. A [matriz técnica completa](docs/README.md#matriz-current-de-capabilities)
explica cada boundary.

## Instalação rápida

Requer Python 3.10+ e um endpoint OpenAI-compatible configurado.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
llm-agent config init
llm-agent config path
llm-agent doctor
```

Edite o profile indicado por `config path` com endpoint e modelo. Em Linux ou
macOS, ative o ambiente com `source .venv/bin/activate`. A instalação core não
inclui a stack opcional de memória semântica; use `.[ml]` quando necessário.

## Produto instalado (WAVE 18)

O fluxo acima continua sendo o fluxo de desenvolvimento editável: o checkout e
o `.venv` pertencem ao desenvolvedor. O produto instalado W18 é um fluxo
separado, normativo para Windows x64 e para o usuário atual, sem administrador.

Use um bundle W18 extraído localmente e execute o wrapper que está dentro dele:

```powershell
Expand-Archive .\local-llm-agent-0.2.0rc1-windows-x64.zip -DestinationPath .\w18-bundle
Set-Location .\w18-bundle
.\install.cmd
```

O instalador encontra o próprio bundle pela localização do script; não use pipe
de script remoto. A instalação do usuário é offline: o ZIP já contém o CPython
embutido, as dependências resolvidas e a aplicação. O installer apenas valida,
extrai, testa, promove e atualiza o User PATH; não baixa artefatos, não invoca
uv/pip e não cria venv. uv/pip/Python provisioning existem somente no release build.
Depois da instalação, o uso diário é somente o comando estável `llm-agent`:

```powershell
Set-Location C:\Windows\System32
llm-agent --version
llm-agent --help
llm-agent config init
llm-agent doctor --json
```

Executar o instalador novamente é seguro para o mesmo candidato e faz upgrades
por staging quando o bundle traz outro candidato. Falha de validação ou de
aceitação preserva o candidato anterior. `uninstall.cmd` remove apenas o runtime
W18, o launcher estável e o segmento próprio do User PATH; configuração, dados,
estado, cache, logs e estado por workspace são preservados. A reinstalação pode
reutilizar esse estado.

Os nomes atuais (`LLM Agent`, `llm-agent` e `local-llm-agent`) são provisionais.
O namespace de estado é separado da identidade de distribuição para permitir um
alias futuro sem mover dados existentes. W18 não publica PyPI, MSIX ou WinGet;
os scripts PowerShell não são um executável nativo first-party assinado. O
produto instalado normativo desta wave é Windows x64; o fluxo Ubuntu continua
sendo compatibilidade de pacote, não um instalador equivalente.

## Uso

```powershell
llm-agent chat
llm-agent run --workspace C:\caminho\projeto "Analise este repositório"
llm-agent run --workspace C:\caminho\projeto --json "Resuma o projeto"
llm-agent task status --workspace C:\caminho\projeto
llm-agent task status --workspace C:\caminho\projeto --json
llm-agent task resume --workspace C:\caminho\projeto
llm-agent task resume --workspace C:\caminho\projeto --json
llm-agent run --workspace C:\caminho\projeto --yes "Aplique a alteração"
llm-agent inspect list --json
llm-agent inspect show --json --run-id RUN_ID
llm-agent inspect replay --json --run-id RUN_ID
llm-agent inspect export --run-id RUN_ID --output trace.zip
```

### Shell interativo

`llm-agent chat` e o comando sem subcomando sao superficies humanas e exigem
stdin e stdout TTY. Em pipe ou redirecionamento, use `run`, `task` ou as rotas
JSON; o chat falha rapidamente sem consumir stdin nem inicializar a UI.

O compositor usa uma sessao Prompt Toolkit unica: Enter envia, Ctrl-J insere
uma nova linha, historico e completion de comandos slash ficam no compositor,
e a saida de background aparece acima do draft. O shell preserva scrollback
nativo e nao captura mouse. `/status`, `/where`, `/timeline` e `/details`
mostram orientacao bounded sem fazer uma nova chamada de modelo.

Enquanto uma tarefa executa, texto comum vira follow-up pendente bounded; ele
nao e enviado automaticamente. Use `/pending`, `/pending edit ID`,
`/pending discard ID` ou `/pending send ID` depois do settlement. `/cancel`
somente solicita cancelamento; o worker continua dono do settlement e do
checkpoint. `/attention approve` e `/attention deny` sao as acoes explicitas
para a aprovacao atual; texto comum, inclusive `y`, nunca aprova uma operacao.

`/model select PROFILE` e `/workspace switch PATH` exigem uma sessao ociosa e
recriam a aplicacao atraves dos owners canonicos. A primeira execucao oferece
configuracao guiada, profile/modelo/endpoint e escolha de workspace. Nenhuma
rota de UX inicia ou supervisiona um backend persistente.

Para uma visao tecnica bounded, use `/debug` para alternar OFF/DIAG/VERBOSE e
`/help` para os comandos preferidos. O registro de comandos apenas roteia a
entrada; autoridade, approval, workspace, task/checkpoint e perfil de modelo
continuam pertencendo aos owners de runtime. Queries locais (`/ls`, `/read`,
`/find`, `/git-status`, `/diff`) usam um executor read-only separado da tarefa.

### Diretivas por tarefa (W11)

As diretivas e perfis deliberativos usam prefixos slash na entrada da tarefa:

```powershell
llm-agent run --workspace C:\\caminho\\projeto "/read /smart Analyze the repository"
llm-agent run --workspace C:\\caminho\\projeto "/plan /cautious Refactor parser.py"
llm-agent run --workspace C:\\caminho\\projeto "/do Apply the change"
llm-agent run --workspace C:\\caminho\\projeto "/continue"
```

`/read` é uma diretiva de tarefa diferente do comando interativo `/read <arquivo>`;
o dispatcher do chat preserva o segundo significado. `/do` não
concede authority nem substitui `--yes`. `/plan` valida e mostra um preview,
mas não executa o plano. `/continue` reutiliza o checkpoint e o owner de
continuidade do W10, sem iniciar uma tarefa nova. `/economy`, `/normal`,
`/smart` e `/cautious` são perfis de deliberação, não o `--profile` de modelo;
eles não selecionam provider ou modelo.

`run` é headless e nunca lê `stdin`. Se uma ação exigir consentimento e `--yes`
não estiver presente, ela termina bloqueada sem executar o efeito. `--yes`
fornece aprovação para aquela execução; não cria capability, grant ou authority
e não transforma validação ausente em sucesso.

`--home DIR` fornece uma raiz portátil para configuração e estado; sem override,
paths são resolvidos nos diretórios de usuário do sistema. O pacote instalado
não é usado como diretório gravável.

Rotas headless que criam ou retomam tarefas exigem `--workspace` explícito e
falham antes do bootstrap quando ele não é informado; o chat interativo mantém
seu chooser de workspace. Perfis OpenAI-compatible podem guardar somente a
metadata de uma `credential_ref` de ambiente/bearer; o valor é resolvido tarde,
apenas no transporte HTTP, e não aparece em trace ou estado comum.

`llm-agent inspect` é uma superfície somente leitura para traces de runs ativos
ou históricos. A trace é redigida, possui completude explícita e não é
checkpoint, outcome, memória ou autoridade da tarefa. Veja o
[guia de observabilidade e inspector](docs/observability-inspector.md).

### Continuidade de tarefas

Cada workspace possui um único slot de checkpoint. `task status` é uma consulta
read-only, sem carregar modelo, criar `AgentApplication` ou adquirir o lock de
execução; ele classifica o slot como `ABSENT`, `RESUMABLE`, `PAUSED`,
`TERMINAL`, `UNSUPPORTED` ou `INVALID`. Use `--json` para um documento bounded
e estável.

`task resume` aceita somente a retomada explícita do checkpoint válido:

```powershell
llm-agent task status --workspace C:\caminho\projeto
llm-agent task resume --workspace C:\caminho\projeto --yes
```

Uma retomada bem-sucedida cria um novo `run_id` para o mesmo `root_task_id` e
preserva progresso, autoridade/referência de Task Definition e policy/budget;
o tempo em que o processo esteve offline não conta como tempo ativo. Pausar ou
interromper preserva um checkpoint não terminal; cancelar explicitamente é
terminal e não pode ser reaberto por `task resume`.

Checkpoint corrompido, incompatível ou hierárquico em estado `running` é
reportado como `INVALID` ou `UNSUPPORTED`, com razão estável, preservado e sem
execução. A limitação hierárquica usa
`HIERARCHICAL_RESUME_UNSUPPORTED`: Wave 10 não faz pseudo-resume de um
microplan em andamento.

Quando houver observabilidade, `llm-agent inspect` pode mostrar o fato de
retomada e a linhagem entre tentativas. Traces são enriquecimento opcional e
não são a autoridade para classificar ou executar a continuidade.

## Estado

A avaliação determinística atual está **GREEN LOCAL** para os contratos e
cenários cobertos. Ela não deve ser interpretada como benchmark de modelo real
nem como gate final de release; a execução com modelo real permanece separada,
explicitamente autorizada e não é iniciada por estes comandos. Os critérios
determinísticos PRE-V1 estão **GREEN LOCAL**; aceitação final com modelo real é
gated/not run e Standalone V1 ainda não foi declarada.

## Documentação e contribuição

O [índice técnico authoritative](docs/README.md) mapeia arquitetura, contratos
CURRENT, ADRs, referências e registros históricos. Consulte também
[CONTRIBUTING.md](CONTRIBUTING.md) para qualidade e contribuição.
