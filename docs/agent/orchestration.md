# Módulo `agent/` — orchestration

> Parte da documentação técnica do projeto. Veja o [índice](../README.md).

## Visão geral

O pacote `agent/orchestration/` concentra a composição de runtime e o ciclo da
execução de uma tarefa sob a fachada do `Orchestrator`.

Ele mantém a orquestração fora dos caminhos de implementação de ferramentas,
sem criar um segundo ponto de entrada para o agente.

## Componentes principais

- `AgentSubsystems` (`agent/orchestration/subsystems.py`)
  - Constrói serviços sob demanda para manter o bootstrap leve.
  - Expõe `workspace`, `context_manager`, `auto_coder`, `reactive_loop`,
    `plan_builder`, `plan_executor`, `final_responder`, `tool_executor`,
    `watchdog` e `execution_gateway`.
  - Garante que cada serviço seja instanciado apenas uma vez por execução.

- `OrchestratorOperations` (`agent/orchestration/operations.py`)
  - Fornece portas de infraestrutura compartilhadas por planejamento,
    execução e composição.
  - Centraliza gravação e restauração de checkpoint, persistência de memória,
    geração de relatórios, emissão de eventos e métricas.
  - Também expõe métodos transversais como `_save_checkpoint()`,
    `_load_checkpoint()`, `_delete_checkpoint()`, `_emit()`, `_log_metric()` e
    `_generate_task_report()`.

- `TaskRunner` (`agent/orchestration/task_runner.py`)
  - Coordena o ciclo de vida de uma única tarefa.
  - Resolve o objetivo atual, detecta retomada via checkpoint e decide se deve
    tratar a solicitação como trivial, hierárquica, de segurança ou linear.
  - Executa o plano validado, atualiza o estado, persiste checkpoints e limpa o
    histórico de mensagens ao final.
  - Em caso de interrupção (`KeyboardInterrupt`), salva o checkpoint e retorna
    uma mensagem amigável.

- `SecurityAnalysisService` (`agent/orchestration/security_service.py`)
  - Trata objetivos de auditoria de segurança.
  - Invoca `code_analyzer` via `ToolInvocationGateway` quando disponível ou via
    skill legada quando não há gateway.
  - Consolida achados com o `security_scanner` e, em seguida, gera a resposta
    final para o usuário.

## Fluxo de execução

1. O `TaskRunner` resolve o objetivo e decide se retoma um checkpoint.
2. Se o objetivo for trivial, ele responde diretamente.
3. Para objetivos de segurança, delega ao `SecurityAnalysisService`.
4. Para objetivos complexos, tenta o caminho hierárquico.
5. Se o plano é gerado, ele é executado pelo `ExecutionGateway`.
6. Ao final, persiste memória, gera relatório opcional e limpa o estado do
   agente.

## Responsabilidades do pacote

- Isolar a composição de serviços do resto do domínio.
- Garantir que o ciclo de uma tarefa seja reproduzível e auditável.
- Fornecer pontos únicos de checkpoint e relatório.
- Manter a separação entre decisão de fluxo, execução de ferramentas e
  infraestrutura.
