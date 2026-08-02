# Módulo `agent/` — evaluation

> Parte da documentação técnica do projeto. Veja o [índice](../README.md).

## Visão geral

O pacote `agent/evaluation/` suporta cenários herméticos de avaliação de
capacidades.

Ele não faz parte do fluxo de execução do agente, mas fornece uma camada de
verificação externa que checa efeitos observáveis em workspaces isolados.

## Contratos

Implementados em `agent/evaluation/contracts.py`.

### `FileExpectation`

Define o estado esperado de um arquivo após a execução:

- `path`
- `exists`
- `exact_content`
- `contains`
- `not_contains`

### `ScenarioExpectation`

Expressa as expectativas de um cenário:

- `success`
- `files`
- `unchanged_files`
- `allowed_changed_files`
- `answer_contains`
- `answer_not_contains`
- `max_steps`

### `CapabilityScenario`

Define um cenário de avaliação hermético:

- `scenario_id`
- `capability`
- `objective`
- `initial_files`
- `expectation`
- `metadata`

### `ExecutionObservation`

Representa o resultado bruto de uma execução:

- `success`
- `answer`
- `steps`
- `diagnostics`
- `artifacts`
- `error`

### `EvaluationFailure` e `ScenarioReport`

- `EvaluationFailure` descreve uma falha objetiva.
- `ScenarioReport` agrega o resultado final, incluindo `passed`,
  `observation`, `failures` e `changed_files`.

## Executor de cenários

- `CapabilityEvaluator` em `agent/evaluation/runner.py` prepara um workspace
  limpo, escreve arquivos iniciais e executa o agente através de um adapter
  (`ScenarioExecutor`).
- O executor:
  1. cria o workspace vazio,
  2. escreve `initial_files`,
  3. captura um snapshot dos hashes antes da execução,
  4. chama `executor.execute(objective, workspace)`,
  5. captura o snapshot depois da execução,
  6. compara alterações de arquivos,
  7. verifica o resultado contra `ScenarioExpectation`.
- A verificação inclui:
  - `success` esperado,
  - presença/ausência de trechos na resposta,
  - existência e conteúdo de arquivos,
  - arquivos não alterados,
  - arquivos alterados permitidos.

## Segurança e hermeticidade

- O workspace de avaliação deve estar vazio antes de iniciar.
- Caminhos relativos são resolvidos de forma segura com `_safe_relative_path`.
- O snapshot usa SHA-256 para detectar alterações precisas no workspace.
