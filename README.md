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

`run` é headless e nunca lê `stdin`. Se uma ação exigir consentimento e `--yes`
não estiver presente, ela termina bloqueada sem executar o efeito. `--yes`
fornece aprovação para aquela execução; não cria capability, grant ou authority
e não transforma validação ausente em sucesso.

`--home DIR` fornece uma raiz portátil para configuração e estado; sem override,
paths são resolvidos nos diretórios de usuário do sistema. O pacote instalado
não é usado como diretório gravável.

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
explicitamente autorizada e não é iniciada por estes comandos.

## Documentação e contribuição

O [índice técnico authoritative](docs/README.md) mapeia arquitetura, contratos
CURRENT, ADRs, referências e registros históricos. Consulte também
[CONTRIBUTING.md](CONTRIBUTING.md) para qualidade e contribuição.
