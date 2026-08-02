# Contexto e Estrutura do Projeto: LLM Agent

> **Atualizado após as fases 0 e 1 da evolução standalone.**
> O projeto preserva o núcleo das fases 0–3 (`ExecutionGateway`,
> `PlanExecutor`, `StepExecutor`, checkpoint v2) e adiciona: gateway de modelo
> independente de provider, perfis de hardware, catálogo canônico de skills,
> domínio de engenharia de código, `ChangeSet` transacional, validação
> cancelável, comandos `/code` sem planner, seleção determinística de contexto,
> política de confiança, `TaskGraph` com templates executáveis e uma aplicação
> instalável com configuração e estado separados por workspace.

Este documento apresenta a arquitetura e o uso do **LLM Agent**. O projeto é um
agente local de desenvolvimento com CLI, planejamento linear/hierárquico,
workflows de análise e alteração de código e execução multitarefa local. Ele não
é um sistema distribuído ou “multiagente”: a multitarefa usa nós isolados de um
grafo sob um único runtime e políticas compartilhadas.

---

## 0. Início Rápido (Como Rodar o Projeto)

### Pré-requisitos
* Python 3.10+ instalado.
* Um servidor LLM acessível por um adapter configurado. O adapter embutido atual
  usa Chat Completions OpenAI-compatible; o domínio não depende desse protocolo.

Para uma GTX 1070 com 8 GB, use o perfil padrão `low_vram_8gb`: uma chamada de
modelo por vez, até duas operações de I/O concorrentes, uma validação de processo
por vez e saídas padrão de 2048 tokens. O perfil core não instala a stack de ML.

### Instalação

```bash
# 1. Clone o repositório e entre na pasta
git clone <url-do-repo>
cd LLM

# 2. Instalação leve recomendada para 8 GB (sem stack de ML)
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install .

# Ambiente de desenvolvimento e CI
python -m pip install -e ".[dev]"

# Opcional: memória semântica/stack de ML
# python -m pip install ".[ml]"

# 3. Crie a configuração versionada no diretório da aplicação
llm-agent config init
llm-agent config path

# 4. Edite no path exibido o endpoint e o nome do modelo em
# model_profiles.local_8gb
# (veja docs/modelos-providers.md e docs/perfil-hardware.md)
```

### Execução

```bash
llm-agent doctor
# Opcional: persistir o diagnóstico no estado da aplicação
llm-agent doctor --write-report
llm-agent chat
# Compatibilidade: python cli.py

# Uma tarefa headless, com workspace explícito
llm-agent run --workspace C:\caminho\do\projeto "Analise este repositório"

# Saída adequada a automação
llm-agent run --workspace C:\caminho\do\projeto --json "Resuma o projeto"

# Autoridade explícita para efeitos que pedirem consentimento nesta execução
llm-agent run --workspace C:\caminho\do\projeto --yes "Aplique a alteração"
```

Sem subcomando, `llm-agent` também abre o chat. O workspace padrão é o
diretório atual, mas automações devem passá-lo explicitamente. `--home DIR`
define uma raiz portátil para configuração, dados, estado, cache e logs; sem
esse override, a aplicação usa os diretórios do usuário apropriados ao sistema
operacional. Nada é gravado no pacote instalado.

O diagnóstico é offline: valida instalação, paths, schema, workspace, perfil e
integridade da memória persistente, mas não constrói o modelo nem testa o
endpoint. Sem `--write-report`, ele também não persiste arquivos. Digite sua
pergunta ou objetivo diretamente no chat e use `/agent <objetivo>` para acionar
o modo agente.

Execuções headless nunca solicitam entrada pelo terminal. Quando um efeito
exige consentimento, o resultado é `blocked`, com `success: false`, e a ação não
é executada. Isso abrange escrita de arquivos e validadores de subprocesso.
`llm-agent run --yes` concede essa autoridade apenas à execução atual;
validações ausentes ainda resultam em `unverified`, nunca em sucesso artificial.

Configurações e estados antigos não são adotados silenciosamente:

```bash
llm-agent config migrate --from C:\caminho\config.json
llm-agent state migrate --workspace C:\caminho\do\projeto --from C:\caminho\runtime
```

As migrações preservam a origem, são idempotentes para conteúdo igual e falham
em conflitos.

Para tarefas de código conhecidas, prefira os comandos explícitos. Eles não
pedem ao modelo que escolha skill nem monte um plano:

```text
/code analyze agent/code/workflows.py
/code review agent/code/workflows.py agent/code/changes.py
/code modify agent/code/workflows.py -- Adicione telemetria sem mudar a API
/code repair agent/code/workflows.py --tests -- Corrija a falha preservando contratos
/code template parallel_analyze agent/code/workflows.py agent/code/changes.py
/code template analyze_then_modify agent/code/workflows.py -- Simplifique o fluxo
```

No chat, propostas de menor confiança exibem o diff e pedem confirmação;
`/code ... --yes` aprova a proposta daquele comando. No modo headless,
`llm-agent run --yes ...` concede a aprovação à execução atual. Nenhuma das
opções transforma validação ausente em sucesso. Use `/code help` para a sintaxe
completa.

### Executar os testes
```bash
python scripts/check_quality.py
ruff check .
mypy --platform linux
mypy --platform win32
pytest -q
# Aceitação completa: usa build PEP 517 isolado e instala dependências
python scripts/verify_installed_package.py
```

Os gates são estritos para todo o código de produção: complexidade ciclomática
máxima 10, módulos de até 300 linhas, Ruff limpo e mypy para Linux e Windows,
sem overrides por módulo. As listas de exceção em `quality/baseline.json` permanecem vazias,
fontes Python do projeto não podem ser ocultadas por regras do `.gitignore`, e os
arquivos textuais são validados como UTF-8 sem BOM.

O último gate constrói um wheel, instala-o com suas dependências em um ambiente
limpo e exercita CLI, análise real e confinamento de paths fora do checkout.
Ele requer acesso a um índice ou wheelhouse. Em desenvolvimento, é possível
reutilizar somente as ferramentas de build já instaladas com
`--no-build-isolation`. O modo `--offline-diagnostic` também reutiliza pacotes
do ambiente e instala o wheel sem dependências; por isso é um diagnóstico mais
fraco e não substitui o gate de aceitação.

Para revisar um `runtime/` legado deste repositório sem apagá-lo, execute
`python scripts/clean_runtime.py`. O comando faz apenas dry-run; `--apply`
arquiva estado persistente antes de remover caches allowlisted.

---

## 1. Visão Geral da Arquitetura

O sistema combina um facade **Orchestrator** com serviços internos de ciclo da
tarefa, segurança, execução hierárquica e composição preguiçosa, além de casos
de uso modulares. A direção das dependências é explícita:

```text
CLI / modo headless
        |
        v
AgentApplication
        |
        +--> Orchestrator / planning / TaskGraph
        +--> code: discovery / intelligence / ChangeSet / validation
        +--> skills: registry / descriptors / capability policy
        +--> llm: ModelGateway --> provider adapter
        +--> runtime: config / workspace / paths / lifecycle / artifacts
```

O fluxo de processamento de um objetivo do usuário segue estas etapas:
1. **Roteamento de Persona (Router):** Analisa a intenção da solicitação para atribuir o papel mais adequado ao agente (`coder`, `researcher`, `general` ou `security_auditor`), o que restringe as ferramentas disponíveis e altera o prompt de sistema. Para casos não triviais, esta decisão usa uma classificação LLM baseada em prompt em vez de apenas palavras-chave fixas.
2. **Criação do Plano (Plan Builder):** Caso a tarefa não seja trivial, o agente solicita ao LLM um plano sequencial contendo a chamada de ferramentas adequadas — ou, para objetivos complexos, decompõe em um `MacroPlan` hierárquico (ver `complexity.py`/`hierarchical_planner.py`).
3. **Validação e execução do plano:** todo plano legado atravessa o
   `ExecutionGateway`. Macrodependências agora são validadas como `TaskGraph` e
   executadas em ordem topológica.
4. **Casos de uso de código:** `code_task` expõe `analyze`, `review`, `generate`,
   `modify`, `repair`, `refactor`, `template` e `multitask`.
   `agent/interfaces/cli/commands.py` usa a
   mesma camada de aplicação em `/code`, sem passar pelo planner. Análise e
   revisão não precisam chamar o modelo.
5. **Contexto e mudança:** arquivos são ranqueados por target, nome, símbolo e
   imports; o contexto inclui SHA-256 e é limitado pelo perfil. O modelo propõe
   um `ChangeSet`, preferencialmente com edições pequenas, `base_hash` e
   `expected_text`.
6. **Política e validação:** risco e confiança são calculados por código. Uma
   proposta de baixa confiança exige aprovação antes do commit. Paths e
   precondições são verificados, o diff é produzido e a validação decide entre
   `succeeded`, `unverified` ou rollback com falha. O modelo não declara que
   testes passaram.
7. **Multitarefa local:** o scheduler executa somente nós prontos, permite
   leituras compatíveis em paralelo e serializa recursos com escrita. A
   concorrência de modelo permanece 1 no perfil de 8 GB.

### Núcleo de execução atual

Após a validação do `ExecutionGateway`, o `PlanExecutor` coordena dependências,
paralelismo, limites, cancelamento e replanejamento. A execução e finalização de
um único passo pertencem ao `StepExecutor`. O `AgentState` mantém `_step_id`,
status (`pending`, `running`, `completed`, `failed`, `skipped`), tentativas e
erros; eventos terminais persistem o checkpoint v2.

Na retomada, passos `running` voltam a `pending` e passos concluídos não são
repetidos. `failed` e `skipped` só são reexecutados quando
`resume_retry_failed` ou `resume_retry_skipped` forem habilitados. Checkpoints
v1 são rejeitados por segurança, pois não contêm estado confiável por passo.

---

## Documentação Detalhada

O restante da documentação técnica está em `docs/`. Veja o [índice completo](docs/README.md):

* [Guia de contribuição e qualidade](CONTRIBUTING.md)
* [Visão da plataforma standalone](docs/plataforma-standalone.md)
* [Operação standalone](docs/operacao-standalone.md)
* [ADR: visão do assistente standalone](docs/adr/0002-visao-do-assistente-standalone.md)
* [ADR: bootstrap e ciclo de vida](docs/adr/0003-bootstrap-paths-e-ciclo-de-vida-standalone.md)
* [ADR: invocation ID no protocolo stdio 1.0](docs/adr/0004-invocation-id-protocolo-stdio-1-0.md)
* [ADR: launcher interno para contenção stdio no Windows](docs/adr/0005-launcher-interno-contencao-stdio-windows.md)
* [Árvore de Diretórios do Projeto](docs/estrutura-diretorios.md)
* [Detalhamento dos Arquivos da Raiz (Root Files)](docs/arquivos-raiz.md)
* [Mapeamento de Ferramentas (Skills) em `agent/skills/`](docs/skills.md)
* [A Suíte de Testes (tests/)](docs/testes.md)
* [Guia de Extensão e Solução de Problemas (Onde Alterar?)](docs/guia-extensao.md)
* [Arquitetura de execução e retomada](docs/arquitetura-execucao.md)
* [Modelos e providers](docs/modelos-providers.md)
* [Agente de código](docs/agente-codigo.md)
* [TaskGraph e multitarefa](docs/multitarefa.md)
* [Perfil de hardware limitado](docs/perfil-hardware.md)
* Módulo `agent/`: [core](docs/agent/core.md) · [llm](docs/agent/llm.md) · [memory](docs/agent/memory.md) · [planning](docs/agent/planning.md) · [reporting](docs/agent/reporting.md) · [security](docs/agent/security.md)
