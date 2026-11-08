> Referencia expandida. A entrada padrao usa a sessao compartilhada, com LLM inicialmente desativada. Os detalhes do chat antigo permanecem acessiveis com `chat --legacy-session`.

# Workbench — LLM Agent

## Começar no Windows

Para abrir um menu único de uso, gerenciamento e validação, execute
`Workbench.cmd`. Ele reúne a abertura do app, as baterias de tarefas, a
validação do código e o gerenciamento da instalação. Para abrir o app direto
sem o menu, use `Workbench.cmd open`.
Executar casos da bateria pode chamar a LLM configurada e gerar custo; validar o
checkout não usa LLM.
Atualizações também podem ser instaladas pelo painel **Atualizações** dentro do
Workbench. Esses caminhos usam arquivos locais; não iniciam downloads nem
chamadas ao modelo para instalar.

Na janela do Workbench, use **Escolher projeto** na barra lateral para selecionar a pasta de trabalho. O app começa na **Visão geral**, em modo local: arquivos, comandos pontuais, análises estáticas e preparação de baterias não precisam de LLM. Ative a LLM para conversar ou executar tarefas com IA; use **Escolher perfil** na Visão geral ou abra **Sessão → Perfil e modelo** para selecionar um perfil já configurado. Se a lista estiver vazia, use **Configurar perfil** para registrar um endpoint compatível; credenciais remotas continuam sendo configuradas separadamente. A conversa começa em `READ_ONLY`. Para solicitar edição, use `/modo editor`; a execução ainda depende das validações e aprovações apresentadas pelo aplicativo. Volte ao modo inicial com `/modo read-only`.

Se o aplicativo não abrir, consulte `.workbench-managed/logs` e o
`maintenance.log` dentro do candidato em `.workbench-managed/versions/`. Se não
houver instalação ativa, abra `Workbench.cmd`, escolha **Gerenciar instalação**
e depois **Instalar / atualizar**; isso só funciona
quando o checkout tem os arquivos e dependências locais necessários. Veja o
[guia de uso básico](../guides/uso-basico.md) para mais detalhes.

O Workbench organiza o trabalho em projetos locais com recursos de arquivos, Git, análise estática, testes, execuções e plugins. O Assistente com LLM é uma capacidade opcional; app e CLI usam um núcleo compartilhado.

## O que está disponível

- leitura e busca confinadas ao workspace;
- alterações suportadas por `code_task` → `ChangeSet` → `ProjectValidator`;
- Shell do modelo limitado a comandos allowlisted; `/exec` executa somente o comando que a pessoa escreveu, sem sandbox;
- memória persistente por workspace; busca semântica é opcional;
- extensions stdio 1.0 condicionadas a catálogo, configuração e authority
  explícita da tarefa;
- web search para a persona apropriada, sujeita a política/aprovação de rede.

`file_writer` ainda existe para consumidores low-level/admin, mas não é
model-actionable. MCP, sandbox universal de sistema operacional e instalação de
pacotes pelo modelo não são fornecidos. Discovery ou aprovação não concedem
authority. A [matriz técnica completa](../referencia-capacidades.md#matriz-current-de-capabilities)
explica cada boundary.

## Usar o Workbench no projeto

Use o Workbench como ambiente visual de projeto: escolha uma pasta e consulte
arquivos e Git, rode análises estáticas, prepare baterias, acompanhe resultados
e gerencie plugins. Essas operações locais não precisam de LLM.

Abra o Assistente quando quiser conversa ou execução com IA. Liberar a LLM não
envia nenhum pedido automaticamente.

A CLI oferece comandos do Agent para operações de projeto e automação. No
Windows, o terminal é a forma de iniciar a CLI; as consultas, o estado da tarefa,
as permissões e os resultados continuam sendo tratados pelos serviços do Agent.
Use `llm-agent --help` ou `llm-agent commands` para descobrir ações. A CLI cobre
operações oferecidas pelo Agent; não é um interpretador geral de comandos do
sistema. `llm-agent local` abre uma sessão útil sem configuração de modelo; ela
começa com a LLM desativada. `/llm on` libera o uso do modelo sem enviar pedido;
depois, reenvie a solicitação que deseja executar. `/llm test` consulta o
catálogo `/models` explicitamente, sem gerar texto; `/llm status` não faz rede.

```powershell
llm-agent doctor
llm-agent local --workspace "C:\projetos\app"
llm-agent task status --workspace "C:\projetos\app"
llm-agent queue create ".\examples\test-queue" ".\reports\minha-fila"
llm-agent queue check ".\reports\minha-fila"
llm-agent queue --help
```

`queue create`, `check`, `status`, `details`, `report`, `pause`, `stop` e `next`
funcionam sem inferência. Para executar/retomar os casos, use
`llm-agent queue run PASTA_DA_FILA --config ARQUIVO --live`; `--live` deixa
explícita a chamada potencialmente faturável à LLM. A fila usa o serviço
compartilhado com o Workbench e mantém verificação externa dos resultados. Veja
o [guia da bateria de testes](../guides/fila-de-testes.md).

## Instalação rápida

O fluxo local requer Python 3.10+; arquivo, Git e preparação de testes não exigem endpoint de LLM. Para habilitar conversa e tarefas agênticas, configure um endpoint OpenAI-compatible:

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

Para instalar o Workbench deste checkout e atualizar pela própria interface,
abra `Workbench.cmd` e escolha **Gerenciar instalação**. A abertura direta usa
`Workbench.cmd open`: ele abre a instalação gerenciada quando ativa e recorre à
instalação F1 preservada quando ainda não existe uma instalação gerenciada.
Veja [atualizações locais do Workbench](../workbench-atualizacoes-locais.md).

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

Para experimentar a execução compacta no runtime compartilhado por CLI e app,
veja [perfil compacto e campanha para modelos menores](../weak-model-runtime.md).
O padrão permanece `standard`; a campanha offline não exige servidor ativo.

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

### App desktop leve

O Workbench usa o WebView2 do Windows e os mesmos serviços de sessão da CLI.
A dependência gráfica é opcional; o frontend usa JavaScript e CSS locais, sem
framework de frontend nem backend persistente próprio.

```powershell
python -m pip install -e ".[dev,desktop]"
llm-agent-workbench --workspace "C:\caminho\projeto"
```

**Comandos**, F2 ou Ctrl+K abre a paleta gerada pelo catálogo compartilhado.
Selecionar um comando prepara o texto; revise os argumentos e envie. O chat
aceita os comandos interativos da CLI, incluindo memória, histórico, instruções,
raciocínio, modo, diagnóstico, `/code`, `/search`, inspeção e troca de
workspace/perfil. A sessão começa em READ_ONLY; `/modo editor` e `/modo full`
continuam sujeitos à autoridade e às aprovações do runtime.

Durante uma tarefa, você pode consultar o estado, cancelar, revisar uma aprovação
ou guardar mensagens pendentes. Elas só executam após um envio explícito com
`/pending send ID`. O painel de aprovação mostra a solicitação e o diff disponível
antes de registrar a decisão. Veja [os detalhes do app](../agent-workbench.md).
Os controles e a revisão de concorrência da interface estão documentados na
[segunda revisão do app](../research/2026/workbench-ui-review-20261029.md).

### Shell interativo

Comece descrevendo seu objetivo em português, por exemplo: “Explique este
projeto”. `/help` apresenta os comandos principais; `/help all` mostra o
catálogo completo com aliases. `/commands <busca>` encontra recursos em
português, e `/commands` oferece um menu curto para o uso cotidiano.
No seletor, digite o número ou nome, ou cancele com Enter (sem opção padrão),
`q` ou Ctrl+C. Na paleta de comandos, a escolha ou um `/comando` digitado
prepara um rascunho: revise e envie no chat para executá-lo. F2 abre a paleta
e recupera o rascunho anterior quando você cancela.

O início da sessão mostra o modelo configurado e seu provedor. `default` é
identificado como alias do servidor, sem afirmar qual modelo está carregado.
`/doctor` verifica a configuração local; `/doctor --online` solicita uma sonda
de rede limitada. A capacidade `READ_WRITE` do filesystem no diagnóstico não
concede permissão ao Agent: o chat inicia em `READ ONLY`, e `/modo` explica
as opções. O seletor orienta a escolher a pasta do projeto e sinaliza pastas
de sistema, como `C:\Windows\System32`.

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
[guia de observabilidade e inspector](../observability-inspector.md).

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

O [Agent Workbench](../agent-workbench.md) oferece uma beta READ_ONLY para Windows
com WebView2, além da implementação Tk experimental, usando a camada interativa
compartilhada e os serviços locais de arquivos, tarefas e inventário. Instale a
opção `desktop` e execute `llm-agent-workbench`; ela não substitui a CLI padrão.

O [índice técnico authoritative](../README.md) mapeia arquitetura, contratos
CURRENT, ADRs, referências e registros históricos. Consulte também
[CONTRIBUTING.md](../../CONTRIBUTING.md) para qualidade e contribuição.

A [campanha de previsibilidade de tarefas](../previsibilidade-tarefas.md)
documenta o fluxo de intenção, contrato, validação e conclusão. Para executar
os oráculos determinísticos de validação, sem chamar um modelo:

```powershell
.venv\Scripts\python.exe scripts\run_predictability_campaign.py
```

Os ajustes de checkpoint, consultas e histórico estão registrados nas
[correções de consistência](../history/2026/milestones/correcoes-consistencia-20261026.md).

O [planejamento orientado a resultados](../planejamento-por-resultados.md)
relaciona requisitos, etapas e evidências observadas, mantendo explícitos os
requisitos sem vínculo ou sem verificação canônica.
Condições opcionais no Contract verificam existência de arquivos e presença de
campos em JSON; condições violadas ou inconclusivas bloqueiam a conclusão e
publicam a observação no recibo.
Ao esgotar o plano, o agente pode propor uma correção dessas condições dentro
do orçamento existente; falhas repetidas sem progresso interrompem o reparo,
inclusive após retomada por checkpoint.

A [rodada de modernizações de 27/10/2026](../history/2026/milestones/modernizacoes-20261027.md)
reforça os limites do contexto, o armazenamento do Discovery, as métricas de
avaliação e o tratamento de respostas/conexões do modelo.

## Mapa do workspace

| Finalidade | Local |
| --- | --- |
| Código ativo do Agent | `src/llm_agent/` |
| Testes e fixtures | `tests/` |
| Ferramentas e gates | `scripts/` e `quality/` |
| Distribuição e instalação | `distribution/` e `installer/` |
| Documentação atual | `docs/README.md` e `docs/` |
| CI principal | `.github/workflows/ci.yml` |
| Validação local (perfis `static`, `tests`, `installed`, `all`) | `CONTRIBUTING.md` |

Históricos e evidências locais têm seu arquivo externo em
`D:\LLM Agent Harness Archive\2026-10-25\relocated-root\`, fora deste checkout.
Consulte [o mapa do arquivo local](../arquivo-local.md) e o registro
`D:\LLM Agent Harness Archive\2026-10-25\RESULTS.json` para os destinos e o
resultado de cada movimentação. Consulte os índices de campanha antes de usar
uma materialização como fonte.

## Referência F1 deste workspace

O candidato congelado para revisão é `51c14ec447ecaf7e380a1477e77162b66f9cb3b600d9b6a59094965ef71a3c42`
(1.744 arquivos). O pacote documental local está em
`D:\LLM Agent Harness Archive\2026-10-25\relocated-root\f1-autonomous-close-20261024\campaign-consolidation\F1-CONSOLIDATION-PREPARATION-20261024-v3.zip`;
consulte primeiro `campaign/reports/F1-CONSOLIDATION-REPORT.md` dentro dele.
Esse pacote é específico deste workspace e pode não existir numa cópia limpa do
repositório. O candidato não está aceito nem promovido. A suíte completa precisa
ser repetida numa extração limpa do ZIP, e a aceitação instalada, revisão visual
e decisão humana continuam pendentes.
