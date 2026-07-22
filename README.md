# Contexto e Estrutura do Projeto: LLM Agent

> **Atualizado após a implementação do roadmap de código e multitarefa.**
> O projeto preserva o núcleo das fases 0–3 (`ExecutionGateway`,
> `PlanExecutor`, `StepExecutor`, checkpoint v2) e adiciona: gateway de modelo
> independente de provider, perfis de hardware, catálogo canônico de skills,
> domínio de engenharia de código, `ChangeSet` transacional, validação
> cancelável, comandos `/code` sem planner, seleção determinística de contexto,
> política de confiança e `TaskGraph` com templates executáveis.

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

# 2. Instalação recomendada para 8 GB (sem stack de ML)
pip install -e .

# Ambiente de desenvolvimento e CI
pip install -e ".[dev]"

# Opcional: memória semântica/stack de ML
# pip install -e ".[ml]"

# 3. Crie o arquivo de configuração a partir do exemplo
copy config.example.json config.json   # Windows
# cp config.example.json config.json   # Linux/macOS

# 4. Edite model_profiles.local_8gb com o endpoint e o nome do modelo
# (veja docs/modelos-providers.md e docs/perfil-hardware.md)
```

### Execução
```bash
llm-agent
# Compatibilidade: python cli.py
```
O terminal interativo será iniciado. Digite sua pergunta ou objetivo diretamente. Use `/agent <objetivo>` para acionar o modo agente de forma explícita.

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

Propostas de menor confiança exibem o diff e pedem confirmação. `--yes` aprova
essa confirmação explicitamente; não transforma validação ausente em sucesso.
Use `/code help` para a sintaxe completa.

### Executar os testes
```bash
python scripts/check_quality.py
ruff check .
mypy
pytest -q
```

Os gates são estritos para todo o código de produção: complexidade ciclomática
máxima 10, módulos de até 300 linhas, Ruff limpo e mypy sem overrides por
módulo. As listas de exceção em `quality/baseline.json` permanecem vazias,
fontes Python do projeto não podem ser ocultadas por regras do `.gitignore`, e os
arquivos textuais são validados como UTF-8 sem BOM.

Para revisar artefatos antigos sem apagá-los, execute
`python scripts/clean_runtime.py`. O comando faz apenas dry-run; `--apply`
arquiva estado persistente em `runtime/archive/` antes de remover caches
allowlisted.

---

## 1. Visão Geral da Arquitetura

O sistema combina um facade **Orchestrator** com serviços internos de ciclo da
tarefa, segurança, execução hierárquica e composição preguiçosa, além de casos
de uso modulares. A direção das dependências é explícita:

```text
CLI / Orchestrator
        |
        +--> planning: ExecutionGateway / TaskGraph / Scheduler
        +--> code: discovery / intelligence / ChangeSet / validation / workflows
        +--> skills: registry / descriptors / capability policy
        +--> llm: ModelGateway --> provider adapter
        +--> runtime: context / limits / cancellation / artifacts
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
