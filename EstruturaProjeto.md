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
└── tests
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

### 3.3. [session.py](session.py)
Encapsula o gerenciamento de sessões do chat e comunicação direta com a API do LLM (servidor compatível com OpenAI):
* **Payloads:** Monta dinamicamente a estrutura de requisições, injetando instruções de raciocínio no prompt de sistema (`[THINKING]`) e adicionando parâmetros de controle de templates como `enable_thinking`.
* **Streaming e Streaming Parser (`process_stream`):** Analisa o protocolo de stream SSE (Server-Sent Events) retornado do endpoint `/v1/chat/completions`, extraindo e enviando trechos de texto em tempo real para os callbacks de pensamento (`reasoning_content`) e de resposta final (`content`).
* **Função Auxiliar (`extrair_json`):** (Removida durante a refatoração — a lógica de extração de JSON está centralizada em `agent/parsers.py`).

### 3.4. [config.py](config.py)
Carrega o arquivo `config.json` e realiza validações minuciosas de segurança e tipos de dados:
* **Fallbacks:** Se uma chave não for encontrada ou tiver o tipo errado (ex.: `temperature` com string ou fora do intervalo [0.0, 2.0]), ele emite um aviso no logger e adota os valores padrões descritos no dicionário `DEFAULT_CONFIG`.
* **Padrões de Prompt:** Define o comportamento padrão do assistente para pensar em inglês e responder em português brasileiro.

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

### 3.9. [pyproject.toml](pyproject.toml) e [requirements.txt](requirements.txt)
Configurações de ambiente. O arquivo `pyproject.toml` especifica as regras de lint do `ruff` (limite de 120 caracteres por linha, regras de import) e do verificador estático `mypy`. O arquivo `requirements.txt` lista pacotes necessários, incluindo `requests` para requisições HTTP, `pytest` para testes unitários, `rich` para formatação visual e `ddgs` para buscas web.

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

### 4.6. [context_manager.py](agent/context_manager.py)
Administra a janela de contexto de tokens e otimiza o tráfego de dados para a API. Após a refatoração (Fix 5), a comunicação HTTP foi extraída para `ModelClient`, permitindo que o `ContextManager` foque exclusivamente na preparação do contexto:
* **Contexto do Projeto:** Constrói um sumário dos arquivos presentes no repositório listando arquivos rastreados via `git ls-files` ou scaneando o diretório raiz.
* **Compressão de Diálogo (`maybe_compress_context`):** Monitora a janela de tokens. Se o histórico estimado de conversas ultrapassar o limiar de compressão (80% do limite de 8192 tokens), o sistema gera um resumo condensado da conversa via chamada de modelo externa e limpa as mensagens intermediárias, mantendo o resumo no topo.
* **Compactação de Leituras (`build_compact_view`):** Quando o histórico atinge limites elevados, localiza leituras de arquivos passadas e as substitui por seus resumos técnicos extraídos da memória, poupando espaço útil no prompt.
* **Mapeamento de Linhas (`get_file_hints`):** Busca menções a arquivos no objetivo do usuário para expor o total de linhas de cada arquivo, ajudando o modelo a decidir a paginação de leitura.
* **Comunicação com o Modelo (`ask_model`):** Prepara o contexto completo (system prompt, histórico, memória) e delega a requisição HTTP ao `ModelClient`.

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
* **Lint Check (`lint_check`):** Roda compilação sintática nativa Python (`py_compile`) e, caso a ferramenta esteja instalada no ambiente, executa a verificação estática de estilo `flake8` com limite de 120 colunas.

### 4.13. [final_response.py](agent/final_response.py)
Compila a resposta definitiva do agente:
* **Geração da Resposta:** Reúne o histórico de uso de ferramentas e as anotações geradas em `analysis_notes.md` para submeter um prompt final ao LLM sem o uso de ferramentas adicionais.
* **Auditoria de Menções:** Examina a resposta em linguagem natural por meio de expressões regulares à procura de menções a caminhos de arquivos. Caso o texto mencione arquivos que o agente não leu de fato através de suas ferramentas, ele anexa um aviso no final da resposta alertando que sugestões sobre aqueles arquivos específicos podem ser imprecisas.

### 4.14. [router.py](agent/router.py)
Executa a triagem inteligente de prompts e ferramentas:
* Identifica se uma solicitação de usuário é meramente trivial (saudações como "olá" ou "quem é você") para atribuir a persona `general` e evitar consumo de plano.
* Utiliza busca de palavras-chave para detectar listagens estritas (`general`), tarefas de código (`coder`) ou pesquisas web (`researcher`).
* Se houver ambiguidade, submete o objetivo ao LLM sob o prompt `ROUTER_PROMPT` para obter a persona final em formato JSON.
* Cada persona ativa um subset de ferramentas e injeta regras de comportamento específicas no prompt inicial.

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
| `file_writer` | `FileWriterSkill` | Cria ou modifica arquivos. | Impede alteração de arquivos do núcleo do agente (`CORE_FILES_BLOCKLIST`). Suporta escrita inteira, anexo, substituição por correspondência simples de linhas e substituição sintática de blocos via árvore abstrata (`ast_patch`). |
| `python_executor` | `PythonExecutorSkill` | Executa código Python. | Executa o código em um subprocesso com timeout. Valida o script via AST para bloquear imports perigosos (permite apenas lista branca como `math`, `re`, `json`, etc.) e proíbe abertura de arquivos em modo escrita ou remoção de caminhos. |
| `shell` | `ShellSkill` | Executa comandos Shell. | Permite apenas comandos explícitos da lista branca (`pytest`, `python`, `pip`, `ruff`, `mypy`, `npm`, `node`, `echo`, `type`, `dir`, `tree`, `ls`, e leitura/commit do `git`). Limita saída de caracteres. |
| `git_reader` | `GitSkill` | Executa comandos de leitura do Git. | Aceita unicamente os comandos `status`, `log` e `diff` de forma segura. |
| `grep` | `GrepSkill` | Busca por padrões regex. | Varre recursivamente o diretório raiz à procura de correspondências textuais, filtrando pastas e arquivos de log temporários. |
| `web_search` | `WebSearchSkill` | Busca informações na Web. | Utiliza a API DuckDuckGo (`ddgs`) para coletar snippets atualizados e injeta a data/hora atual do sistema para calibrar o LLM. |
| `summarize` | `SummarizeSkill` | Resume textos técnicos. | Encaminha o texto para o LLM com o prompt instruído a reter nomes de variáveis, métodos, classes e dependências técnicas relevantes. |
| `session_memory` | `SessionMemorySkill` | Edita a memória do agente. | Facilita a leitura, inserção e deleção de dados na chave `key_findings` da memória. |
| `calculator` | `CalculatorSkill` | Avalia expressões matemáticas. | Realiza cálculo seguro por parsing de AST de operadores simples e funções matemáticas da biblioteca padrão (`sqrt`, `sin`, `log`, etc.), sem usar `eval()` nativo. |
| `echo` | `EchoSkill` | Repete o input fornecido. | Utilizado para teste básico de infraestrutura. |

---

## 6. A Suíte de Testes (tests/)

O sistema possui testes automatizados implementados com a ferramenta `pytest`. Os testes cobrem:
* **[test_config.py](tests/test_config.py):** Valida o comportamento da função de carregar configurações, certificando-se de que arquivos inexistentes disparem as exceções corretas, parâmetros vazios adotem os fallbacks seguros e tipos inválidos/limites numéricos sejam higienizados conforme a especificação.
* **[test_hello.py](tests/test_hello.py) e [test_temp.py](tests/test_temp.py):** Verificações e validações de infraestrutura de testes básicas.
* **[test_orchestrator.py](tests/test_orchestrator.py):** Concentra a validação dos parsers do orquestrador. Testa exaustivamente a extração de JSONs embutidos em blocos de códigos markdown limpos ou misturados a textos explicativos, a validação de contratos estruturais de decisão (final vs tool) e o comportamento da higienização e classificação de erros nas respostas das ferramentas.
* **[test_session.py](tests/test_session.py):** Foca nas funcionalidades de `ChatSession`, testando a manipulação do histórico de mensagens, injeção de parâmetros de raciocínio no prompt de sistema e montagem apropriada do payload de rede.

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
| **Modificar a lógica de comunicação HTTP com o LLM** | [agent/model_client.py](agent/model_client.py) | Ajuste o método `request` para alterar retry, timeouts ou formato de métricas. |
| **Ajustar validação de limites de custo ou fallbacks de config** | [config.py](config.py) e [agent/plan_executor.py](agent/plan_executor.py) | Altere a função `carregar_config` para adicionar campos na validação e a função `_check_cost_limits` no executor para regular o teto de tokens, passos e chamadas. |
| **Modificar a lógica de compressão de histórico de conversas** | [agent/context_manager.py](agent/context_manager.py) (função `maybe_compress_context`) | Altere os limites de tokens da janela de contexto ou as regras de sumarização do histórico do chat. |
| **Alterar a lógica do linter ou do backup antes de rodar código** | [agent/workspace.py](agent/workspace.py) e [agent/skills/file_writer.py](agent/skills/file_writer.py) | Ajuste as funções de criação de pontos de restauração (`create_restore_point`), de verificação de sintaxe (`lint_check`) ou expanda a `CORE_FILES_BLOCKLIST` no file_writer. |
| **Corrigir como o JSON de saída é parseado ou validado** | [agent/parsers.py](agent/parsers.py) | Ajuste a expressão regular de extração em `extract_json` ou expanda a validação de parâmetros das ferramentas em `validate_tool_args`. |
| **Mudar o ciclo automático de teste e correção do código** | [agent/auto_coder.py](agent/auto_coder.py) (função `test_and_correct`) | Modifique o prompt de geração de testes, o comando de execução do subprocesso do arquivo temporário de testes ou o número de tentativas de correção automática. |
| **Incluir novos comandos com barra na CLI** | [commands.py](commands.py) | Registre o comando na tabela da função `exibir_menu` e implemente a respectiva condicional na função `handle_command`. |