# Relatório Detalhado de Implementação: Fases 0-4

> Estado factual: este relatório descreve o snapshot atual. A existência de
> uma classe não é, sozinha, evidência de integração concluída. O runbook
> [plano executável das fases 0–4](plano-conclusao-fases-0-4.md) é a fonte
> operacional para o que ainda estiver marcado como parcial ou pendente.

## Objetivo

Este documento descreve, em nível técnico, como as fases 0 a 4 foram implementadas no repositório `LLM`.
Ele está formatado para revisão por terceiros e inclui referências a arquivos, componentes e testes relevantes.

---

## Sumário Executivo

A implementação buscou completar o ciclo básico de um assistente standalone, desde definição de caminhos e configuração até planejamento seguro, autorização por capacidades, execução de ferramentas e retomada de checkpoint.

O trabalho cobriu:

- Fase 0: Constituição arquitetural e contratos canônicos
- Fase 1: Standalone verdadeiro com `AgentApplication`, CLI e lifecycle
- Fase 2: Microkernel de ferramentas com `ToolRegistry` e adapters
- Fase 3: Autorização baseada em capacidades e uso de gateway único
- Fase 4: Ciclo completo de planejamento, validação e execução segura

---

## 1. Fase 0 — Constituição arquitetural

### 1.1 Definições e fronteiras

Foram validadas e usadas as seguintes abstrações:

- `assistant`: orquestrador público do runtime, encapsulado por `AgentApplication`
- `model/provider`: abstraído por `ChatSession` e `ModelGateway` na camada de LLM
- `tool`: operação invocável descrita por `agent/tools/contracts.py`
- `extension`: fonte externa de `ToolDescriptor`s via adapters, incluindo `stdio`
- `transport`: interface de comunicação usada por extensões externas
- `workspace`: diretório alvo resolvido por `WorkspaceContext` e `WorkspacePaths`
- `capability`: atributo de autorização de ferramenta e persona
- `artifact`: resultado persistido em `WorkspacePaths.artifacts_dir`
- `memória` e `estado operacional`: separados em `AgentState`, `AgentMemory` e `CheckpointManager`

### 1.2 Arquivos principais

- `agent/runtime/paths.py`
  - `AppPaths.discover()` resolve caminhos de aplicação a partir de `LLM_AGENT_HOME`, variáveis de ambiente ou padrões de sistema.
  - `WorkspacePaths` isola dados, estado, cache, artefatos e checkpoints por workspace.

- `agent/runtime/config.py`
  - Carrega e valida arquivo JSON de configuração.
  - Usa valores default e garante presença de `checkpoint_file`.

- `agent/application.py`
  - `AgentApplication.create()` monta `AppPaths`, `WorkspaceContext`, `ChatSession`, `ToolRegistry`, `Orchestrator` e `ToolInvocationGateway`.
  - Controla lifecycle, lock de workspace, logging e bootstrap de recursos.

- `agent/tools/contracts.py`
  - Define `ToolDescriptor`, `ToolInvocation`, `ToolResult`, `ToolError`.
  - Normaliza estados explícitos de tool: `succeeded`, `failed`, `cancelled`, `timed_out`, `permission_denied`, `protocol_error`, `unavailable`.

---

## 2. Fase 1 — Standalone verdadeiro

### 2.1 Paths e contexto de workspace

- `AppPaths` e `WorkspacePaths` já estavam disponíveis.
- O projeto separa claramente:
  - `config_dir`
  - `data_dir`
  - `state_dir`
  - `cache_dir`
  - `log_dir`
  - `workspace` via `AppPaths.for_workspace()`.

### 2.2 Configuração versionada

- `agent/runtime/config_schema.py`
  - Schema versionado (`schema_version` obrigatório).
  - Validação de limites, perfis e seções específicas.

- `agent/runtime/config_repository.py`
  - Carrega e valida configuração, preserva precedência de `CLI > ambiente > arquivo > defaults`.
  - Testes em `tests/unit/runtime/test_config_repository.py` garantem esquema e versões.

### 2.3 `AgentApplication` e lifecycle

- `AgentApplication.create()` foi a composição central.
- Foi validado que:
  - logging é inicializado após paths resolvidos
  - workspace e paths são separados por workspace
  - recurso de lock evita instâncias simultâneas na mesma workspace

- Testes relevantes:
  - `tests/integration/test_standalone_application.py`
  - entre outros, validam criação de aplicação, workspace isolado, config init e lock.

### 2.4 CLI e modo headless

- `agent/interfaces/cli/app.py` contém parser de CLI com comandos:
  - `chat`
  - `run`
  - `doctor`
  - `config` (`init`, `path`, `validate`, `migrate`)
  - `state` (`migrate`)
  - `tools` (`list`, `add`, `enable`, `disable`, `doctor`)

- O comando `run` já suporta execução headless e output JSON.

---

## 3. Fase 2 — Microkernel de ferramentas

### 3.1 Contratos canônicos de ferramenta

Implementados em:

- `agent/tools/contracts.py`

Definições incluíram:

- `ToolDescriptor`
- `ToolInvocation`
- `ToolResult`
- `ToolError`
- `ToolAdapter` (protocol)

### 3.2 Registro dinâmico de ferramentas

- `agent/tools/tool_registry.py`
  - `ToolRegistry` recebe múltiplos adapters.
  - Permite lookup por nome e invocação via `registry.invoke()`.

- `tests/unit/tools/test_tool_registry.py`
  - Garante registro e lookup de adapters.

### 3.3 Adapters de ferramentas

- `agent/tools/builtin_adapter.py`
  - Expõe skills builtin como `ToolDescriptor`/`ToolInvocation`.

- `agent/tools/stdio_adapter.py`
  - Suporte a ferramentas externas via protocolo JSONL/stdio.
  - Mantém manifest, framing, status e correlação por `invocation_id` no
    protocolo `1.0`, delegando execução bounded aos helpers abaixo.

- `agent/tools/stdio_process.py` e `agent/tools/process_tree.py`
  - Drenam stdout/stderr concorrentemente com limites de 1 MiB, timeout,
    encerramento solicitado da árvore e cleanup bounded de pipes/threads.
  - Usam grupo de processos no POSIX e Job Object nativo no Windows, sem
    dependência adicional. Falhas de terminação, Job Object e drenagem são
    observáveis como `CLEANUP_ERROR`; remoção de status privado gera diagnóstico
    bounded sem substituir o resultado principal.
  - O Gate 1 está `READY_FOR_CI`, permanecendo pendente apenas a evidência do CI
    Windows e Ubuntu. No Windows, o launcher interno é associado ao Job Object
    antes de iniciar a extension; o teste POSIX usa readiness e requer execução
    Linux no CI.

---

## 4. Fase 3 — Ferramentas externas e segurança

### 4.1 Gateway único de invocação

- `agent/tools/invocation_gateway.py`
  - Controla:
    - existência da tool no registry
    - autorização por `active_skills`
    - autorização por `allowed_capabilities`
    - validação de schema de argumentos
    - timeout e telemetria
    - gravação de resultado em estado

- O gateway é usado sempre que possível via `agent/tool_executor.py`.

### 4.2 Autorização dinâmica e capacidades

- `agent/skills/policy.py`
  - Definiu `PERSONA_CAPABILITIES` e `persona_allowed_capabilities()`.
  - `builtin_skills_for_persona()` coleta skills permitidas com base em capacidades.

- `agent/orchestrator.py`
  - `_route_persona()` determina persona, `active_skills` e `allowed_capabilities`.
  - Armazena prompt e persona no estado do agente.

- `agent/planning/plan_validator.py`
  - Valida planos também contra capacidades exigidas pelas ferramentas.

### 4.3 Caminho de segurança

- `agent/orchestration/security_service.py`
  - Invoca `code_analyzer` pelo gateway autorizado.
  - Usa `active_skills` e `allowed_capabilities` do orchestrator.

---

## 5. Fase 4 — Ciclo completo do assistente

### 5.1 Validação e execução de planos

- `agent/planning/execution_gateway.py`
  - Único ponto de entrada para validar e executar planos.
  - Usa `PlanValidator` com `active_skills` e `allowed_capabilities`.
  - Otimiza planos e replaneja etapas bloqueadas.

- `agent/planning/plan_validator.py`
  - Bloqueia ferramentas sem skill permitida.
  - Bloqueia ferramentas com capacidades acima do autorizado.

- `agent/planning/replan.py`
  - Replaneja passos bloqueados e aplica validação de capacidades às etapas substitutas.

### 5.2 Checkpoint e retomada

- `agent/state.py`
  - `AgentState.to_checkpoint_dict()` já serializa o estado operacional.
  - Extendi para armazenar `persona` e `persona_prompt`.

- `agent/orchestrator.py`
  - `_restore_persona_from_state()` reaplica persona/capacidades na retomada.
  - `TaskRunner._resume_plan()` chama essa restauração antes de executar plano salvo.

### 5.3 Garantia do ciclo completo

- `agent/tool_executor.py`
  - Direciona execução via `ToolInvocationGateway` quando disponível.
  - Continua a suportar fallback legacy quando gateway ausente.

- `agent/planning/hierarchical_executor.py`
  - Usa `ExecutionGateway` para executar subplanos hierárquicos.
  - Fecha o ciclo de validação de plano mesmo em execução macro-hierárquica.

---

## 6. Testes e validação

### 6.1 Testes unitários relevantes

- `tests/unit/tools/test_invocation_gateway.py`
  - Cobertura de autorização por skill e capability
  - Cobertura de schema validation e tool unavailability

- `tests/unit/tools/test_stdio_adapter.py`
  - Conformance do protocolo `1.0`: ID correto, ausente, divergente,
    resposta duplicada, framing extra, status inválido, limites concorrentes,
    timeout com descendente e limpeza de threads

- `tests/unit/tools/test_stdio_extension_example.py`
  - Extension de referência ecoa o `invocation_id` recebido

- `tests/unit/planning/test_plan_validator.py`
  - Validação de bloqueio por capacidades não autorizadas

- `tests/unit/runtime/test_agent_state.py`
  - Restauração de checkpoint e retry policy
  - Persistência de persona no checkpoint

- `tests/unit/orchestration/test_security_service.py`
  - Caminho de segurança via `ToolInvocationGateway`

### 6.2 Testes de integração relevantes

- `tests/integration/test_standalone_application.py`
  - Validação de criação de `AgentApplication`
  - Teste de workspace isolado
  - Teste novo de retomada de checkpoint com persona/capacidades restauradas

### 6.3 Resultado de execução

Foram executados com sucesso os seguintes grupos:

- `tests/unit/runtime/test_agent_state.py`
- `tests/unit/orchestration/test_security_service.py`
- `tests/unit/tools/test_invocation_gateway.py`
- `tests/unit/planning/test_plan_validator.py`
- `tests/integration/test_standalone_application.py -k "resume_restores_persona_and_capabilities or test_application_runs_trivial_task_with_explicit_workspace"`

Todos passaram.

---

## 7. Conclusão

### O que foi implementado

- Ciclo arquitetural standalone completo estabelecido.
- `AgentApplication` como root de composição e lifecycle.
- Registro dinâmico de ferramentas e gateway único de invocação.
- Autorização de ferramenta por persona e capacidades.
- Validação de planos antes da execução.
- Retomada de tarefas com preservação de persona/capabilities.

### O que ainda não é coberto por este documento

- A fase 5 do roadmap não tem implementação explícita no repositório atual.
- A integração TCC / segunda extensão externa permanece como próximo passo arquitetural.

---

## 8. Lista de arquivos mais relevantes alterados

- `agent/application.py`
- `agent/orchestrator.py`
- `agent/tool_executor.py`
- `agent/tools/invocation_gateway.py`
- `agent/tools/tool_registry.py`
- `agent/tools/builtin_adapter.py`
- `agent/tools/stdio_adapter.py`
- `agent/skills/policy.py`
- `agent/planning/execution_gateway.py`
- `agent/planning/plan_validator.py`
- `agent/planning/replan.py`
- `agent/state.py`
- `tests/unit/tools/test_invocation_gateway.py`
- `tests/unit/planning/test_plan_validator.py`
- `tests/unit/runtime/test_agent_state.py`
- `tests/unit/orchestration/test_security_service.py`
- `tests/integration/test_standalone_application.py`

---

## 9. Observações finais

A implementação foi feita para fechar o ciclo de um assistente local seguro e extensível, mantendo o modelo como uma interface substituível e colocando autorização/validação antes da execução.
O resultado é uma base capaz de operar como standalone e de suportar futuras extensões externas sem acoplamento direto ao repositório principal.

## 10. Snapshot histórico anterior à correção do manifest

Os números desta seção registram o snapshot anterior à correção da política de
campos desconhecidos e não representam o estado atual do baseline.

### Gates reproduzíveis

- `pytest -q`: **385 passed, 15 skipped**.
- `scripts/check_quality.py`: **passou** (`complexity_debt=0`, `oversized_modules=0`).
- `ruff check .`: **passou**.
- `mypy --platform linux`: **passou** em 196 arquivos.
- `mypy --platform win32`: **passou** em 196 arquivos.
- `scripts/verify_installed_package.py --no-build-isolation`: **passou**.
- `git diff --check`: **passou**; os avisos restantes são apenas normalização CRLF/LF do Git.

### Entregue neste snapshot

- uma única composição de `SkillRegistry` compartilhada pelo adapter builtin e
  pelo `Orchestrator`;
- status `blocked` e `unverified` preservados no contrato canônico;
- registro de tools determinístico, com colisões rejeitadas;
- registro de extensions com persistência atômica e caminho canônico usado pelo
  CLI e pelo bootstrap;
- autorização por contexto de invocação e aprovação de efeitos no gateway;
- manifest stdio validado, ambiente externo reduzido e limite de resposta;
- planner capaz de consultar descriptors do `ToolRegistry` para tools externas;
- replanejamento que não remove silenciosamente um passo bloqueado;
- `LegacyToolInvoker` isolado dos caminhos standalone;
- gates estáticos e de pacote instalável sem dívida nova.

### Limitações ainda explícitas

- cancelamento cooperativo de adapters in-process não é garantido pelo
  processo Python; extensões stdio têm timeout de subprocesso, mas não são uma
  sandbox de sistema operacional;
- respostas stdio sem `invocation_id`, com ID divergente/desconhecido ou com
  mais de uma resposta terminal são rejeitadas como erro de protocolo no
  protocolo atual `1.0`; não há fallback permissivo nem protocolo `1.1`;
- logs/eventos e o resultado persistido preservam o `invocation_id`; artifacts
  pertencem a um contrato separado e ainda não possuem envelope de correlação
  próprio;
- retry ainda não distingue invocação lógica de tentativa concreta (`attempt_id`);
- grants persistidos completos, retomada que reautoriza com política alterada,
  conformance comum de todos os adapters e probe de extension no wheel ainda
  precisam das tarefas pendentes do runbook;
- não foi adicionada dependência pesada ao runtime core e a integração do TCC
  continua externa e futura.

## 11. Atualização atual após a correção do manifest

Em 2026-08-01, a validação da política estrita de campos desconhecidos foi
executada novamente. O adapter rejeita campos não documentados no objeto raiz e
nas declarações de tools, preservando campos arbitrários dentro de `schema`.

- `tests/unit/tools/test_stdio_adapter.py`: **85 passed, 1 skipped**.
- launcher e extension de exemplo: **11 passed**.
- `pytest -q`: **479 passed, 16 skipped**.
- Ruff: **passou**.
- mypy Linux e Windows: **passou** em 201 arquivos.
- `scripts/check_quality.py`: **passou** (`complexity_debt=0`,
  `oversized_modules=0`).
- `scripts/verify_installed_package.py --no-build-isolation`: **passou**.
- `pip check`: **passou**, sem dependências quebradas.

O CI Windows, o CI Ubuntu com Python 3.10 e 3.12 e o acceptance executado no
ambiente do CI continuam pendentes. O status operacional de C20-02 permanece
`ABERTO — READY_FOR_CI`.
