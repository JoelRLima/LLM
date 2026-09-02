# Compatibilidade e retirada de legado

> **STATUS: CURRENT — WAVE 8 INVENTORY.** Este arquivo é a fotografia viva das
> compatibilidades que permanecem. Não é diário de Wave nem changelog.

O caminho instalado e suportado é `llm-agent`, apontando para
`agent.interfaces.cli.app`. Código novo usa as portas tipadas canônicas. As
fachadas abaixo foram removidas depois da migração dos consumidores internos.

## Removido

- `agent/planning/failure_policy.py` e `agent/planning/operational_constants.py`,
  que eram apenas reexportes de política/taxonomia; os consumidores usam os
  donos `agent.runtime.failure_policy` e `agent.runtime.outcome_taxonomy`.
- Os aliases de raiz `cli.py`, `cli_chat.py`, `cli_streaming.py`, `commands.py`,
  `command_handlers.py`, `command_ui.py`, `config.py`, `config_validation.py`,
  `logger.py`, `paths.py`, `session.py` e `benchmark.py`.
- `ModelClient`, `LegacyPayloadGateway`, `PendingStream`, `LegacySessionMixin`,
  os helpers de payload/sessão raw e a ponte `record_legacy_metadata`.
- `LegacyToolInvoker`, `AutoCoder`, a composição de compatibilidade e o reparo
  de plano em lista.
- `agent/planning/replan_compat.py`, incluindo `LegacyReplanContext`, `RetryPolicy`,
  `legacy_replan_context`, `legacy_replan_failure` e `ReplanContextCompat`.
  O caminho vivo usa somente `agent.planning.replan_models.ReplanContext` e
  `FailureFact` tipados.
- O alias de skill `git`; planos/checkpoints que o encontrem falham fechados
  com razão estável `W7_RETIRED_TOOL_ALIAS`.
- Os aliases de política `consumed_logical_steps` e `active_elapsed`; estados
  persistidos que os apresentem falham fechados com
  `W7_RETIRED_TASK_POLICY_KEY`.
- `TaskRuntimePolicy.admit()`; a admissão viva usa
  `TaskRuntimePolicy.admit_work_units()`.
- `ResourceClaim` e `normalize_resource_name` de
  `agent.planning.task_resources`; o scheduler usa `ResourceAccess` e a
  normalização canônica `normalize_resource_id`.
- `agent/state_plan.py::canonicalize_plan_steps`; os consumidores usam
  `Plan.from_raw(...)` e o método canônico de `StatePlanExecutionMixin`.
- A importação de fonte `agent.contracts.ToolResult` e seu hook
  `__getattr__`; resultados vivos usam `agent.tools.contracts.ToolResult`.
- `RuntimeEvent.from_legacy_fields`; eventos de checkpoint usam o dono de
  correlação canônico e a leitura persistida usa `RuntimeEvent.from_legacy`.
- Os reexports de exceções de `agent.llm.contracts` e os métodos de fonte
  `ConfigRepository.load_legacy()`/`ResolvedConfig.to_legacy_dict()`.
- `failure_fact_from_legacy_message`; conversores internos usam
  `FailureFact.unknown(...)` sem inferência de política a partir de texto.
- O import dinâmico do root `config` em `agent/health/state_checks.py`; a
  verificação usa diretamente `agent.runtime.config.carregar_config`.
- Os aliases `DEFAULT_MAX_TASK_STEPS`, `DEFAULT_MAX_TASK_TOKENS` e
  `DEFAULT_MAX_TASK_TOOL_CALLS` de `agent/cost_guard.py`, e
  `DEFAULT_MAX_TASK_WALL_SECONDS`, `DEFAULT_MAX_REPEATED_NO_PROGRESS` e
  `DEFAULT_MAX_CONSECUTIVE_SAME_ERROR` de `agent/watchdog.py`; os consumidores
  usam `agent.runtime.limits.runtime_limit_values`/`default_runtime_limit`.
- O fallback de emissão de eventos sem dispatcher em
  `agent/orchestration/operations.py`; operações usam o
  `RuntimeEventDispatcher` canônico e seu observer de checkpoint.
- A confirmação de checkpoint por retorno `None`; somente `True` explícito de
  `CheckpointManager.save(...)` confirma persistência.
- `LegacyEventSinkAdapter` e o surface
  `agent/runtime/event_dispatch.py::LegacyEventSinkAdapter` (`W7-W02`) foram
  removidos; sinks repository-controlled usam apenas `RuntimeEvent` pelo
  dispatcher canônico.
- `agent/planning/reasoning_boundary.py::_call_extension` (`W7-W05`) e
  `agent/planning/plan_builder.py::legacy_reviewer` (`W7-W08`) foram removidos.
  O seam vivo é `call_extension_boundary`, com assinatura suportada explícita,
  e o reviewer vivo exige o relatório tipado.

### Ledger exato de remoções Wave 7

Cada linha abaixo registra a superfície, consumidores migrados, consumidor
durável e condição de retirada. `REMOVE` significa que não há shim substituto.

- `W7-R01` | `agent/planning/failure_policy.py::<module>` | owner `agent.runtime.failure_policy` | consumers: planning semantics | durable: none | source reexport sem consumer suportado | condição: já ausente, sem facade.
- `W7-R02` | `agent/planning/operational_constants.py::<module>` | owner `agent.runtime.outcome_taxonomy` | consumers: planning completion dispatch | durable: none | source reexport sem consumer suportado | condição: já ausente, sem facade.
- `W7-R03` | `agent/runtime/events.py::from_legacy_fields` | owner `RuntimeEvent` | consumers: checkpoint event operation migrado para emitter canônico | durable: none | construtor de campos legado era facade de construção | condição: já ausente; callers usam o owner canônico.
- `W7-R04` | `agent/planning/replan_compat.py::<module>` | owner `agent.planning.replan_models.ReplanContext` | consumers: callers do replan migrados | durable: none | construção legada sem consumer durável | condição: já ausente, sem facade.
- `W7-R05` | `agent/state_plan.py::canonicalize_plan_steps` | owner `agent.planning.plan_model.Plan.from_raw` | consumers: checkpoint e executor migrados | durable: none | decoder de lista duplicava admissão de Plan | condição: já ausente, sem wrapper substituto.
- `W7-R06` | `agent/contracts.py::ToolResult` | owner `agent.tools.contracts.ToolResult` | consumers: imports runtime/test migrados | durable: none | hook de import de raiz era source compatibility | condição: já ausente, sem facade.
- `W7-R07` | `agent/planning/task_resources.py::ResourceClaim` | owner `agent.planning.task_resources.ResourceAccess` | consumers: scheduler e testes migrados | durable: none | alias duplicava a autoridade de recursos | condição: já ausente, sem alias.
- `W7-R08` | `agent/planning/task_resources.py::normalize_resource_name` | owner `agent.planning.task_resources.normalize_resource_id` | consumers: scheduler e testes migrados | durable: none | nome histórico sem consumer durável | condição: já ausente, sem alias.
- `W7-R09` | `agent/llm/providers/factory.py::resolve_model_profile` | owner `agent.llm.model_profile.resolve_model_profile` | consumers: gateway callers migrados | durable: none | resolver local duplicava a ingressão canônica | condição: já ausente; import direto canônico.
- `W7-R10` | `agent/final_response.py::summary/status aliases` | owner: observation-evidence constants | consumers: renderer e testes migrados | durable: none | aliases públicos eram source compatibility | condição: já ausente; somente nomes canônicos.
- `W7-R11` | `agent/planning/reactive_loop.py::_compatibility_decision` | owner: typed `ReactiveToolDecision`/`ReactiveFinalDecision` | consumers: testes migrados | durable: none | admissão de dicionário privado duplicava a fronteira tipada | condição: já ausente; caller deve fornecer decisão admitida.
- `W7-R12` | `agent/reporting/observation_evidence.py::PUBLIC_TOOL_ERROR_CODES` | owner: `agent.runtime.outcome_taxonomy.PUBLIC_ERROR_CODES` | consumers: módulo de evidence migrado | durable: none | alias duplicava o conjunto canônico de error codes | condição: já ausente; nome canônico.
- `W7-R13` | `agent/reporting/observation_evidence.py::PUBLIC_TOOL_STATUSES` | owner: `agent.runtime.outcome_taxonomy.PUBLIC_TERMINAL_STATUSES` | consumers: módulo de evidence migrado | durable: none | alias duplicava o conjunto canônico de status | condição: já ausente; nome canônico.
- `W7-R14` | `agent/reporting/partial_response.py::MAX_TOOL_RESULTS_SUMMARY_CHARS` | owner: `agent.reporting.observation_evidence.MAX_OBSERVATION_EVIDENCE_CHARS` | consumers: partial response migrado | durable: none | alias duplicava o limite canônico | condição: já ausente; nome canônico.
- `W7-R15` | `agent/reporting/partial_response.py::MAX_TOOL_RESULT_SUMMARY_CHARS` | owner: `agent.reporting.observation_evidence.MAX_OBSERVATION_RECORD_CHARS` | consumers: partial response migrado | durable: none | alias duplicava o limite canônico | condição: já ausente; nome canônico.
- `W7-R16` | `agent/planning/plan_model.py::Plan(list) identity` | owner: typed `agent.planning.plan_model.Plan` | consumers: typed plan callers | durable: none | Plan live não herda `list` nem expõe identidade de lista | condição: já ausente; serialização explícita.
- `W7-R17` | `agent/final_response.py::MAX_TOOL_RESULTS_SUMMARY_CHARS` | owner: observation-evidence limits | consumers: final renderer migrado | durable: none | alias de summary era source compatibility | condição: já ausente; nome canônico.
- `W7-R18` | `agent/final_response.py::MAX_TOOL_RESULT_SUMMARY_CHARS` | owner: observation-evidence limits | consumers: final renderer migrado | durable: none | alias de record era source compatibility | condição: já ausente; nome canônico.
- `W7-R19` | `agent/final_response.py::PUBLIC_TOOL_ERROR_CODES` | owner: `agent.runtime.outcome_taxonomy.PUBLIC_ERROR_CODES` | consumers: final renderer migrado | durable: none | alias de error code era source compatibility | condição: já ausente; nome canônico.
- `W7-R20` | `agent/final_response.py::PUBLIC_TOOL_STATUSES` | owner: `agent.runtime.outcome_taxonomy.PUBLIC_TERMINAL_STATUSES` | consumers: final renderer migrado | durable: none | alias de status era source compatibility | condição: já ausente; nome canônico.
- `W7-R21` | `agent/resources/contracts.py::ResourceTrust` | owner: `agent.resources.contracts.ResourceProvenance` | consumers: nenhum repository consumer; nome canônico usado | durable: none | alias duplicava o vocabulário de provenance | condição: já ausente; sem alias substituto.
- `W7-R22` | `agent/resources/contracts.py::ResourceOrigin` | owner: `agent.resources.contracts.ResourceProvenance` | consumers: nenhum repository consumer; nome canônico usado | durable: none | alias duplicava o vocabulário de provenance | condição: já ausente; sem alias substituto.

- `W7-R23` | `agent/health/state_checks.py::dynamic root config import` | owner `agent.runtime.config.carregar_config` | consumers: health configuration check | durable: none | import dinâmico do root removido após a retirada de `config.py` | condição: já ausente; import canônico direto.
- `W7-R24` | `agent/cost_guard.py::DEFAULT_MAX_* aliases` | owner `agent.runtime.limits.runtime_limit_values`/`default_runtime_limit` | consumers: testes e imports históricos | durable: none | constantes de módulo duplicavam o owner tipado de limites | condição: já ausente; APIs canônicas.
- `W7-R25` | `agent/watchdog.py::DEFAULT_MAX_* aliases` | owner `agent.runtime.limits.runtime_limit_values`/`default_runtime_limit` | consumers: testes e imports históricos | durable: none | constantes de módulo duplicavam o owner tipado de limites | condição: já ausente; APIs canônicas.
- `W7-R26` | `agent/orchestration/operations.py::dispatcher-less/legacy event emission fallback` | owner `RuntimeEventDispatcher` | consumers: doubles de checkpoint e rotas migrados para o dispatcher canônico | durable: none | emissão direta no estado bypassava o dispatcher e seu observer de checkpoint | condição: já ausente; dispatcher canônico obrigatório.
- `W7-R27` | `agent/orchestration/operations.py::None checkpoint confirmation` | owner `CheckpointManager.save -> True` | consumers: doubles de checkpoint migrados para confirmação booleana explícita | durable: none | conclusão sem `True` não prova persistência durável | condição: já ausente; somente `True` literal confirma.

## Retido como contrato de persistência ou leitura limitada

| Superfície | Dono atual | Limite |
| :--- | :--- | :--- |
| checkpoint v2, `CheckpointManager` e serialização tipada de `Plan` | estado/checkpoint canônico | leitores validam a forma; não há dupla escrita nem interpretação silenciosa |
| `model_metadata` histórico | leitores de métricas/relatórios | somente leitura; o writer atual é `model_call` |
| `legacy_model_decision_compatibility` | admissão de resposta estruturada | somente leitura de envelopes de resposta delimitados; não é API de request |
| `LegacyToolResult`, `SerializedToolHistoryEntry` | checkpoint v2 e histórico de ferramentas | `RETAIN_PERSISTENCE_CONTRACT`: projeção serializada somente nas fronteiras de checkpoint/histórico; o runtime usa `ToolResult` tipado |
| `agent/tools/result_adapter.py`: `to_legacy_result`, `from_legacy_result`, `ensure_canonical_result` | `ToolResult` tipado, `AgentState` e restauração de checkpoint/histórico | `RETAIN_PERSISTENCE_CONTRACT`: conversão delimitada de dados persistidos ou resultados de extensões; não contém política de recuperação |
| `Plan.from_legacy`, `Plan.to_legacy`, `deserialize_plan`, `serialize_plan` | `Plan` tipado | `RETAIN_PERSISTENCE_CONTRACT`: representação de lista permanece somente na fronteira durável/modelo; escrita canônica não cria outro dono |
| `RuntimeEvent.from_legacy`, `RuntimeEvent.to_legacy_dict`, `serialize_runtime_event` | `RuntimeEvent` e dispatch/checkpoint | `RETAIN_PERSISTENCE_CONTRACT`: leitura/projeção delimitada para eventos históricos; eventos vivos são tipados |
| `TaskSemantics.from_legacy` e restauração de semântica histórica | `TaskSemantics` | `RETAIN_PERSISTENCE_CONTRACT`: migração/reconstrução explícita de checkpoints antigos; sem reinterpretação silenciosa |
| `model_profile_compat` e campos de perfil flat históricos | `ResolvedModelProfile` | `RETAIN_PERSISTENCE_CONTRACT`: leitura de configurações/profile data legados; escritores atuais usam o modelo tipado |
| projeções de efeitos `requested_effects`, `executed_effects`, `waived_effects` e `prohibited_effects` | `TaskSemantics` e restauração de estado | `RETAIN_PERSISTENCE_CONTRACT`: compatibilidade de estado/checkpoint com dono semântico único |

### Ledger exato de contratos de persistência/leitura

Todas as linhas são `RETAIN_PERSISTENCE_CONTRACT`: o owner canônico continua
único no runtime; a borda só permanece para dados serializados, históricos,
model-shapes ou migração explícita. A condição indicada é a retirada do
formato antigo, não a criação de uma nova API de fonte.

- `W7-P01` | `agent/contracts.py::LegacyToolResult` | owner `agent.tools.contracts.ToolResult` | consumers: adapters de checkpoint/history | durable: checkpoint v2 e entries históricas | condição: expirar/migrar entries suportadas.
- `W7-P02` | `agent/contracts.py::SerializedToolHistoryEntry` | owner `ToolResult` canônico | consumers: readers de history | durable: tool history histórica | condição: expirar retenção histórica.
- `W7-P03` | `agent/evaluation/evaluation_identity.py::resume_compatible` | owner: evaluation identity | consumers: resume validation | durable: campaign records | condição: aposentar versões armazenadas.
- `W7-P04` | `agent/llm/admitted_decision_core.py::_legacy` | owner: typed admitted decisions | consumers: serialização de decisões | durable: response envelopes históricos/model | condição: aposentar envelopes suportados.
- `W7-P05` | `agent/llm/admitted_decision_variants.py::LegacyModelDecision` | owner: typed admitted decisions | consumers: structured response admission | durable: response envelopes | condição: aposentar envelopes suportados.
- `W7-P06` | `agent/llm/admitted_decision_variants.py::ModelDecisionWithCompatibility` | owner: typed admitted decisions | consumers: structured response admission | durable: response envelopes | condição: aposentar envelopes suportados.
- `W7-P07` | `agent/llm/admitted_decisions.py::ask_model_decision_with_compatibility` | owner: typed decision admission | consumers: planning/replan response boundaries | durable: response envelopes | condição: aposentar envelopes suportados.
- `W7-P08` | `agent/llm/admitted_decisions.py::_freeze_compatibility_payload` | owner: typed decision admission | consumers: response projection | durable: response envelopes | condição: remover com a borda de response.
- `W7-P09` | `agent/llm/decision_contract.py::legacy_model_decision_compatibility` | owner: typed decision admission | consumers: structured response readers | durable: response envelopes | condição: aposentar response compatibility.
- `W7-P10` | `agent/llm/task_definition_decision_compat.py::_compat_initial` | owner: Task Definition decision admission | consumers: task-definition response decoding | durable: response envelopes | condição: aposentar envelopes antigos.
- `W7-P11` | `agent/llm/task_definition_decision_compat.py::_compat_effect` | owner: Task Definition decision admission | consumers: task-definition response decoding | durable: response envelopes | condição: aposentar envelopes antigos.
- `W7-P12` | `agent/llm/task_definition_decision_compat.py::legacy_model_decision_compatibility` | owner: Task Definition decision admission | consumers: response decoding | durable: response envelopes | condição: aposentar envelopes antigos.
- `W7-P13` | `agent/planning/observation_invalidation.py::_can_have_legacy_mutated` | owner: canonical observation/result contract | consumers: legacy result observations | durable: result fixtures | condição: aposentar fixtures históricas.
- `W7-P14` | `agent/planning/plan_builder_compat.py::build_legacy_initial` | owner: typed `Plan` | consumers: model response plan boundary | durable: model plan responses | condição: aposentar model shapes suportados.
- `W7-P15` | `agent/planning/plan_builder_compat.py::build_legacy_continuation` | owner: typed `Plan` | consumers: model response plan boundary | durable: model plan responses | condição: aposentar model shapes suportados.
- `W7-P16` | `agent/planning/plan_builder_compat.py::legacy_plan` | owner: typed `Plan` | consumers: model response plan boundary | durable: list-shaped plans | condição: aposentar model shapes suportados.
- `W7-P17` | `agent/planning/plan_model.py::from_legacy` | owner: typed `Plan` | consumers: checkpoint/model readers | durable: plan data | condição: aposentar dados antigos suportados.
- `W7-P18` | `agent/planning/plan_model.py::to_legacy` | owner: typed `Plan` | consumers: checkpoint/model projections | durable: plan data | condição: aposentar dados antigos suportados.
- `W7-P19` | `agent/planning/plan_optimizer.py::_legacy_projection` | owner: typed `Plan` | consumers: optimizer projection | durable: model plan consumers | condição: aposentar consumers de lista.
- `W7-P20` | `agent/planning/task_completion.py::_legacy_continuation_increment` | owner: canonical recovery/replan state | consumers: continuation restore | durable: checkpoint counters | condição: aposentar counters antigos.
- `W7-P21` | `agent/planning/task_semantics.py::from_legacy` | owner: canonical `TaskSemantics` | consumers: checkpoint restoration | durable: semantic checkpoints | condição: aposentar checkpoints históricos.
- `W7-P22` | `agent/planning/task_semantics_checkpoint_authority.py::validate_trusted_nonproof_compatibility` | owner: `TaskSemantics` authority | consumers: checkpoint validation | durable: non-proof checkpoint fields | condição: aposentar versões de checkpoint.
- `W7-P23` | `agent/reporting/run_receipt_builder.py::_legacy_outcome` | owner: canonical run receipt | consumers: receipt rendering | durable: historical receipt fields | condição: aposentar consumers históricos.
- `W7-P24` | `agent/runtime/config_repository.py::_remove_legacy_state_paths` | owner: `ConfigRepository` | consumers: configuration migration | durable: legacy config files | condição: fechar janela de migração.
- `W7-P25` | `agent/runtime/events.py::from_legacy` | owner: `RuntimeEvent` | consumers: event/checkpoint readers | durable: historical event records | condição: aposentar records suportados.
- `W7-P26` | `agent/runtime/events.py::to_legacy_dict` | owner: `RuntimeEvent` | consumers: event/checkpoint projection | durable: historical event records | condição: aposentar records antigos.
- `W7-P27` | `agent/runtime/recovery.py::restore_legacy_projection` | owner: `RecoveryBudgetState` | consumers: checkpoint restoration | durable: historical recovery projections | condição: aposentar projections históricas.
- `W7-P28` | `agent/runtime/schema_validation.py::_legacy_property` | owner: schema validation | consumers: schema readers | durable: persisted/model schemas | condição: aposentar schemas antigos.
- `W7-P29` | `agent/runtime/state_migration.py::migrate_legacy_state` | owner: canonical `AgentState` | consumers: maintenance migration | durable: legacy runtime state | condição: fechar janela de migração.
- `W7-P30` | `agent/state_checkpoint.py::_restore_legacy_semantics` | owner: canonical `TaskSemantics` | consumers: checkpoint restoration | durable: historical semantics | condição: aposentar checkpoint versions.
- `W7-P31` | `agent/state_checkpoint_counters.py::_validate_canonical_legacy_conflicts` | owner: canonical recovery state | consumers: checkpoint validation | durable: checkpoint counters | condição: aposentar counters antigos.
- `W7-P32` | `agent/state_checkpoint_history.py::_rebuild_legacy_semantics` | owner: canonical `TaskSemantics` | consumers: history restoration | durable: checkpoint history | condição: aposentar history antigo.
- `W7-P33` | `agent/tools/contracts.py::to_legacy_dict` | owner: canonical `ToolResult` | consumers: checkpoint/history projection | durable: serialized tool history | condição: aposentar history antigo.
- `W7-P34` | `agent/tools/contracts.py::_compat_mapping` | owner: canonical `ToolResult` | consumers: result/reporting boundary | durable: historical result mappings | condição: aposentar mapping consumers.
- `W7-P35` | `agent/tools/extension_catalog_errors.py::CatalogManifestIncompatibleError` | owner: extension catalog validation | consumers: catalog migration/validation | durable: persisted manifests | condição: aposentar manifest versions.
- `W7-P36` | `agent/tools/extension_catalog_errors.py::LegacyMigrationError` | owner: extension catalog migration | consumers: maintenance migration | durable: legacy catalogs | condição: aposentar catalogs antigos.
- `W7-P37` | `agent/tools/extension_catalog_migration.py::_read_legacy` | owner: extension catalog service | consumers: catalog migration | durable: legacy catalogs | condição: aposentar catalogs antigos.
- `W7-P38` | `agent/tools/extension_catalog_migration.py::migrate_legacy` | owner: extension catalog service | consumers: catalog migration | durable: legacy catalogs | condição: aposentar catalogs antigos.
- `W7-P39` | `agent/tools/extension_catalog_migration.py::_LEGACY_ENTRY_FIELDS` | owner: extension catalog service | consumers: catalog migration | durable: legacy catalogs | condição: aposentar catalogs antigos.
- `W7-P40` | `agent/tools/result_adapter.py::to_legacy_result` | owner: canonical `ToolResult` | consumers: checkpoint/history and extension boundaries | durable: serialized history | condição: aposentar serialized consumers.
- `W7-P41` | `agent/tools/result_adapter.py::from_legacy_result` | owner: canonical `ToolResult` | consumers: checkpoint/history and extension boundaries | durable: serialized history | condição: aposentar serialized consumers.
- `W7-P42` | `agent/tools/result_adapter.py::ensure_canonical_result` | owner: canonical `ToolResult` | consumers: checkpoint/history and extension boundaries | durable: serialized history | condição: aposentar serialized consumers.
- `W7-P43` | `agent/tools/result_completeness.py::is_legacy_complete_result` | owner: canonical result completeness | consumers: historical result readers | durable: result fixtures | condição: aposentar fixtures.
- `W7-P44` | `agent/tools/result_completeness.py::legacy_result_successful` | owner: canonical result completeness | consumers: historical result readers | durable: result fixtures | condição: aposentar fixtures.
- `W7-P45` | `agent/runtime/events.py::serialize_runtime_event` | owner: `RuntimeEvent` | consumers: event/checkpoint projection | durable: historical event records | condição: aposentar records antigos.
- `W7-P46` | `agent/runtime/events.py::deserialize_runtime_event` | owner: `RuntimeEvent` | consumers: event/checkpoint readers | durable: historical event records | condição: aposentar records antigos.
- `W7-W09` | `agent/planning/result_bindings.py::_resolve_ordinal` | owner: typed binding resolver | consumers: model/checkpoint binding readers | durable: historical binding data | condição: aposentar binding shapes.
- `W7-W10` | `agent/runtime/failure_policy.py::failure_fact_for_result` | owner: canonical `FailureFact` | consumers: result/failure boundary | durable: historical result records | condição: aposentar records históricos.

## Reclassificado como canônico

- `ModelGateway`, `ModelRequest`, `ModelResponse`, `StreamEvent` e o ciclo
  `ChatSession.build_request` → `complete_request`/`consume_stream_request`.
- `ToolInvocationGateway`, `BuiltinToolAdapter`, `file_writer` administrativo
  e `ChangeSetTransaction`; nenhuma dessas superfícies é um bypass
  model-actionable.
- `OpenAICompatibleGateway.build_payload`, como serialização do provider e
  suporte à medição de entrada.
- `compress_conversation` e `load_all_skills`, como comportamento vivo sob
  seus donos canônicos.
- `agent/code/changes.py` e `agent/task_definition/models.py`, como
  agregações públicas estáveis sobre os donos canônicos.
- `StatePlanExecutionMixin.canonicalize_plan_steps`, `ResourceAccess`,
  `normalize_resource_id`, `TaskRuntimePolicy.admit_work_units()` e
  `agent.runtime.context_results.TaskResult`, como contratos canônicos vivos.
- `ConfigRepository.migrate()`, como operação administrativa explícita de
  migração de configuração; a leitura normal usa `ConfigRepository.load()`.
- `agent/runtime/paths.py::<module>` (`W7-W01`) permanece como owner explícito
  de caminhos process-level; consumidores produtivos recebem `WorkspacePaths`.
- `agent/orchestrator.py::resolve_user_path` (`W7-W01A`) delega sempre ao
  `WorkspaceManager.resolve_path`.
- `agent/tools/builtin_adapter.py::<module>` (`W7-W03`) é a fronteira canônica
  explícita entre `SkillRegistry` e `ToolResult`.
- `agent/skills/policy.py::<module>` (`W7-W04`) projeta descritores admitidos
  para o planejamento e o bootstrap.

### Ledger exato de reclassificações canônicas

Estas superfícies permanecem como comportamento vivo do owner indicado. O
marcador histórico descreve protocolo, plataforma ou redação anterior; não é
uma facade de source/API.

- `W7-C01` | `agent/llm/providers/openai_compatible.py::OpenAICompatibleGateway` | owner: provider gateway contract | consumers: provider router | durable: none | motivo: compatible descreve protocolo do provider | condição: não retirar; implementação canônica.
- `W7-C02` | `agent/tools/extension_path.py::is_compatible_with` | owner: extension path contract | consumers: extension catalog service | durable: none | motivo: validação host/platform, não source compatibility | condição: não retirar; check canônico.
- `W7-C03` | `agent/workspace.py::lint_check` | owner: project validation service | consumers: workspace/orchestration validation | durable: none | motivo: operação viva de validação canônica | condição: não retirar; owner canônico.
- `W7-C04` | `agent/reporting/task_report_rendering.py::aggregate_metrics` | owner: task report aggregation | consumers: reporting callers | durable: none | motivo: API estável de agregação | condição: não retirar; owner canônico.
- `W7-C05` | `agent/runtime/event_dispatch.py::append_state_event` | owner: state event sink | consumers: runtime event dispatch | durable: checkpoint event storage | motivo: boundary canônica de eventos apesar da redação histórica | condição: não retirar; sink canônico.
- `W7-C06` | `agent/task_definition/models.py::<module>` | owner: Task Definition models | consumers: task-definition runtime | durable: serialized task definitions | motivo: aggregate/model API tipada estável | condição: não retirar; owner canônico.
- `W7-C07` | `agent/code/changes.py::<module>` | owner: `ChangeSetTransaction` | consumers: change planning/application | durable: change receipts | motivo: aggregate API sobre transactions canônicas | condição: não retirar; owner canônico.
- `W7-C08` | `agent/orchestration/operations.py::_emit_checkpoint_event` | owner: canonical runtime event owner | consumers: checkpoint operation | durable: checkpoint event history | motivo: helper delega somente ao emitter canônico, sem construção legacy | condição: não retirar; helper canônico.
- `W7-C09` | `agent/skills/__init__.py::load_all_skills` | owner: `SkillRegistry` | consumers: health e regression skill collection callers | durable: none | motivo: projeção ordenada estável do registry canônico, não facade | condição: não retirar; helper de coleção canônico.

## Retido como compatibilidade de import de pacote

- `W8-PATH-01` | `agent/code/path_safety.py::<module>` permanece como uma
  facade estreita de compatibilidade para o submódulo historicamente incluído
  no pacote. Ela apenas reexporta `agent.runtime.path_safety`; código produtivo
  controlado pelo repositório importa diretamente o owner runtime. A facade
  deve ser retirada somente após a janela de compatibilidade e os imports
  downstream suportados serem aposentados.

## Adiado para W8 com evidência bloqueante (histórico; disposições encerradas)

Nenhuma compatibilidade de código produtivo permanece adiada após a Wave 8. A
fronteira `legacy_stdio_compatibility` permanece explicitamente suportada e é
uma boundary delimitada de transporte/dados, não um owner alternativo de
execução. O estado arquitetural consolidado está em
[`pre-v1-freeze.md`](pre-v1-freeze.md).

## Contexto documental histórico

Referências históricas em outros documentos permanentes preservam a decisão e
o contexto da época com notas de supersessão; não são instruções operacionais
atuais. Não há compatibilidade removida mantida por um novo alias.
