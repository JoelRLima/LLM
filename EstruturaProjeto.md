# Estrutura e arquitetura do LLM Agent

Este documento descreve o estado atual do repositório. Ele, o README e os guias
permanentes em `docs/` formam a referência operacional; artefatos temporários de
análise e roadmap não fazem parte da documentação versionada.

## 1. Objetivo e escopo

O projeto é um agente local de desenvolvimento com:

- CLI conversacional e modo agente;
- planejamento linear, hierárquico e por grafo de tarefas;
- ferramentas locais com catálogo, capacidades e política de acesso;
- análise, revisão, geração, alteração, reparo e refatoração de código;
- mudanças representadas por `ChangeSet`, com diff, validação e rollback;
- comandos explícitos `/code` que ignoram router/planner em tarefas conhecidas;
- seleção determinística de contexto e confirmação baseada em risco explicável;
- integração com modelos por uma interface independente de provider;
- limites de contexto, concorrência e reparo adequados a hardware local.

“Multitarefa” significa executar nós independentes de um `TaskGraph` no mesmo
runtime. O projeto não é um sistema multiagente distribuído e não inicia vários
modelos locais em paralelo no perfil recomendado de 8 GB.

## 2. Início rápido

Requisitos: Python 3.10+ e um servidor de modelo acessível pelo adapter
configurado. O adapter fornecido implementa Chat Completions
OpenAI-compatible.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
pip install -e ".[dev]"
Copy-Item config.example.json config.json
llm-agent
```

Instalação completa:

```powershell
pip install -e ".[dev,ml]"
```

O extra `ml` contém a camada opcional de memória semântica. O runtime de
código não precisa dela. Os arquivos `requirements-*.txt` permanecem como
fachadas de instalação e `requirements.lock` preserva o ambiente congelado.

Antes de executar, ajuste `model_profiles.local_8gb.base_url` e `model` em
`config.json`. As chaves legadas `api_url`, `model`, `temperature`,
`max_tokens`, `timeout` e `ENABLE_GBNF` continuam válidas durante a migração.

## 3. Arquitetura em camadas

```text
CLI / Commands
      |
      +-- Orchestrator e execução legada
      |      +-- ExecutionGateway -> PlanExecutor -> StepExecutor
      |      +-- HierarchicalPlanner -> TaskGraph -> ordem topológica
      |
      +-- Skills
      |      +-- SkillRegistry + SkillSpec + CapabilityPolicy
      |      +-- code_task
      |
      +-- Domínio de código
      |      +-- application / commands
      |      +-- descoberta e adapters de linguagem
      |      +-- inteligência e índice
      |      +-- seleção de contexto -> workflows
      |      +-- ChangeSet estruturado -> policy -> validação/rollback
      |      +-- classificação de falhas e templates de TaskGraph
      |
      +-- Planejamento multitarefa
      |      +-- TaskGraphValidator -> TaskGraphScheduler
      |
      +-- Runtime
      |      +-- contexto, limites, cancelamento, eventos e artifacts
      |
      +-- ModelGateway -> adapter de provider
```

As dependências apontam para contratos estreitos:

- `agent/code/` não conhece CLI, `Orchestrator` nem protocolo HTTP;
- `agent/planning/` não conhece um provider concreto;
- `agent/skills/` adapta argumentos externos para casos de uso;
- peculiaridades de API ficam em `agent/llm/providers/`;
- limites e serviços transversais ficam em `agent/runtime/`.

## 4. Responsabilidades por pacote

### `agent/llm/`

- `contracts.py`: mensagens, requests, responses, uso, stream, capacidades,
  `ModelGateway` e erros normalizados;
- `providers/factory.py`: resolve perfil novo ou configuração legada;
- `providers/openai_compatible.py`: HTTP, payload, SSE, reasoning, GBNF e
  tokenize específicos do protocolo;
- `structured_output.py`: seleciona JSON Schema, GBNF ou JSON instruído e
  valida o retorno;
- `context_manager.py`: orçamento e compressão do contexto;
- `model_client.py`: fachada do planejador legado. Casos de uso novos usam
  `ModelGateway` diretamente.

Adicionar um provider não deve criar condicionais em workflows ou skills.

### `agent/interfaces/cli/`

Contém o entry point, loop interativo, comandos, handlers, streaming e
apresentação. Essa camada adapta terminal e input humano aos casos de uso; o
domínio não a importa. Os arquivos homônimos da raiz são aliases temporários.

### `agent/runtime/`

- `hardware.py`: perfis imutáveis de hardware;
- `context.py`: `TaskExecutionContext`, limites, cancelamento compartilhado,
  gate de modelo, eventos, métricas, `Artifact` e `TaskResult`.
- `config.py` e `config_validation.py`: carregamento e normalização da
  configuração;
- `paths.py` e `logging.py`: caminhos de artefatos e logging centralizados.

Contextos filhos têm identificação e permissões próprias, mas compartilham o
token de cancelamento e o limite global de chamadas ao modelo.

### `agent/code/`

- `contracts.py`: contratos normalizados de projeto, análise e diagnósticos;
- `discovery.py`: linguagens, manifests, raízes de source/teste e limites;
- `languages/`: adapters sem dependência do workflow;
- `intelligence.py`: análise, índice, busca de símbolos e cache por hash;
- `changes.py`: proposta, preparação, diff, commit, validação e rollback;
- `application.py`: entrada única de CLI e skill para casos de uso de código;
- `commands.py`: parser determinístico e sem efeitos de `/code`;
- `context_selection.py`: ranking por target, diretório, nome, símbolo e import;
- `diagnostics.py`: classificação de falhas antes do reparo;
- `policy.py`: confiança, motivos e necessidade de confirmação;
- `task_templates.py`: grafos determinísticos para operações recorrentes;
- `validation.py`: execução de validadores com timeout e cancelamento;
- `workflows.py`: analyze, review, generate, modify, repair e refactor;
- `multitask.py`: adaptação desses workflows a nós de `TaskGraph`.

Python possui análise AST. Linguagens sem adapter usam análise textual de baixa
confiança e retornam essa limitação explicitamente.

### `agent/planning/`

O caminho histórico de planos continua protegido por:

```text
PlanValidator -> PlanOptimizer -> PlanValidator -> ExecutionGateway
              -> PlanExecutor -> StepExecutor
```

`task_graph.py` define nós, dependências, prioridade, recursos, estados,
checkpoint e validação de ciclos. `task_scheduler.py` executa somente nós
prontos, respeita conflitos de leitura/escrita e agrega resultados em ordem
determinística.

Macroplanos hierárquicos são convertidos e validados como `TaskGraph`. O
executor hierárquico permanece sequencial porque ainda compartilha a sessão e o
`AgentState` legados; os workflows novos usam contextos isolados e podem
concorrer quando seus recursos são compatíveis.

### `agent/skills/`

`catalog.py` é a fonte canônica de construção, capacidades, custo, cache,
timeout e categoria. `SkillRegistry` instancia e valida as implementações.
`policy.py` concede capacidades a personas. `tool_metadata.py` é somente uma
fachada derivada para consumidores legados.

A skill `code_task` expõe:

- `analyze` e `review`, determinísticos e sem modelo;
- `generate`, `modify` e `refactor`, com proposta estruturada;
- `repair`, com tentativas limitadas;
- `template`, com grafo determinístico;
- `multitask`, com grafo validado.

Não existe mais registro por `SKILL_CONFIG` nem lista manual de ferramentas por
persona como fonte de verdade.

### Memória, segurança e reporting

- `agent/memory/`: memória persistente e camada semântica opcional;
- `agent/security/`: padrões e scanner estático;
- `agent/reporting/`: métricas, relatórios, tracking e resumos incrementais;
- `agent/runtime/paths.py`: caminhos de todos os artefatos de runtime.

## 5. Fluxos funcionais

### Análise e revisão

`ProjectDiscovery` cria um perfil do repositório. `LanguageRegistry` escolhe um
adapter e `CodeIntelligenceService` gera símbolos e diagnósticos. Review compara
os bytes antes/depois para garantir que continue somente leitura.

### Geração, modificação e refatoração

```text
CodeRequest explícito ou code_task
        -> ContextSelector (targets / símbolos / imports / hashes)
        -> ModelGateway
        -> saída estruturada validada
        -> ChangeSet proposto
        -> verificação de path/base_hash/expected_text
        -> diff + ChangeApprovalPolicy
        -> commit ou blocked aguardando confirmação
        -> validação do projeto
        -> succeeded | unverified | rollback + failed
```

`unverified` significa que não havia validator disponível; nunca equivale a
“testes passaram”. O modelo propõe conteúdo, mas não recebe acesso direto ao
filesystem.

`edit` é a operação preferida em arquivos existentes: aplica `replace`,
`insert_before`, `insert_after` ou `delete` sobre faixas do conteúdo original.
`expected_text` protege a região e `base_hash` protege o arquivo inteiro. Edits
sobrepostos são rejeitados antes da escrita.

### Comando explícito

`/code` transforma a entrada diretamente em `CodeRequest` e chama
`CodingApplicationService`; ele não usa `Orchestrator`, router ou planner. Isso
reduz chamadas e decisões delegadas ao modelo em hardware/modelos limitados.
Análise e review continuam totalmente determinísticos.

### Reparo

Reparo repete o pipeline de proposta por no máximo o limite do perfil. Antes da
próxima proposta, `FailureClassifier` identifica sintaxe, teste, timeout,
conflito, formato, permissão ou cancelamento. Falhas não recuperáveis não são
enviadas novamente ao modelo. Cada tentativa falha de forma fechada e uma falha
de validação restaura os arquivos antes da próxima proposta; ChangeSets
idênticos não são repetidos.

### Multitarefa

O scheduler:

1. valida IDs, dependências, recursos e ausência de ciclos;
2. seleciona os nós prontos por prioridade;
3. cria contextos filhos;
4. agrupa somente recursos compatíveis;
5. bloqueia dependentes de nós que falharam, salvo política explícita;
6. persiste estados próprios do grafo quando solicitado.

Duas leituras do mesmo arquivo podem concorrer. Qualquer escrita sobre recurso
igual ou ancestral/descendente é serializada.

Para fluxos comuns, `parallel_analyze`, `parallel_review` e
`analyze_then_modify` constroem IDs, dependências, capabilities e recursos sem
planejamento por LLM.

## 6. Perfil de hardware padrão

O default é `low_vram_8gb`, pensado para uma GTX 1070 com 8 GB:

| Limite | Valor |
| :--- | ---: |
| contexto lógico | 8192 tokens |
| saída por chamada | 2048 tokens |
| chamadas de modelo concorrentes | 1 |
| operações de I/O concorrentes | 2 |
| processos de validação concorrentes | 1 |
| chamadas de modelo por tarefa | 20 |
| tentativas de reparo | 2 |
| memória semântica | desabilitada |

O perfil não exige CUDA e não escolhe, baixa ou carrega um modelo. O servidor
local continua responsável pelo modelo, quantização e contexto físico. Aumentar
`max_model_concurrency` pode elevar muito o consumo de VRAM e não é recomendado
para esse hardware.

## 7. Configuração de modelo

Exemplo mínimo do formato atual:

```json
{
  "hardware_profile": "low_vram_8gb",
  "max_model_concurrency": 1,
  "max_io_concurrency": 2,
  "max_process_concurrency": 1,
  "code_policy": {
    "auto_apply_min_confidence": 0.85,
    "max_auto_files": 2,
    "require_target_alignment": true
  },
  "default_model_profile": "local_8gb",
  "model_profiles": {
    "local_8gb": {
      "provider": "openai_compatible",
      "base_url": "http://127.0.0.1:8080/v1",
      "model": "default",
      "temperature": 0.2,
      "max_tokens": 2048,
      "timeout": 300,
      "capabilities": {
        "streaming": true,
        "structured_output": "gbnf",
        "reasoning": true,
        "token_counting": true,
        "tool_calls": false
      }
    }
  }
}
```

Capacidades devem declarar o que o endpoint realmente suporta. Se não houver
GBNF ou JSON Schema nativo, use `json_prompt`; o parser continuará validando o
objeto em runtime.

## 8. Extensão do projeto

| Objetivo | Extensão correta |
| :--- | :--- |
| novo provider | implementar `ModelGateway` em `agent/llm/providers/` e registrar na factory |
| nova linguagem | implementar `LanguageAdapter` e registrar no `LanguageRegistry` |
| novo validator | implementar `ValidationProvider` sem instalar dependências |
| nova skill | implementar `BaseSkill`, criar um `SkillSpec` no catálogo e testar a política |
| novo workflow | compor serviços do domínio e retornar `TaskResult` |
| novo sinal de contexto | estender `ContextSelector` com score e motivo determinísticos |
| nova regra de aprovação | estender `ChangeApprovalPolicy` sem consultar o modelo |
| novo comando de código | adicionar caso de uso à aplicação e manter CLI/skill como bordas |
| nova unidade multitarefa | criar `TaskNode` com dependências, capacidades e recursos explícitos |
| novo campo transversal | adicionar ao contrato/runtime/config, sem dicionários paralelos |

Antes de adicionar uma dependência pesada, avalie se ela pode ser opcional e
isolada em `requirements-ml.txt` ou em outro extra específico.

## 9. Qualidade e verificação

O gate local é:

```powershell
.venv\Scripts\python.exe scripts\check_quality.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe -m pytest -q
git diff --check
```

O primeiro comando aplica limites estritos: complexidade máxima 10, módulos de
produção com até 300 linhas, direção das dependências nas camadas estáveis e
links locais válidos e arquivos de texto em UTF-8 sem BOM.
`quality/baseline.json` não contém exceções. O mypy
descobre todo o pacote `agent` e não usa overrides por módulo, portanto código
novo não fica fora da análise por acidente.

Convenções de responsabilidade, contratos, testes e definição de pronto estão
no [guia de contribuição](CONTRIBUTING.md).

A avaliação de capacidades em `agent/evaluation/` usa cenários herméticos e
oráculos de filesystem, resposta e limites. Ela valida efeitos reais sem
precisar de rede ou modelo. `benchmark.py` é separado e mede o fluxo completo
com o backend configurado.

## 10. Garantias e limitações

Garantias implementadas:

- isolamento de paths dentro da raiz nos fluxos novos;
- base hash opcional contra sobrescrita concorrente;
- `expected_text` e detecção de sobreposição para edits localizados;
- propostas de baixa confiança permanecem sem commit até aprovação;
- execução de comandos com `shell=False`, timeout e limite de saída;
- rollback das mudanças descritas no `ChangeSet` após falha de validação;
- saída estruturada validada antes de virar alteração;
- dependências e conflitos de recurso verificados antes da multitarefa;
- compatibilidade com configuração e executor legados coberta por testes.

Limitações explícitas:

- o único adapter real fornecido é OpenAI-compatible;
- suporte semântico completo existe apenas para Python;
- validators de outros ecossistemas precisam ser adicionados;
- rollback cobre arquivos do `ChangeSet`, não efeitos arbitrários de processos;
- a sandbox de Python é defesa em profundidade, não isolamento de sistema
  operacional para código hostil;
- locks do scheduler são locais ao processo;
- a qualidade da geração ainda depende do modelo escolhido.

## 11. Documentação relacionada

- [README](README.md): instalação e visão geral;
- [guia de contribuição](CONTRIBUTING.md): padrões e gates de qualidade;
- [índice técnico](docs/README.md);
- [providers](docs/modelos-providers.md);
- [perfil de hardware](docs/perfil-hardware.md);
- [domínio de código](docs/agente-codigo.md);
- [multitarefa](docs/multitarefa.md);
- [guia de extensão](docs/guia-extensao.md).
