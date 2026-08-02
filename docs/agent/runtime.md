# Módulo `agent/` — runtime

> Parte da documentação técnica do projeto. Veja o [índice](../README.md).

## Visão geral

O pacote `agent/runtime/` trata da infraestrutura de configuração, paths,
workspace e runtime do agente.

Ele assegura que o agente seja instalado e inicializado de forma hermética,
separando dados, estado, cache e logs por workspace e por instalação.

## AppPaths e WorkspacePaths

### `AppPaths`

- Implementado em `agent/runtime/paths.py`.
- Resolve os diretórios do agente com base em:
  - `LLM_AGENT_HOME`, quando fornecido.
  - `AGENT_RUNTIME_DIR` para compatibilidade legada.
  - variáveis XDG no Unix e `%APPDATA%`/`%LOCALAPPDATA%` no Windows.
- Não realiza efeitos colaterais no filesystem.
- Propriedades:
  - `config_dir`
  - `data_dir`
  - `state_dir`
  - `cache_dir`
  - `log_dir`
- Métodos:
  - `discover()` resolve o layout padrão.
  - `config_file` e `log_file` expõem os paths no filesystem.

### `WorkspacePaths`

- Representa os diretórios de um workspace específico.
- Separação clara entre dados, estado e cache do workspace.
- Propriedades relevantes:
  - `memory_file`
  - `memory_db_file`
  - `checkpoint_file`
  - `lock_file`
  - `metrics_file`
  - `reports_dir`
  - `artifacts_dir`
  - `restore_points_dir`
  - `chat_history_file`
  - `task_tracker_json`
  - `task_tracker_markdown`
  - `scratch_dir`
  - `benchmark_results_file`
- `ensure_directories()` cria os diretórios necessários após a validação.

## ConfigRepository

- Implementado em `agent/runtime/config_repository.py`.
- Carrega, inicializa e migra a configuração do agente.
- Lê, valida e mescla valores de:
  - defaults empacotados,
  - arquivo de configuração do usuário,
  - variáveis de ambiente allowlisted,
  - overrides explícitos da CLI.
- Respeita o versionamento de schema:
  - `schema_version` não pode ser sobrescrito pela CLI.
  - `load()` exige schema completo quando a configuração é resolvida.
  - `initialize()` cria o arquivo padrão se ele não existir.
  - `migrate()` copia legacy config sem modificar a origem.
- `ResolvedConfig` encapsula valores validados e fornece `to_dict()` e
  `to_legacy_dict()`.

## WorkspaceManager

- Implementado em `agent/workspace.py`.
- Garante isolamento seguro de paths dentro do workspace.
- Funções principais:
  - `resolve_path(file_path)` confina acessos ao workspace e bloqueia paths
    fora dele.
  - `create_restore_point(plan)` faz backup preventivo de arquivos alterados
    por ferramentas de escrita.
  - `rollback()` restaura arquivos de backup e remove arquivos recém-criados.
  - `show_diff(file_path, new_content)` exibe um diff das mudanças propostas.
  - `lint_check(file_path)` valida arquivos Python com `py_compile` e, se
    habilitado, `ruff`, `mypy` e `pytest`.
- Usa `validation_config` para controlar habilitação de validações e as ações
  de `fail_triggers_replan`.

## Invariantes

- O runtime não grava dados no pacote instalado.
- O workspace é explicitamente injetado e resolvido antes do bootstrap.
- O estado e os artefatos de cada workspace são isolados por ID estável.
- O arquivo de configuração é versionado e validado estritamente.
