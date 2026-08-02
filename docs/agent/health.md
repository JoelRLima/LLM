# Módulo `agent/` — health

> Parte da documentação técnica do projeto. Veja o [índice](../README.md).

## Visão geral

O pacote `agent/health/` contém componentes de diagnóstico e verificações de
integridade do runtime.

Ele é usado pelo comando `llm-agent doctor` para avaliar se a instalação,
configuração, workspace e memória estão em condições de operação.

## Componentes principais

- `agent/health/core.py`
  - Define constantes e caminhos essenciais para saúde do projeto.
  - Lista chaves de configuração obrigatórias, seções esperadas de memória e
    skills essenciais.
  - Fornece `ensure_sys_path()` para tornar o repositório importável nas
    verificações legadas de health.

- `agent/health/standalone.py` e `agent/health/standalone_checks.py`
  - Implementam verificações read-only que não inicializam o Orchestrator.
  - Verificam:
    - esquema de configuração,
    - integridade da memória JSON/SQLite,
    - paths de aplicação,
    - permissões e disponibilidade de disk,
    - tamanho do estado,
    - saúde do workspace.

- `agent/health/state_integrity.py`
  - Contém checagens específicas de integridade de estado persistente.
  - Detecta corrupção, chaves ausentes e inconsistências estruturais.

## Propriedades do diagnóstico

- O diagnóstico é offline: não constrói modelo nem testa backends.
- O relatório pode ser gerado em JSON ou outro formato sem gravar nada,
  exceto quando `--write-report` é usado.
- Um workspace somente leitura pode ser considerado válido para análise, mas
  não para alterações.
- `doctor` distingue `read_write`, `read_only` e `unavailable`.

## Invariantes

- Verificações de saúde não devem executar efeitos.
- O comando `doctor` não deve alterar estado, exceto quando explicitamente
  solicitado por `--write-report`.
- O diagnóstico se concentra em integridade, configuração e compatibilidade,
  não em qualidade de modelo ou resultados de execução.
