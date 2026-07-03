# Contexto e Estrutura do Projeto: LLM Agent

Este documento apresenta uma visão detalhada sobre a arquitetura, organização e funcionamento do projeto **LLM Agent**. Trata-se de um sistema de agente de execução autônomo e multi-agente que interage por meio de um terminal interativo (CLI), planeja tarefas sequenciais de forma dinâmica, gerencia seu próprio contexto de tokens e executa ferramentas especializadas (skills) no repositório de forma segura.

---

## 0. Início Rápido (Como Rodar o Projeto)

### Pré-requisitos
* Python 3.10+ instalado.
* Um servidor LLM local compatível com a API OpenAI rodando (ex.: [LM Studio](https://lmstudio.ai/), [llama.cpp](https://github.com/ggerganov/llama.cpp) com `--server`, [Ollama](https://ollama.com/) com o endpoint `/v1/chat/completions`).

### Instalação
```bash
# 1. Clone o repositório e entre na pasta
git clone <url-do-repo>
cd LLM

# 2. Instale as dependências
pip install -r requirements.txt

# 3. Crie o arquivo de configuração a partir do exemplo
copy config.example.json config.json   # Windows
# cp config.example.json config.json   # Linux/macOS

# 4. Edite config.json com o endpoint correto do seu servidor LLM
# (veja a seção 3.8 para a referência completa de chaves)
```

### Execução
```bash
python cli.py
```
O terminal interativo será iniciado. Digite sua pergunta ou objetivo diretamente. Use `/agent <objetivo>` para acionar o modo agente de forma explícita.

### Executar os testes
```bash
pytest tests/
```

---

## 1. Visão Geral da Arquitetura

O sistema é construído sobre um padrão **Orquestrador-Executor** (com fallback reativo), projetado para otimizar o uso de modelos de linguagem de grande porte (LLMs) locais ou remotos. A comunicação com o modelo de linguagem é unificada em um fluxo que suporta *thinking budget* (tokens dedicados ao raciocínio lógico) e *streaming* de respostas no terminal.

O fluxo de processamento de um objetivo do usuário segue estas etapas:
1. **Roteamento de Persona (Router):** Analisa a intenção da solicitação para atribuir o papel mais adequado ao agente (`coder`, `researcher` ou `general`), o que restringe as ferramentas disponíveis e altera o prompt de sistema.
2. **Criação do Plano (Plan Builder):** Caso a tarefa não seja trivial, o agente solicita ao LLM um plano sequencial contendo a chamada de ferramentas adequadas.
3. **Execução do Plano (Plan Executor):** O orquestrador executa recursivamente cada passo do plano. Possui mecanismos contra loops (repetição de ferramentas), controle rígido de limites de custo (máximo de passos, chamadas e tokens) e geração inteligente de código por um subcomponente (`AutoCoder`).
4. **Ciclo de Correção e Validação (Test & Correct / Lint):** Modificações em arquivos Python são automaticamente validadas por testes unitários gerados sob demanda e verificadas por analisadores de estilo (linter).
5. **Rollback Seguro (Workspace Manager):** Se o plano falhar ou for interrompido, o sistema restaura o estado original dos arquivos a partir de backups automáticos.

---

## 2. Árvore de Diretórios do Projeto

Abaixo está a representação estrutural das pastas e arquivos sob controle de versão (desconsiderando arquivos no `.gitignore`):

```text
.
├── agent
│   ├── security_patterns.py
│   ├── security_scanner.py
│   ├── grammars.py             
│   ├── plan_optimizer.py       
│   ├── plan_validator.py       
│   ├── tool_metadata.py        
│   ├── cancellation.py         
│   ├── complexity.py           
│   ├── hierarchical_executor.py
│   ├── hierarchical_planner.py
│   ├── incremental_summarizer.py
│   ├── task_report.py
│   ├── task_tracker.py
│   ├── health_check.py
│   ├── semantic_memory.py
│   ├── __init__.py
│   ├── auto_coder.py
│   ├── context_manager.py
│   ├── cost_guard.py
│   ├── error_handler.py
│   ├── final_response.py
│   ├── memory.py
│   ├── model_client.py
│   ├── orchestrator.py
│   ├── parsers.py
│   ├── plan_builder.py
│   ├── plan_executor.py
│   ├── prompts.py
│   ├── reactive_loop.py
│   ├── replan.py
│   ├── router.py
│   ├── skills
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── calculator.py
│   │   ├── code_analyzer.py
│   │   ├── directory_reader.py
│   │   ├── echo.py
│   │   ├── file_reader.py
│   │   ├── file_writer.py
│   │   ├── git.py
│   │   ├── grep.py
│   │   ├── python_executor.py
│   │   ├── session_memory.py
│   │   ├── shell.py
│   │   ├── summarize.py
│   │   └── web_search.py
│   ├── state.py
│   ├── tool_executor.py
│   ├── watchdog.py
│   └── workspace.py
├── cli.py
├── commands.py
├── config.example.json
├── config.py
├── logger.py
├── pyproject.toml
├── refactor_orchestrator.py
├── requirements.txt
├── session.py
├── benchmark.py
├── benchmark_results.json
├── health_report.json
├── task_tracker.json            ← NOVO (artefato de tracking)
├── task_tracker.md              ← NOVO (artefato de tracking)
├── reports/                     ← NOVO (relatórios de tarefa)
├── tests
│   ├── test_grammar.py          ← NOVO
    ├── __init__.py
    ├── test_config.py
    ├── test_hello.py
    ├── test_orchestrator.py
    ├── test_parsers.py
    ├── test_session.py
    └── test_temp.py
```

---

## 3. Detalhamento dos Arquivos da Raiz (Root Files)

### 3.1. [cli.py](cli.py)
Gerencia o ponto de entrada da interface de linha de comando.
* **Inicialização:** Carrega as configurações do `config.json`, ativa a sessão de chat (`ChatSession`), carrega todas as ferramentas (`skills`), instancia o `Orchestrator` e restaura a memória persistente (`agent_memory.json`).
* **Loop Principal:** Lê comandos e inputs do usuário, imprimindo o estado do pensamento (*thinking*) e diagnósticos no prompt.
* **Streaming:** Consome a resposta do LLM linha por linha, separando o texto de raciocínio (*thinking chunk*) e o texto de resposta real em cores e painéis formatados com a biblioteca `rich`.

### 3.2. [commands.py](commands.py)
Responsável por interpretar comandos iniciados por barra `/` na CLI. Oferece controle e depuração em tempo real:
* `/system` ou `/sistema`: Altera o prompt de sistema em tempo real.
* `/prompt`: Exibe o Prompt de Sistema ativo na sessão.
* `/think` ou `/pensar`: Alterna o uso de raciocínio lógico profundo (*thinking budget*) definindo um teto de tokens.
* `/clear` ou `/limpar`: Limpa o histórico de diálogo da sessão.
* `/save` e `/load`: Exporta e importa o histórico de conversas em formato JSON.
* `/agent`: Ativa/desativa o comportamento do agente ou executa um objetivo isolado.
* `/debug`: Alterna o nível de diagnóstico (Normal, Verbose ou Desligado).
* `/memory` ou `/memoria`: Exibe uma tabela com o estado da memória atual do agente.
* `/events`: Mostra a telemetria passo a passo da última execução do agente.
* `/remember`, `/forget`, `/clearmemory`, `/save_memory`, `/load_memory`: Gerenciam a persistência e limpeza da memória do agente.
* `/doctor` ou `/diagnostico`: Executa o diagnóstico de saúde do agente (config, memória, skills, permissões).
* `/ls` ou `/list`: Lista os arquivos do projeto (atalho, sem LLM).
* `/read <arquivo>`: Lê o arquivo diretamente (atalho).
* `/find <texto>`: Busca por texto nos arquivos (atalho).
* `/search <consulta>`: Pesquisa na web (atalho).
* `/retry` ou `/retomar`: Retoma a tarefa interrompida a partir do checkpoint salvo.

### 3.3. [session.py](session.py)
Encapsula o gerenciamento de sessões do chat e comunicação direta com a API do LLM (servidor compatível com OpenAI):
* **Payloads:** Monta dinamicamente a estrutura de requisições, injetando instruções de raciocínio no prompt de sistema (`[THINKING]`) e adicionando parâmetros de controle de templates como `enable_thinking`.
* **Streaming e Streaming Parser (`process_stream`):** Analisa o protocolo de stream SSE (Server-Sent Events) retornado do endpoint `/v1/chat/completions`, extraindo e enviando trechos de texto em tempo real para os callbacks de pensamento (`reasoning_content`) e de resposta final (`content`).
* **Função Auxiliar (`extrair_json`):** (Removida durante a refatoração — a lógica de extração de JSON está centralizada em `agent/parsers.py`).
* **Suporte a GBNF:** O método `build_payload` aceita um parâmetro opcional `grammar` que, quando fornecido, inclui o campo `"grammar"` no payload da requisição, forçando o LLM a gerar saída no formato especificado.

### 3.4. [config.py](config.py)
Carrega o arquivo `config.json` e realiza validações minuciosas de segurança e tipos de dados:
* **Fallbacks:** Se uma chave não for encontrada ou tiver o tipo errado (ex.: `temperature` com string ou fora do intervalo [0.0, 2.0]), ele emite um aviso no logger e adota os valores padrões descritos no dicionário `DEFAULT_CONFIG`.
* **Padrões de Prompt:** Define o comportamento padrão do assistente para pensar em inglês e responder em português brasileiro.
* **Nova chave `validation`**: Valida a configuração de validação automática pós-modificação, com subcampos `enabled`, `ruff`, `mypy`, `pytest`, `pytest_dir` e `fail_triggers_replan`, todos com fallbacks seguros.
* **Nova chave `ENABLE_GBNF`**: Ativa ou desativa globalmente o uso de gramáticas GBNF. Padrão: `true`.

### 3.5. [logger.py](logger.py)
Configura a infraestrutura de logging do sistema.
* Define um handler de arquivos (`agent.log`) com nível de logs em `DEBUG`.
* Configura um handler para a saída padrão (`sys.stdout`) cujo nível varia dinamicamente de acordo com o modo de depuração ativado pelo usuário na CLI (`DEBUG` ou `WARNING`).

### 3.6. [gerar.py](gerar.py)
Script utilitário utilizado para atualizar a árvore estrutural do projeto contida no arquivo `estrutura.txt`. Ele lê recursivamente os arquivos do diretório raiz e subdiretórios, pulando deliberadamente extensões compiladas `.pyc` e arquivos/pastas bloqueadas (como `.git`, `.venv`, cache de testes e arquivos de depuração do agente).

### 3.7. [refactor_orchestrator.py](refactor_orchestrator.py)
Script histórico que automatizou a modularização de `agent/orchestrator.py`. Ele lê o código fonte original e usa substituição de strings e expressões regulares para extrair responsabilidades e delegá-las para os componentes recém-criados como `workspace.py`, `context_manager.py`, etc., além de remover as declarações de métodos antigos.

### 3.8. [config.example.json](config.example.json)
Arquivo de template da configuração. Copie-o para `config.json` e ajuste os valores. Referência completa de todas as chaves suportadas:

| Chave | Tipo | Padrão (fallback) | Descrição |
| :--- | :--- | :--- | :--- |
| `api_url` | `string` | `http://127.0.0.1:8080/v1/chat/completions` | Endpoint completo do servidor LLM compatível com OpenAI. |
| `model` | `string` | `"default"` | Nome do modelo a ser passado no campo `model` da requisição. |
| `temperature` | `float` [0.0–2.0] | `0.7` | Criatividade/aleatoriedade das respostas do modelo. |
| `max_tokens` | `int` > 0 | `4096` | Número máximo de tokens na resposta do modelo por chamada. |
| `timeout` | `int` > 0 | `120` | Timeout em segundos para cada requisição HTTP à API. |
| `max_task_steps` | `int` > 0 | `20` | Número máximo de passos que o agente pode executar em um único objetivo. |
| `max_task_tokens` | `int` > 0 | `25000` | Orçamento total de tokens consumidos durante a execução de um objetivo. |
| `max_task_tool_calls` | `int` > 0 | `40` | Número máximo de chamadas de ferramentas em um único objetivo. |
| `default_system_prompt` | `string` | Prompt padrão (PT-BR) | Prompt de sistema usado na sessão de chat direta (fora do modo agente). |
| `max_task_wall_seconds` | `int` > 0 | `300` | Tempo máximo de parede (em segundos) para uma tarefa antes do Watchdog abortar. |
| `max_repeated_no_progress` | `int` > 0 | `3` | Número de repetições idênticas de uma ferramenta antes do Watchdog detectar loop. |
| `max_consecutive_same_error` | `int` > 0 | `3` | Número de falhas consecutivas com o mesmo erro antes do Watchdog abortar. |
| `validation` | `object` | `{...}` | Configuração de validação automática pós-modificação. Ver subcampos abaixo. |
| `validation.enabled` | `bool` | `true` | Ativa/desativa a validação automática. |
| `validation.ruff` | `bool` | `false` | Executa `ruff check` após cada `file_writer` em arquivos `.py`. |
| `validation.mypy` | `bool` | `false` | Executa `mypy` após cada `file_writer` em arquivos `.py`. |
| `validation.pytest` | `bool` | `false` | Executa `pytest` após cada `file_writer` em arquivos `.py`. |
| `validation.pytest_dir` | `string` | `"tests/"` | Diretório onde o `pytest` buscará os testes. |
| `validation.fail_triggers_replan` | `bool` | `false` | Se `true`, uma falha de validação aciona o replanejamento automático. |
| `checkpoint_file` | `string` | `"agent_checkpoint.json"` | Caminho do arquivo de checkpoint para retomada de tarefas. |
| `task_report` | `object` | `{...}` | Configuração do relatório de auditoria da tarefa. Ver subcampos abaixo. |
| `task_report.enabled` | `bool` | `true` | Ativa/desativa a geração do relatório da tarefa. |
| `task_report.format` | `string` | `"json"` | Formato do relatório (`"json"` ou `"markdown"`). |
| `task_report.output_dir` | `string` | `"reports/"` | Diretório onde os relatórios serão salvos. |
| `ENABLE_GBNF` | `bool` | `true` | Ativa/desativa o uso de gramáticas GBNF nas requisições ao LLM. |

### 3.9. [pyproject.toml](pyproject.toml) e [requirements.txt](requirements.txt)
Configurações de ambiente. O arquivo `pyproject.toml` especifica as regras de lint do `ruff` (limite de 120 caracteres por linha, regras de import) e do verificador estático `mypy`. O arquivo `requirements.txt` lista pacotes necessários, incluindo `requests` para requisições HTTP, `pytest` para testes unitários, `rich` para formatação visual e `ddgs` para buscas web.

### 3.10. [benchmark.py](benchmark.py) 🆕
Script de benchmark headless para medir o desempenho do agente. Executa 4 tarefas fixas (listar arquivos, criar e executar hello.py, somar 1..10, resumir EstruturaProjeto.md) e coleta métricas de sucesso, passos e tempo. Resultados salvos em `benchmark_results.json`.

---

## 4. O Módulo `agent/` (Núcleo do Agente Inteligente)

### 4.1. [orchestrator.py](agent/orchestrator.py)
O coração da execução autônoma. Após a refatoração de modularidade, o `Orchestrator` atua como um coordenador central que instancia e conecta os subcomponentes especializados:
* **Subcomponentes:** `ContextManager` (contexto e prompts), `PlanBuilder` (geração do plano), `PlanExecutor` (execução dos passos), `ReactiveLoop` (fallback reativo), `AutoCoder` (geração de código e testes), `ToolExecutor` (execução de ferramentas), `WorkspaceManager` (backup, rollback, diff, lint) e `FinalResponder` (resposta final).
* **Inicialização:** Registra ferramentas na inicialização e expõe endpoints utilitários que conectam as necessidades dos subcomponentes.
* **Mecanismo de Execução (`run`):**
  1. Limpa o estado temporário e registra o objetivo.
  2. Identifica se a pergunta é uma saudação ou dúvida trivial para responder diretamente.
  3. Consulta o roteador de persona para carregar o contexto restrito.
  4. Solicita a criação do plano estruturado ao `PlanBuilder`.
  5. Se o plano for gerado com sucesso, repassa ao `PlanExecutor`; caso contrário, adota o fallback de decisões interativas de passo a passo (`ReactiveLoop`).
  6. Emite eventos telemétricos de controle a cada início/fim de execução de ferramenta.
  7. Se houver falha crítica, executa o rollback das mudanças via `WorkspaceManager`.
* **Streaming:** O método `run()` aceita um parâmetro opcional `stream_callback` que, se fornecido, é repassado ao `FinalResponder` para exibir a resposta final em tempo real.
* **Checkpointing:** O `Orchestrator` persiste o estado da tarefa a cada passo concluído (`_save_checkpoint`) e pode retomar uma tarefa interrompida se nenhum novo objetivo for fornecido (`_load_checkpoint`). Ao final da tarefa (sucesso ou falha), o checkpoint é removido (`_delete_checkpoint`). O arquivo de checkpoint é configurável via `checkpoint_file` em `config.json`.
* **Cancelamento cooperativo:** O método `run()` captura `KeyboardInterrupt` (Ctrl+C) e interrompe a execução de forma limpa, salvando o checkpoint e retornando uma mensagem amigável. O comando `/retry` permite retomar a tarefa posteriormente a partir do checkpoint.
* **Planejamento hierárquico:** Para objetivos complexos (detectados por `complexity.py`), o `Orchestrator` delega a execução ao `HierarchicalPlanner` (geração de macroplano) e `HierarchicalExecutor` (execução de sub‑objetivos), com tracking via `TaskTracker` e sumarização incremental via `IncrementalSummarizer`. Se o macroplano não puder ser gerado, o fluxo linear é usado como fallback.
* **Relatório da tarefa:** Ao final de cada execução, o `Orchestrator` gera um relatório estruturado de auditoria (`TaskReportBuilder`) com passos, métricas, erros e uma prévia da resposta final. Configurável via `task_report` em `config.json`.

### 4.2. [state.py](agent/state.py)
Define a estrutura de dados `AgentState` que encapsula o estado de execução global:
* `objective`: O objetivo em processamento.
* `plan` / `plan_step`: O plano ativo e o índice do passo sendo executado.
* `last_tool` / `last_args` / `last_result`: Detalhes da última ação executada pelo agente.
* `tool_history`: Histórico de chamadas a ferramentas da execução atual.
* `memory`: Instância de `AgentMemory` contendo a memória de longo prazo da sessão.
* `events`: Fila de telemetria de passos.
* `conversation_history`: Histórico de turnos anteriores de conversa.
* **`record_tool_result(tool_name, args, result)`:** (Adicionado na refatoração) Centraliza a mutação de estado após cada execução de ferramenta, atualizando `last_tool`, `last_args`, `last_result` e `tool_history` de forma atômica.

### 4.3. [memory.py](agent/memory.py)
Implementa a classe `AgentMemory` para gerenciar informações persistentes e indexações de arquivos:
* **Estado de Memória:** Estruturado em seções como `project_map`, `key_findings` (lembretes manuais), `analyzed_files` (visão superficial dos arquivos lidos), `file_summaries` (resumos detalhados gerados por IA) e `file_hashes` (para validação de integridade de arquivos).
* **Backup de Memória:** Mantém um histórico das últimas cópias na pasta `memory_backups/` toda vez que salva o estado em `agent_memory.json`.
* **Injeção Dinâmica de Memória (`get_context_for_prompt`):** Evita inundar o prompt do modelo. Filtra os resumos com base nos arquivos explicitamente mencionados no objetivo do usuário e respeita um limite estrito de tokens.

### 4.4. [parsers.py](agent/parsers.py)
Contém utilitários cruciais para processamento de saídas e garantia de contratos estritos:
* `extract_json`: Localiza o primeiro par de chaves `{}` e realiza o parseamento ignorando blocos de códigos markdown.
* `extract_json_from_end`: Varre o texto a partir do fim para encontrar o último objeto JSON fechado (útil caso o modelo escreva texto após o JSON).
* `validate_decision`: Valida se o JSON da decisão do agente possui estrutura obrigatória (ação `tool` ou `final`).
* `normalize_tool_result`: Garante que as ferramentas sigam a assinatura de retorno (chaves `ok`, `done`, `data`, `error`, `message`). Caso a ferramenta retorne uma string contendo padrões conhecidos de falha (ex.: "not found", "exception"), normaliza automaticamente a chave `ok` para `False`.
* `validate_tool_args`: Valida as chaves e tipos de argumentos enviados para uma ferramenta contra o schema JSON gerado pela classe da skill. Lida com tipos primitivos, enums, limites numéricos de mínimo/máximo e validações semânticas (ex.: linha inicial menor que a linha final).

### 4.5. [prompts.py](agent/prompts.py)
Armazena a constante de prompt de sistema global do agente (`AGENT_SYSTEM_PROMPT`) que instrui o LLM sobre:
* A obrigatoriedade de planejar passos de forma estruturada.
* O formato estrito de saída em JSON.
* A necessidade de consultar informações e ler arquivos usando ferramentas adequadas em vez de deduzir seus conteúdos.
* Regras para o uso de memória de sessão.
* **Personas centralizadas**: Todas as personas (`CODER_PROMPT`, `RESEARCHER_PROMPT`, `GENERAL_PROMPT`, `SECURITY_AUDITOR_PROMPT`) são definidas como constantes neste módulo, permitindo manutenção centralizada.

### 4.6. [context_manager.py](agent/context_manager.py)
Administra a janela de contexto de tokens e otimiza o tráfego de dados para a API. Após a refatoração (Fix 5), a comunicação HTTP foi extraída para `ModelClient`, permitindo que o `ContextManager` foque exclusivamente na preparação do contexto:
* **Contexto do Projeto:** Constrói um sumário dos arquivos presentes no repositório listando arquivos rastreados via `git ls-files` ou scaneando o diretório raiz.
* **Compressão de Diálogo (`maybe_compress_context`):** Monitora a janela de tokens. Se o histórico estimado de conversas ultrapassar o limiar de compressão (80% do limite de 8192 tokens), o sistema gera um resumo condensado da conversa via chamada de modelo externa e limpa as mensagens intermediárias, mantendo o resumo no topo.
* **Compactação de Leituras (`build_compact_view`):** Quando o histórico atinge limites elevados, localiza leituras de arquivos passadas e as substitui por seus resumos técnicos extraídos da memória, poupando espaço útil no prompt.
* **Mapeamento de Linhas (`get_file_hints`):** Busca menções a arquivos no objetivo do usuário para expor o total de linhas de cada arquivo, ajudando o modelo a decidir a paginação de leitura.
* **Comunicação com o Modelo (`ask_model`):** Prepara o contexto completo (system prompt, histórico, memória) e delega a requisição HTTP ao `ModelClient`.
* **Seleção automática de gramática:** O método `ask_model` aceita um parâmetro `grammar` que, por padrão (`AUTO_GRAMMAR`), seleciona automaticamente a gramática GBNF apropriada com base no `step_type`. Pode ser sobrescrito com uma string explícita ou desabilitado com `None`.

### 4.7. [plan_builder.py](agent/plan_builder.py)
Interage com o modelo de linguagem especificamente para estruturar um plano de ações:
* **Construção do Prompt:** Junta as informações de objetivo, arquivos e descrições curtas das ferramentas.
* **Regras de Planejamento:** Exige que cada etapa tenha exatamente uma ferramenta. Instrui o modelo a usar `file_writer` para apagar arquivos comuns (com `content: ""`), mas proíbe esvaziar `analysis_notes.md`. Também proíbe o uso de `shell` para operações de arquivo.
* **Validação Inicial:** Valida e remove do plano passos cujos argumentos não correspondam às especificações exigidas pelas ferramentas.

### 4.8. [plan_executor.py](agent/plan_executor.py)
Executa a sequência de passos definidos pelo `PlanBuilder`:
* **Ponto de Restauração:** Antes de executar a lista de passos, solicita ao `WorkspaceManager` o backup preventivo de arquivos sob iminência de modificação.
* **Mecanismos de Segurança:**
  * **Verificação de Custo:** Delega ao `CostGuard` a verificação de limites de passos, tokens e chamadas de ferramentas.
  * **Hard Block:** Impede que ferramentas de análise/leitura sejam chamadas repetidamente com os mesmos parâmetros exatos no mesmo arquivo, mitigando loops redundantes.
  * **Preenchimento de Escrita:** Detecta se um passo de escrita de arquivo está sem o campo `content` (usando `is None` em vez de falsy, para permitir `content: ""` intencional) e solicita ao `AutoCoder` a geração inteligente do código de conteúdo.
  * **Diferencial (Diff):** Antes de persistir qualquer escrita, invoca a impressão do diff no console para transparência visual.
* **Cache Inteligente:** Se um arquivo a ser lido/analisado tiver o mesmo hash SHA256 do arquivo em cache na memória, o executor recupera o resumo do arquivo da memória instantaneamente, pulando a leitura direta.
* **Ciclo Pós-Execução:** Invoca verificação de testes automatizados e linters para validar modificações.
* **Dependência Explícita entre Passos:** Antes de executar cada passo, o executor analisa o plano e detecta dependências implícitas baseadas em arquivos (ex.: um `file_reader` que lê um arquivo gerado por um `file_writer` anterior). Se a dependência falhou, o passo atual é pulado automaticamente para evitar erros em cascata, com registro no histórico de ferramentas.
* **Integração com Replanejamento:** Em caso de falha de um passo, o executor consulta o `ErrorHandler`; se a ação for `"replan"`, aciona o `Replanner` e os novos passos são injetados no plano (substituindo o passo que falhou), dando continuidade à execução.

### 4.9. [reactive_loop.py](agent/reactive_loop.py)
Implementa o fluxo reativo antigo que atua como barreira de segurança secundária. Se o gerador de plano falhar, o loop reativo assume a liderança e decide passo a passo qual ferramenta chamar e com quais parâmetros, baseando-se no histórico recente de execuções. Também utiliza `CostGuard` para verificar limites de custo.

### 4.10. [auto_coder.py](agent/auto_coder.py)
Componente autônomo de auxílio na programação:
* **Geração de Testes Unitários (`generate_tests`):** Utiliza o LLM para escrever testes Python focados nos principais caminhos de execução do arquivo recém-criado/editado.
* **Ciclo de Correção Automatizado (`test_and_correct`):**
  1. Cria um arquivo temporário contendo o código gerado concatenado aos testes unitários propostos.
  2. Executa a suíte de testes em um subprocesso.
  3. Se ocorrerem erros (falha de asserts, sintaxe, exceções), submete o código, testes e a pilha de erros ao LLM para correção.
  4. Realiza esse ciclo por até 3 tentativas. Se os testes passarem, grava a alteração; se falhar, sinaliza falha da tarefa, disparando o rollback do estado original dos arquivos.
* **Geração de Conteúdo (`generate_content`):** Gera textos estruturados e arquivos limpos sem resquícios de tags markdown ou explicações conversacionais do LLM.

### 4.11. [tool_executor.py](agent/tool_executor.py)
Responsável por disparar a execução de cada skill cadastrada:
* Valida a persona ativa para impedir que um agente (ex.: `researcher`) utilize ferramentas não atribuídas à sua função.
* Bloqueia de forma proativa ações que esvaziem arquivos fundamentais como `analysis_notes.md`.
* **Pós-Processamento de Leituras (`maybe_summarize_and_store`):** Toda vez que um arquivo é lido ou analisado pela primeira vez, utiliza a ferramenta `summarize` para extrair um resumo compacto, que é armazenado na memória com o respectivo hash do arquivo para usos futuros de cache.

### 4.12. [workspace.py](agent/workspace.py)
Controla o ecossistema local do espaço de trabalho:
* **Pontos de Restauração (`create_restore_point`):** Copia os arquivos originais que serão alterados para a pasta técnica `memory_backups/restore/<timestamp>`.
* **Rollback:** Se acionado, copia de volta os arquivos preservados e limpa a pasta de restore, devolvendo o projeto ao seu estado inicial limpo.
* **Diff Visível (`show_diff`):** Utiliza o módulo padrão `difflib` para exibir uma saída comparativa clara em formato unificado no console.
* **Lint Check (`lint_check`):** Roda compilação sintática nativa Python (`py_compile`) e, conforme configuração em `config.json` (`validation`), executa opcionalmente `ruff`, `mypy` e `pytest`. Se `fail_triggers_replan` for `true`, lança `ValidationFailedError` que aciona o replanejamento automático.

### 4.13. [final_response.py](agent/final_response.py)
Compila a resposta definitiva do agente:
* **Geração da Resposta:** Reúne o histórico de uso de ferramentas e as anotações geradas em `analysis_notes.md` para submeter um prompt final ao LLM sem o uso de ferramentas adicionais.
* **Auditoria de Menções:** Examina a resposta em linguagem natural por meio de expressões regulares à procura de menções a caminhos de arquivos. Caso o texto mencione arquivos que o agente não leu de fato através de suas ferramentas, ele anexa um aviso no final da resposta alertando que sugestões sobre aqueles arquivos específicos podem ser imprecisas.
* **Streaming na resposta final:** Se um callback `on_chunk` for fornecido pelo `Orchestrator`, a resposta final é gerada em streaming (token por token) em vez de esperar a geração completa. A CLI exibe o texto progressivamente no terminal.

### 4.14. [router.py](agent/router.py)
Executa a triagem inteligente de prompts e ferramentas:
* Identifica se uma solicitação de usuário é meramente trivial (saudações como "olá" ou "quem é você") para atribuir a persona `general` e evitar consumo de plano.
* Utiliza busca de palavras-chave para detectar listagens estritas (`general`), tarefas de código (`coder`) ou pesquisas web (`researcher`).
* Se houver ambiguidade, submete o objetivo ao LLM sob o prompt `ROUTER_PROMPT` para obter a persona final em formato JSON.
* Cada persona ativa um subset de ferramentas e injeta regras de comportamento específicas no prompt inicial.
* **Nova persona `security_auditor`**: Detectada por palavras-chave (segurança, vulnerabilidade, auditoria, etc.) e também disponível via LLM Router. Utiliza ferramentas de leitura/análise sem `file_writer`.

### 4.15. [error_handler.py](agent/error_handler.py)
Centraliza o tratamento, sanitização e logging de erros em todo o agente:
* **`sanitize_error(error_message)`:** Recebe um stack trace ou mensagem de erro bruta e extrai apenas o tipo de erro, a mensagem essencial e a linha relevante — economizando tokens ao enviar contexto de erro ao LLM. Se o traceback for longo (>10 linhas), mantém apenas o início e o fim.
* **`handle_step_failure(step_index, reason, tool, args, emit_callback)`:** Trata falhas na execução de um passo específico: sanitiza o erro, emite um evento telemétrico via `emit_callback` e registra no logger. Retorna a string `"continue"` para indicar ao executor que deve seguir para o próximo passo.
* **`purge_stale_context(session)`:** Limpa o histórico de mensagens da sessão em situações de erro grave, mantendo apenas o system prompt original, mensagens de sistema adicionais (como resumos de compressão) e a última mensagem do usuário — evitando acúmulo de contexto corrompido.

### 4.16. [cost_guard.py](agent/cost_guard.py) 🆕
Centraliza a política de limites de custo de execução do agente. Anteriormente, a verificação de custo (`max_steps`, `max_tokens`, `max_tool_calls`) e a montagem da mensagem de interrupção estavam duplicadas em `PlanExecutor` e `ReactiveLoop`, com valores de fallback divergentes. Este módulo é a única fonte de verdade para essas regras:
* **Constantes padrão:** Define `DEFAULT_MAX_TASK_STEPS = 20`, `DEFAULT_MAX_TASK_TOKENS = 25000` e `DEFAULT_MAX_TASK_TOOL_CALLS = 40`.
* **`check_limits(plan_step, tool_history, estimated_tokens, config) -> bool`:** Retorna `True` se algum limite de custo foi ultrapassado.
* **`build_limit_reached_event(...)`:** Monta o payload do evento de telemetria `cost_limit`.
* **`build_limit_summary(objective, tool_history, last_result) -> str`:** Monta a mensagem padronizada de "tarefa interrompida" exibida ao usuário.

### 4.17. [model_client.py](agent/model_client.py) 🆕
Cliente HTTP para comunicação com o modelo LLM. Extraído do `ContextManager` durante a refatoração de modularidade (Fix 5), isola toda a lógica de comunicação com a API:
* **`request(session, payload, step_type, log_metric_callback, verbose) -> dict`:** Envia uma requisição ao modelo, processa a resposta (incluindo retry com mais tokens em caso de truncamento), coleta métricas (timestamp, step_type, tool, budget, tokens, duração, sucesso) e retorna a decisão parseada.
* **Fallback de tokens:** Utiliza `FALLBACK_AGENT_MAX_TOKENS = 4096` para o retry.
* **Separação de responsabilidades:** O `ContextManager` não depende mais de `requests`, `time` ou `extract_json` para a comunicação com o modelo, facilitando a troca do backend de comunicação no futuro.
* **Suporte a GBNF:** O método `request` aceita um parâmetro opcional `grammar`. Se fornecido e o backend suportar, o campo `"grammar"` é incluído no payload. Um fallback automático detecta backends incompatíveis (erro 400 com "grammar") e desabilita a funcionalidade para a sessão, com cache (`_backend_supports_grammar`) para evitar novas tentativas.

### 4.18. [watchdog.py](agent/watchdog.py) 🆕
Monitora a execução de uma tarefa e decide quando abortar por segurança ou falta de progresso, sem nenhuma chamada adicional ao LLM. Atua como uma camada de proteção independente do `CostGuard` e dos hard blocks do `PlanExecutor`:
* **Timeout global da tarefa:** soma do tempo de parede de todos os passos (complementa o timeout individual do `python_executor` e `shell`). Configurável via `max_task_wall_seconds` (padrão: 300s).
* **Detecção de loop sem progresso:** mesma ferramenta chamada repetidamente com os mesmos argumentos e resultado idêntico, sinal de que o agente está "girando" sem avançar. Configurável via `max_repeated_no_progress` (padrão: 3).
* **Falhas consecutivas com o mesmo erro:** mesmo que os argumentos variem entre tentativas, se o erro for idêntico por N vezes seguidas, o agente é interrompido. Configurável via `max_consecutive_same_error` (padrão: 3).
* **Ponto de entrada único:** `Watchdog.check_all(start_time, tool_history, config)` — executado a cada passo pelo `PlanExecutor` e `ReactiveLoop`, do mesmo modo que `CostGuard.check_limits(...)`.
* **Telemetria:** `build_watchdog_event` e `build_watchdog_summary` padronizam a emissão de eventos e a mensagem ao usuário.

### 4.19. [replan.py](agent/replan.py) 🆕
Implementa o replanejamento automático quando uma ferramenta falha repetidamente (Fase 4C, item 5). Segue o fluxo: **classificar erro → heurística determinística → LLM (último recurso) → aborto**:
* **`ErrorCategory` (Enum):** classifica erros em `FILE_NOT_FOUND`, `SANDBOX`, `SCHEMA`, `TOOL_BLOCKED`, `TIMEOUT`, `UNKNOWN`.
* **`ReplanContext` (dataclass):** agrupa o estado completo do replanejamento (task, current_step, tool_history, retries, exceção, orçamento).
* **`ReplanAction` (dataclass):** representa um ou mais passos gerados pelo replanejador, com indicação da fonte (`heuristic` ou `llm`) e o motivo da substituição.
* **`RetryPolicy` (classe):** limites configuráveis de tentativas (`max_total=2`, `max_heuristic=2`, `max_llm=1`), preparada para evoluir por ferramenta.
* **`classify_error(message) → ErrorCategory`:** classificação determinística baseada na mensagem de erro.
* **`try_heuristic(category, tool, args) → Optional[ReplanAction]`:** heurísticas determinísticas. Atualmente cobre `FileNotFoundError` (gera `grep` + `directory_lister`). Heurísticas inseguras foram deliberadamente excluídas.
* **`ask_llm_for_alternative(step, error, orchestrator) → Optional[ReplanAction]`:** último recurso — consulta o LLM para sugerir um passo alternativo, apenas se a heurística falhar e a `RetryPolicy` permitir.
* **`replan(ctx, error_msg, orchestrator) → Optional[ReplanAction]`:** ponto de entrada único chamado por `PlanExecutor` e `ReactiveLoop`. Registra logs de cada replanejamento via `logger.info`.
* **Integração:** `error_handler.py` retorna `"replan"` para erros recuperáveis; `plan_executor.py` usa loop `while` e injeta novos passos no plano; `reactive_loop.py` chama o replanner quando uma ferramenta falha.

### 4.20. [semantic_memory.py](agent/semantic_memory.py) 🆕
Camada de busca semântica sobre a memória do agente. Usa o modelo `all-MiniLM-L6-v2` (via `sentence-transformers`) para gerar embeddings dos resumos de arquivos armazenados em `AgentMemory.state['file_summaries']`.
* **`SemanticMemory(memory, model_name)`**: Inicializa a camada com lazy loading do modelo.
* **`build_index()`**: Constrói o índice vetorial a partir dos resumos existentes.
* **`find_similar_files(query, top_k=5)`**: Retorna os arquivos mais relevantes semanticamente para uma consulta.
* **Integração**: Chamado por `ContextManager.get_file_hints()` para enriquecer o prompt com arquivos relacionados ao objetivo, mesmo quando o nome do arquivo não é mencionado literalmente.

### 4.21. [health_check.py](agent/health_check.py) 🆕
Módulo de diagnóstico ("Doctor") do agente. Executável via `python -m agent.health_check` ou pelo comando `/doctor` na CLI.
* Verifica: versão do Python, validade do `config.json`, integridade da memória e backups, hashes de arquivos, diretórios órfãos, permissões de leitura/escrita, carregamento de skills, e tamanho de logs/métricas.
* Gera relatório visual no terminal e arquivo `health_report.json`.
* **Comando CLI**: `/doctor` ou `/diagnostico` (integrado em `commands.py`).

### 4.22. [cancellation.py](agent/cancellation.py) 🆕
Utilitário simples de cancelamento cooperativo.
* **`CancellationToken`**: Classe com flag `cancelled`, usada para sinalizar cancelamento de tarefas de forma programática. Pode ser expandida para cancelamento futuro (ex.: via botão em interface web).

### 4.23. [complexity.py](agent/complexity.py) 🆕
Detector de complexidade de objetivos. Decide se um objetivo deve ser tratado via planejamento hierárquico (MacroPlan) ou pelo fluxo linear padrão.
* **`is_hierarchical(objective) -> bool`**: Calcula uma pontuação heurística baseada em palavras‑chave, estrutura do texto e comprimento. Retorna `True` se a pontuação atingir o limiar configurável `HIERARCHICAL_SCORE_THRESHOLD`.
* **`compute_complexity_score(objective) -> float`**: Retorna a pontuação bruta para diagnóstico.

### 4.24. [hierarchical_planner.py](agent/hierarchical_planner.py) 🆕
Planejador hierárquico: decompõe um objetivo complexo em um `MacroPlan` (lista de `MacroStep`), usando o LLM.
* **`Priority` (Enum)**: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.
* **`MacroStep` (dataclass)**: `id`, `title`, `goal`, `priority`, `depends_on` (reservado para paralelismo futuro), `estimated_tools` (validados contra as ferramentas disponíveis).
* **`MacroPlan` (dataclass)**: `objective`, `steps`, `schema_version`.
* **`HierarchicalPlanner(ask_model, valid_tools)`**: Recebe uma função `ask_model` injetada e a lista de ferramentas válidas. O método `build_plan(objective)` retorna um `MacroPlan` ou `None` (fallback para fluxo linear). Desacoplado do `Orchestrator`.

### 4.25. [hierarchical_executor.py](agent/hierarchical_executor.py) 🆕
Executor de `MacroPlan`: orquestra a execução de cada `MacroStep` como uma mini‑tarefa independente.
* Recebe por injeção: `plan_builder`, `plan_executor`, `final_responder`, `context_manager`, `session`, `tracker` e `summarizer`.
* Para cada passo: gera e executa um micro‑plano, coleta resultados das ferramentas (sem chamar `FinalResponder`), atualiza o `TaskTracker` e alimenta o `IncrementalSummarizer`.
* Ao final, chama o `FinalResponder` **uma única vez** para gerar a resposta consolidada.

### 4.26. [incremental_summarizer.py](agent/incremental_summarizer.py) 🆕
Acumulador/sumarizador incremental de resultados parciais durante execução hierárquica.
* **`IncrementalSummarizer(summarize_fn, max_items, max_chars)`**: Recebe uma função de sumarização injetada. Acumula itens de texto e os condensa periodicamente em resumos para evitar explosão de contexto.
* **`add(item)`**: Adiciona um resultado parcial.
* **`force_flush()`**: Força a condensação de itens pendentes.
* **`get_accumulated_content()`**: Retorna todo o conteúdo (resumos + itens recentes) para a consolidação final.

### 4.27. [task_report.py](agent/task_report.py) 🆕
Construtor do Relatório da Tarefa — registro de auditoria consolidado ao final de cada execução.
* **`TaskReportBuilder(config)`**: Constrói um dicionário estruturado com `task_id`, `objective`, `success`, `steps`, `replan_events`, `metrics`, `errors` e `final_answer_preview`.
* **`save_report(report, format, path)`**: Persiste o relatório em JSON (padrão) ou Markdown, com gravação atômica.
* Totalmente desacoplado do `Orchestrator` — depende apenas do estado público de `AgentState` e métricas.

### 4.28. [task_tracker.py](agent/task_tracker.py) 🆕
Rastreador de progresso da execução hierárquica. Mantém um arquivo JSON estruturado (fonte de verdade) e renderiza um arquivo Markdown para leitura humana.
* **`TaskTracker(json_path, md_path)`**: Inicializa os caminhos dos artefatos.
* **`start(objective, steps, metadata)`**: Inicia o tracking com os passos do `MacroPlan`.
* **`mark_running / mark_completed / mark_failed / mark_skipped(step_id)`**: Atualiza o status de cada passo.
* **`finish_success / finish_failure(summary)`**: Finaliza o tracking global.
* **Enums**: `StepStatus` (PENDING, RUNNING, COMPLETED, FAILED, SKIPPED) e `TaskStatus` (RUNNING, COMPLETED, FAILED).
* Todas as gravações são atômicas e falhas de I/O nunca escapam para o chamador.

### 4.29. [grammars.py](agent/grammars.py) 🆕
Infraestrutura de suporte a gramáticas GBNF (GGML BNF) para forçar o LLM a gerar JSON estruturalmente válido.
* **Gramáticas por `step_type`**: define strings GBNF para `plan`, `macro_plan`, `tool_decision`, `final`, `summarize` e `replan`.
* **Sentinela `AUTO_GRAMMAR`**: indica que a gramática deve ser escolhida automaticamente com base no `step_type`.
* **`get_grammar(step_type) -> str | None`**: retorna a gramática apropriada, respeitando a flag `ENABLE_GBNF` de `config.py`.
* **Integração**: usado por `ContextManager.ask_model()` para injetar o campo `grammar` no payload automaticamente. A validação semântica permanece como responsabilidade do `PlanValidator`.

### 4.30. [tool_metadata.py](agent/tool_metadata.py) 🆕
Metadados estáticos de custo e características das ferramentas, usados pelo `PlanValidator` e `PlanOptimizer`.
* **`ToolMetadata` (dataclass)**: `cost`, `reads_disk`, `writes_disk`, `modifies_workspace`, `cacheable`, `side_effects`, `category` (READ, WRITE, EXECUTE, SEARCH, ANALYZE, NETWORK).
* **`TOOL_METADATA`**: dicionário mapeando nome da ferramenta → `ToolMetadata` para todas as skills.
* **`estimate_step_cost(tool, args) -> int`**: refina o custo para `file_reader` (parcial vs inteiro) e `file_writer` (por ação: patch, ast_patch, write).

### 4.31. [plan_validator.py](agent/plan_validator.py) 🆕
Validador de planos que apenas diagnostica, nunca modifica. Executado antes e depois do `PlanOptimizer`.
* **`ValidationReport`**: `is_valid`, `errors`, `warnings`, `blocked_steps` (lista de `BlockedStep` com `index` e `reason`).
* **Validações**: schema e ferramentas, esvaziamento de `analysis_notes.md`, patch sem leitura prévia (aviso), escritas consecutivas (aviso), dependências invertidas (bloqueio).
* **Integração**: chamado pelo `Orchestrator` após a geração do plano e após a otimização. Passos bloqueados são encaminhados ao `Replanner`.

### 4.32. [plan_optimizer.py](agent/plan_optimizer.py) 🆕
Otimizador de planos que aplica apenas transformações comprovadamente equivalentes.
* **`OptimizationReport`**: `optimized_steps`, `removed_duplicates`, `cost_before`, `cost_after`, `cost_details`, `transformations`, `changed`.
* **Otimizações seguras**: remoção de duplicatas exatas (apenas ferramentas `cacheable`), reordenação de leituras/buscas/análises independentes (nunca move ferramentas com `side_effects=True`).
* **Nunca** insere passos novos, converte ferramentas ou altera argumentos. Usa `ToolMetadata` para todas as decisões.

### 4.33. [security_patterns.py](agent/security_patterns.py) 🆕
Banco de dados de padrões de segurança. NÃO contém lógica — apenas metadados.
* **`PATTERN_DATABASE`**: dicionário com 12 padrões (execução, desserialização, criptografia fraca, segredos, path traversal, injeção, misconfig).
* Cada padrão possui: `pattern_id`, `pattern`, `family`, `cwe`, `owasp`, `why_interesting`, `default_priority`.
* **`lookup(pattern_id) -> dict`**: retorna os metadados do padrão ou `{}` se não encontrado.

### 4.34. [security_scanner.py](agent/security_scanner.py) 🆕
Consolidador de fatos de segurança. NÃO usa LLM, NÃO executa ferramentas.
* **`Finding` (dataclass)**: `pattern_id`, `pattern`, `location`, `start_line`, `end_line`, `symbol`, `snippet` (máx 120 chars), `detection_method`, `metadata`.
* **`consolidate(code_analyzer_result, grep_results) -> List[Finding]`**: normaliza, trunca snippets, remove duplicatas e enriquece com metadados do `security_patterns.py`.
* Nenhuma inferência de severidade ou risco — apenas fatos.

---

## 5. Mapeamento de Ferramentas (Skills) em `agent/skills/`

### Contrato Obrigatório da `BaseSkill` ([agent/skills/base.py](agent/skills/base.py))

Toda skill **deve** herdar de `BaseSkill` e implementar os seguintes membros:

| Membro | Tipo | Obrigatório | Descrição |
| :--- | :--- | :--- | :--- |
| `name` | `@property str` | ✅ Sim | Identificador único da skill. É o valor que o modelo usa para selecionar a ferramenta. |
| `description` | `@property str` | ✅ Sim | Texto curto descrevendo o que a skill faz (exibido ao modelo no prompt de planejamento). |
| `get_schema()` | `dict` | ⚠️ Recomendado | Dicionário descrevendo os argumentos esperados (nome → `{type, description}`). Usado por `validate_tool_args` no `parsers.py`. |
| `execute(args: dict)` | `dict` | ✅ Sim | Lógica principal. **Deve retornar o contrato padrão abaixo.** |

**Contrato de retorno de `execute()`** — toda skill deve retornar um dicionário com estas chaves:
```python
{
    "ok":      bool,  # True se a operação foi bem-sucedida
    "done":    bool,  # True se a tarefa da skill foi concluída
    "data":    Any,   # Dados de saída (pode ser None)
    "error":   str,   # Mensagem de erro (ou None)
    "message": str    # Descrição amigável do resultado
}
```
> ⚠️ O `parsers.normalize_tool_result()` detecta e corrige retornos malformados automaticamente, mas retornar o contrato correto é uma obrigação da skill.

### Tabela de Skills Disponíveis

| Nome da Skill (CLI) | Classe Correlata | Descrição | Principais Recursos / Restrições |
| :--- | :--- | :--- | :--- |
| `directory_lister` | `DirectoryListerSkill` | Lista conteúdo de diretórios. | Restringe acesso fora da pasta do projeto e retorna tipo de arquivo (`file` ou `dir`). |
| `file_reader` | `FileReaderSkill` | Lê conteúdo de arquivos. | Limita a leitura a arquivos de texto (lista de extensões permitidas). Implementa chunking e resumo automático para arquivos grandes, salvando o conteúdo bruto em `.temp_analysis/`. |
| `file_writer` | `FileWriterSkill` | Cria ou modifica arquivos. | Impede alteração de arquivos do núcleo do agente (`CORE_FILES_BLOCKLIST`). Suporta escrita inteira, anexo, substituição por correspondência simples de linhas e substituição sintática de blocos via árvore abstrata (`ast_patch`). 
* **Proteção interativa**: antes de modificar qualquer arquivo dentro do diretório `agent/`, o sistema solicita confirmação do usuário, evitando alterações acidentais em componentes críticos.
* **Workspace isolado**: todas as edições são feitas primeiro em uma cópia temporária (`.temp_analysis/workspace/`); o arquivo original só é substituído após confirmação explícita.|
| `python_executor` | `PythonExecutorSkill` | Executa código Python em uma sandbox de múltiplas camadas (Isolation Box). | Cada execução roda em um workspace efêmero (`TemporaryDirectory`) isolado via subprocesso (sem `os.chdir`). Validação AST expandida bloqueia imports perigosos (whitelist), execução dinâmica (`eval`/`exec`/`compile`), monkey patch de builtins críticos, path traversal e caminhos absolutos, APIs de resolução de caminho (`abspath`/`resolve`) e padrões de criação de processos — independente do módulo de origem. Após a execução, valida o estado real do workspace (limites de arquivos, diretórios, profundidade, tamanho, e detecção de symlinks/junctions) e impõe limites rígidos de stdout/stderr. Política fail-closed: qualquer caminho ou comportamento não classificável estaticamente é rejeitado. Ver `tests/test_python_executor.py` para a cobertura completa. |
| `shell` | `ShellSkill` | Executa comandos Shell. | Permite apenas comandos explícitos da lista branca (`pytest`, `python`, `pip`, `ruff`, `mypy`, `npm`, `node`, `echo`, `type`, `dir`, `tree`, `ls`, e leitura/commit do `git`). Limita saída de caracteres. |
| `git_reader` | `GitSkill` | Executa comandos de leitura do Git. | Aceita unicamente os comandos `status`, `log` e `diff` de forma segura. |
| `grep` | `GrepSkill` | Busca por padrões regex. | Varre recursivamente o diretório raiz à procura de correspondências textuais, filtrando pastas e arquivos de log temporários. |
| `web_search` | `WebSearchSkill` | Busca informações na Web. | Utiliza a API DuckDuckGo (`ddgs`) para coletar snippets atualizados e injeta a data/hora atual do sistema para calibrar o LLM. |
| `summarize` | `SummarizeSkill` | Resume textos técnicos. | Encaminha o texto para o LLM com o prompt instruído a reter nomes de variáveis, métodos, classes e dependências técnicas relevantes. |
| `session_memory` | `SessionMemorySkill` | Edita a memória do agente. | Facilita a leitura, inserção e deleção de dados na chave `key_findings` da memória. |
| `calculator` | `CalculatorSkill` | Avalia expressões matemáticas. | Realiza cálculo seguro por parsing de AST de operadores simples e funções matemáticas da biblioteca padrão (`sqrt`, `sin`, `log`, etc.), sem usar `eval()` nativo. |
| `echo` | `EchoSkill` | Repete o input fornecido. | Utilizado para teste básico de infraestrutura. |
| `code_analyzer` | `CodeAnalyzerSkill` | Analisa arquivos Python e gera mapa estrutural com dependências de chamadas. | **Modos:** `file` (único arquivo), `directory` (diretório inteiro), `security` (extrai fatos observáveis via AST para análise de segurança). Suporta modo compacto para visão geral. **Modo `security`**: extrai imports classificados, fontes de entrada, chamadas perigosas, acesso a filesystem/rede/criptografia e call graph simples. Snippets truncados em 120 caracteres. Nenhuma decisão de vulnerabilidade é tomada — apenas fatos. |


---

## 6. A Suíte de Testes (tests/)

O sistema possui testes automatizados implementados com a ferramenta `pytest`. Os testes cobrem:
* **[test_config.py](tests/test_config.py):** Valida o comportamento da função de carregar configurações, certificando-se de que arquivos inexistentes disparem as exceções corretas, parâmetros vazios adotem os fallbacks seguros e tipos inválidos/limites numéricos sejam higienizados conforme a especificação.
* **[test_hello.py](tests/test_hello.py) e [test_temp.py](tests/test_temp.py):** Verificações e validações de infraestrutura de testes básicas.
* **[test_orchestrator.py](tests/test_orchestrator.py):** Concentra a validação dos parsers do orquestrador. Testa exaustivamente a extração de JSONs embutidos em blocos de códigos markdown limpos ou misturados a textos explicativos, a validação de contratos estruturais de decisão (final vs tool) e o comportamento da higienização e classificação de erros nas respostas das ferramentas.
* **[test_session.py](tests/test_session.py):** Foca nas funcionalidades de `ChatSession`, testando a manipulação do histórico de mensagens, injeção de parâmetros de raciocínio no prompt de sistema e montagem apropriada do payload de rede.
* **[test_grammar.py](tests/test_grammar.py):** Testa a infraestrutura de GBNF — inclusão do campo `grammar` no payload, seleção automática por `step_type`, override explícito, desabilitação via `grammar=None`, fallback em erro 400, cache de backend após fallback, e a flag `ENABLE_GBNF`.

---
 
## 7. Guia de Extensão e Solução de Problemas (Onde Alterar?)

Se você precisar corrigir um problema ou implementar um aprimoramento no projeto, consulte esta tabela rápida para saber exatamente quais arquivos e regras do sistema devem ser modificados:

| Objetivo / O que você quer alterar | Onde encontrar / Arquivo alvo | Descrição da Mudança necessária |
| :--- | :--- | :--- |
| **Criar uma nova ferramenta (Skill)** | Pasta `agent/skills/` | Crie um novo arquivo `<nova_skill>.py` que herda de `BaseSkill`, define `name`, `description`, `get_schema()` e implementa a lógica em `execute()`. |
| **Registrar ou inicializar uma Skill** | [agent/skills/\_\_init\_\_.py](agent/skills/__init__.py) | Adicione o nome da classe e seus parâmetros de construtor no dicionário `SKILL_CONFIG`. |
| **Ajustar as ferramentas de uma Persona** | [agent/router.py](agent/router.py) (função `get_persona_config`) | Adicione ou remova a string do nome da skill da lista de ferramentas associadas a cada persona (`coder`, `researcher`, `general`). |
| **Mudar os prompts das Personas** | [agent/router.py](agent/router.py) (dicionário `PERSONA_PROMPTS`) | Ajuste as regras específicas de contexto que são concatenadas ao prompt principal para cada tipo de tarefa. |
| **Alterar o Prompt de Sistema global** | [agent/prompts.py](agent/prompts.py) (`AGENT_SYSTEM_PROMPT`) | Modifique as regras contratuais, a estrutura do JSON de saída exigido e diretrizes gerais de comportamento do modelo. |
| **Mudar o endpoint, modelo ou timeouts da API** | `config.json` ou `config.example.json` | Edite as chaves globais `api_url`, `model`, `timeout` e `temperature`. |
| **Ajustar limites de custo da tarefa** | [agent/cost_guard.py](agent/cost_guard.py) | Altere as constantes `DEFAULT_MAX_TASK_STEPS`, `DEFAULT_MAX_TASK_TOKENS` ou `DEFAULT_MAX_TASK_TOOL_CALLS`. |
| **Ajustar limites do Watchdog (timeout global, loop, falhas consecutivas)** | [agent/watchdog.py](agent/watchdog.py) e `config.json` | Altere as constantes `DEFAULT_MAX_TASK_WALL_SECONDS`, `DEFAULT_MAX_REPEATED_NO_PROGRESS`, `DEFAULT_MAX_CONSECUTIVE_SAME_ERROR` no módulo, ou defina `max_task_wall_seconds`, `max_repeated_no_progress`, `max_consecutive_same_error` no arquivo de configuração. |
| **Ajustar limites ou política do Replanner** | [agent/replan.py](agent/replan.py) | Altere a classe `RetryPolicy` (`max_total`, `max_heuristic`, `max_llm`) ou expanda `try_heuristic` com novas categorias de erro. |
| **Modificar a lógica de comunicação HTTP com o LLM** | [agent/model_client.py](agent/model_client.py) | Ajuste o método `request` para alterar retry, timeouts ou formato de métricas. |
| **Ajustar validação de limites de custo ou fallbacks de config** | [config.py](config.py) e [agent/plan_executor.py](agent/plan_executor.py) | Altere a função `carregar_config` para adicionar campos na validação e a função `_check_cost_limits` no executor para regular o teto de tokens, passos e chamadas. |
| **Modificar a lógica de compressão de histórico de conversas** | [agent/context_manager.py](agent/context_manager.py) (função `maybe_compress_context`) | Altere os limites de tokens da janela de contexto ou as regras de sumarização do histórico do chat. |
| **Alterar a lógica do linter ou do backup antes de rodar código** | [agent/workspace.py](agent/workspace.py) e [agent/skills/file_writer.py](agent/skills/file_writer.py) | Ajuste as funções de criação de pontos de restauração (`create_restore_point`), de verificação de sintaxe (`lint_check`) ou expanda a `CORE_FILES_BLOCKLIST` no file_writer. |
| **Ajustar limites ou regras da sandbox do python_executor (Isolation Box)** | [agent/skills/python_executor.py](agent/skills/python_executor.py) | Altere as constantes da classe (`MAX_FILES_CREATED`, `MAX_DIRS_CREATED`, `MAX_TREE_DEPTH`, `MAX_FILE_SIZE_BYTES`, `MAX_TOTAL_SIZE_BYTES`, `MAX_STDOUT_HARD_LIMIT`, `MAX_STDERR_HARD_LIMIT`) para os limites pós-execução, ou os conjuntos `BLOCKED_MODULES`, `PROCESS_CREATION_ATTRS`, `DANGEROUS_PATH_APIS`, `CRITICAL_BUILTINS_TO_PROTECT` no topo do módulo para as regras de validação AST (Camada 3). |
| **Corrigir como o JSON de saída é parseado ou validado** | [agent/parsers.py](agent/parsers.py) | Ajuste a expressão regular de extração em `extract_json` ou expanda a validação de parâmetros das ferramentas em `validate_tool_args`. |
| **Mudar o ciclo automático de teste e correção do código** | [agent/auto_coder.py](agent/auto_coder.py) (função `test_and_correct`) | Modifique o prompt de geração de testes, o comando de execução do subprocesso do arquivo temporário de testes ou o número de tentativas de correção automática. |
| **Incluir novos comandos com barra na CLI** | [commands.py](commands.py) | Registre o comando na tabela da função `exibir_menu` e implemente a respectiva condicional na função `handle_command`. |
| **Executar diagnóstico de saúde** | `python -m agent.health_check` ou `/doctor` na CLI | Verifica integridade do sistema e gera relatório. |
| **Rodar benchmark** | `python benchmark.py` | Executa 4 tarefas padronizadas e mede desempenho. |
| **Ajustar validação automática** | `config.json` → chave `validation` | Habilita/desabilita `ruff`, `mypy`, `pytest` e o replanejamento por falha de validação. |
| **Consultar métricas** | `agent_metrics.jsonl` | Arquivo JSONL com timestamp, step_type, tool, tokens, duração e sucesso de cada chamada ao modelo. |
| **Ajustar sensibilidade do planejamento hierárquico** | `agent/complexity.py` | Altere `HIERARCHICAL_SCORE_THRESHOLD` ou as listas de palavras‑chave. |
| **Configurar relatório da tarefa** | `config.json` → chave `task_report` | Altere `enabled`, `format` (`json`/`markdown`) ou `output_dir`. |
| **Configurar checkpointing** | `config.json` → chave `checkpoint_file` | Altere o caminho do arquivo de checkpoint. |
| **Retomar tarefa interrompida** | `/retry` na CLI | Restaura o estado a partir do checkpoint e continua a execução. |
| **Ativar/desativar gramáticas GBNF** | `config.json` → chave `ENABLE_GBNF` | Altere para `false` para desabilitar globalmente. |
| **Ajustar gramáticas GBNF** | `agent/grammars.py` | Edite as strings GBNF ou adicione novas entradas no dicionário `GRAMMARS`. |
| **Ajustar metadados de ferramentas** | `agent/tool_metadata.py` | Altere custos, categorias ou flags de `side_effects`/`cacheable`. |
| **Adicionar novo padrão de segurança** | `agent/security_patterns.py` | Adicione uma entrada ao dicionário `PATTERN_DATABASE`. |
| **Ajustar mapeamento de tipos para padrões** | `agent/security_scanner.py` | Altere o dicionário `_TYPE_TO_PATTERN`. |