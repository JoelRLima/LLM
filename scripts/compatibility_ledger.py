"""Explicit compatibility-surface dispositions.

The ledger is deliberately separate from the source checker.  It records why
an edge is still present (or why it was removed) and gives the open-world
checker a finite, reviewable authority for the compatibility markers that are
allowed to remain in production source.
"""

from __future__ import annotations

from dataclasses import dataclass

REMOVE = "REMOVE"
MIGRATE_THEN_REMOVE = "MIGRATE_THEN_REMOVE"
RETAIN_PERSISTENCE_CONTRACT = "RETAIN_PERSISTENCE_CONTRACT"
RECLASSIFY_CANONICAL = "RECLASSIFY_CANONICAL"
DEFER_TO_W8_WITH_BLOCKING_EVIDENCE = "DEFER_TO_W8_WITH_BLOCKING_EVIDENCE"

DISPOSITIONS = frozenset(
    {
        REMOVE,
        MIGRATE_THEN_REMOVE,
        RETAIN_PERSISTENCE_CONTRACT,
        RECLASSIFY_CANONICAL,
        DEFER_TO_W8_WITH_BLOCKING_EVIDENCE,
    }
)


@dataclass(frozen=True, slots=True)
class CompatibilityEdge:
    """One source/API or persisted-data compatibility edge."""

    edge_id: str
    path: str
    symbol: str
    disposition: str
    canonical_owner: str
    consumers: str
    durable_consumers: str
    reason: str
    retirement_condition: str

    @property
    def surface(self) -> str:
        return f"{self.path}::{self.symbol}"


def _edge(
    edge_id: str,
    path: str,
    symbol: str,
    disposition: str,
    canonical_owner: str,
    consumers: str,
    durable_consumers: str,
    reason: str,
    retirement_condition: str,
) -> CompatibilityEdge:
    return CompatibilityEdge(
        edge_id=edge_id,
        path=path,
        symbol=symbol,
        disposition=disposition,
        canonical_owner=canonical_owner,
        consumers=consumers,
        durable_consumers=durable_consumers,
        reason=reason,
        retirement_condition=retirement_condition,
    )


LEDGER = (
    # Explicitly removed by the current retirement candidate.
    _edge("W7-R01", "agent/planning/failure_policy.py", "<module>", REMOVE, "agent.runtime.failure_policy", "planning semantics", "none", "source reexport had no supported consumer", "already absent; no replacement facade"),
    _edge("W7-R02", "agent/planning/operational_constants.py", "<module>", REMOVE, "agent.runtime.outcome_taxonomy", "planning completion dispatch", "none", "source reexport had no supported consumer", "already absent; no replacement facade"),
    _edge("W7-R03", "agent/runtime/events.py", "from_legacy_fields", REMOVE, "RuntimeEvent", "checkpoint event operation migrated to canonical emitter", "none", "legacy field constructor was a source/API construction facade", "already absent; callers use canonical event owner"),
    _edge("W7-R04", "agent/planning/replan_compat.py", "<module>", REMOVE, "agent.planning.replan_models.ReplanContext", "repository callers migrated", "none", "legacy replan construction had no durable consumer", "already absent; no replacement facade"),
    _edge("W7-R05", "agent/state_plan.py", "canonicalize_plan_steps", REMOVE, "agent.planning.plan_model.Plan.from_raw", "checkpoint and executor callers migrated", "none", "list decoder wrapper duplicated typed Plan admission", "already absent; no replacement wrapper"),
    _edge("W7-R06", "agent/contracts.py", "ToolResult", REMOVE, "agent.tools.contracts.ToolResult", "runtime/test imports migrated", "none", "root source import hook was not a persistence boundary", "already absent; no replacement facade"),
    _edge("W7-R07", "agent/planning/task_resources.py", "ResourceClaim", REMOVE, "agent.planning.task_resources.ResourceAccess", "scheduler and tests migrated", "none", "type alias duplicated canonical resource authority", "already absent; no replacement alias"),
    _edge("W7-R08", "agent/planning/task_resources.py", "normalize_resource_name", REMOVE, "agent.planning.task_resources.normalize_resource_id", "scheduler and tests migrated", "none", "historical name normalization alias had no durable consumer", "already absent; no replacement alias"),
    _edge("W7-R09", "agent/llm/providers/factory.py", "resolve_model_profile", REMOVE, "agent.llm.model_profile.resolve_model_profile", "gateway callers migrated", "none", "provider-local resolver wrapper duplicated the canonical profile ingress", "already absent; direct canonical import only"),
    _edge("W7-R10", "agent/final_response.py", "summary/status aliases", REMOVE, "agent.final_response observation-evidence constants", "renderer and tests migrated", "none", "public aliases were source compatibility only", "already absent; canonical names only"),
    _edge("W7-R11", "agent/planning/reactive_loop.py", "_compatibility_decision", REMOVE, "typed ReactiveToolDecision or ReactiveFinalDecision", "reactive-loop tests migrated", "none", "private raw-dictionary admission duplicated the canonical typed decision boundary", "already absent; callers must pass an admitted decision"),
    _edge("W7-R12", "agent/reporting/observation_evidence.py", "PUBLIC_TOOL_ERROR_CODES", REMOVE, "agent.runtime.outcome_taxonomy.PUBLIC_ERROR_CODES", "observation evidence module migrated", "none", "reporting alias duplicated the canonical error-code set", "already absent; canonical name only"),
    _edge("W7-R13", "agent/reporting/observation_evidence.py", "PUBLIC_TOOL_STATUSES", REMOVE, "agent.runtime.outcome_taxonomy.PUBLIC_TERMINAL_STATUSES", "observation evidence module migrated", "none", "reporting alias duplicated the canonical status set", "already absent; canonical name only"),
    _edge("W7-R14", "agent/reporting/partial_response.py", "MAX_TOOL_RESULTS_SUMMARY_CHARS", REMOVE, "agent.reporting.observation_evidence.MAX_OBSERVATION_EVIDENCE_CHARS", "partial response module migrated", "none", "summary limit alias duplicated the canonical observation limit", "already absent; canonical name only"),
    _edge("W7-R15", "agent/reporting/partial_response.py", "MAX_TOOL_RESULT_SUMMARY_CHARS", REMOVE, "agent.reporting.observation_evidence.MAX_OBSERVATION_RECORD_CHARS", "partial response module migrated", "none", "record limit alias duplicated the canonical observation limit", "already absent; canonical name only"),
    _edge("W7-R16", "agent/planning/plan_model.py", "Plan(list) identity", REMOVE, "typed agent.planning.plan_model.Plan", "typed plan consumers migrated", "none", "live Plan no longer inherits list or exposes list identity", "already absent; explicit serialization only"),
    _edge("W7-R17", "agent/final_response.py", "MAX_TOOL_RESULTS_SUMMARY_CHARS", REMOVE, "agent.final_response observation-evidence constants", "final renderer callers migrated", "none", "summary limit alias was source compatibility only", "already absent; canonical name only"),
    _edge("W7-R18", "agent/final_response.py", "MAX_TOOL_RESULT_SUMMARY_CHARS", REMOVE, "agent.final_response observation-evidence constants", "final renderer callers migrated", "none", "record limit alias was source compatibility only", "already absent; canonical name only"),
    _edge("W7-R19", "agent/final_response.py", "PUBLIC_TOOL_ERROR_CODES", REMOVE, "agent.runtime.outcome_taxonomy.PUBLIC_ERROR_CODES", "final renderer callers migrated", "none", "error-code alias was source compatibility only", "already absent; canonical name only"),
    _edge("W7-R20", "agent/final_response.py", "PUBLIC_TOOL_STATUSES", REMOVE, "agent.runtime.outcome_taxonomy.PUBLIC_TERMINAL_STATUSES", "final renderer callers migrated", "none", "status alias was source compatibility only", "already absent; canonical name only"),
    _edge("W7-R21", "agent/resources/contracts.py", "ResourceTrust", REMOVE, "agent.resources.contracts.ResourceProvenance", "no repository consumer; canonical provenance name used", "none", "source alias duplicated the provenance enum vocabulary", "already absent; no replacement alias"),
    _edge("W7-R22", "agent/resources/contracts.py", "ResourceOrigin", REMOVE, "agent.resources.contracts.ResourceProvenance", "no repository consumer; canonical provenance name used", "none", "source alias duplicated the provenance enum vocabulary", "already absent; no replacement alias"),
    _edge("W7-R23", "agent/health/state_checks.py", "dynamic root config import", REMOVE, "agent.runtime.config.carregar_config", "health configuration check", "none", "dynamic import of deleted root config was source compatibility and bypassed the canonical config owner", "already absent; direct canonical import only"),
    _edge("W7-R24", "agent/cost_guard.py", "DEFAULT_MAX_* aliases", REMOVE, "agent.runtime.limits.runtime_limit_values/default_runtime_limit", "cost-guard tests and historical source imports", "none", "module-level limit constants duplicated the typed runtime-limit owner", "already absent; callers use canonical runtime-limit APIs"),
    _edge("W7-R25", "agent/watchdog.py", "DEFAULT_MAX_* aliases", REMOVE, "agent.runtime.limits.runtime_limit_values/default_runtime_limit", "watchdog tests and historical source imports", "none", "module-level watchdog constants duplicated the typed runtime-limit owner", "already absent; callers use canonical runtime-limit APIs"),
    _edge("W7-R26", "agent/orchestration/operations.py", "dispatcher-less/legacy event emission fallback", REMOVE, "RuntimeEventDispatcher", "checkpoint and route-operation doubles migrated to the canonical dispatcher", "none", "direct state-event emission bypassed the canonical dispatcher and its checkpoint observer", "already absent; operations require a callable canonical dispatcher"),
    _edge("W7-R27", "agent/orchestration/operations.py", "None checkpoint confirmation", REMOVE, "CheckpointManager.save -> True", "checkpoint save doubles migrated to explicit boolean confirmation", "none", "exception-free completion without True did not prove durable checkpoint persistence", "already absent; only literal True confirms persistence"),

    # Delimited persisted/checkpoint/model-shape readers and projections.
    _edge("W7-P01", "agent/contracts.py", "LegacyToolResult", RETAIN_PERSISTENCE_CONTRACT, "agent.tools.contracts.ToolResult", "checkpoint/history result adapters", "checkpoint v2 and historical tool entries", "serialized result shape remains readable at the persistence boundary", "remove after supported historical entries are migrated or expired"),
    _edge("W7-P02", "agent/contracts.py", "SerializedToolHistoryEntry", RETAIN_PERSISTENCE_CONTRACT, "agent.tools.contracts.ToolResult", "history readers", "historical tool history", "serialized history representation is not a live result API", "remove after historical history retention expires"),
    _edge("W7-P03", "agent/evaluation/evaluation_identity.py", "resume_compatible", RETAIN_PERSISTENCE_CONTRACT, "evaluation identity", "resume validation", "persisted evaluation campaign records", "resume compatibility is a persisted identity check", "remove after stored campaign versions are retired"),
    _edge("W7-P04", "agent/llm/admitted_decision_core.py", "_legacy", RETAIN_PERSISTENCE_CONTRACT, "typed admitted decisions", "decision serialization", "historical/model response envelopes", "recursive projection is used only to read/write an explicit response shape", "remove after response-envelope compatibility is retired"),
    _edge("W7-P05", "agent/llm/admitted_decision_variants.py", "LegacyModelDecision", RETAIN_PERSISTENCE_CONTRACT, "typed admitted decisions", "structured response admission", "historical/model response envelopes", "legacy decision variant is a bounded response representation", "remove after supported envelopes are retired"),
    _edge("W7-P06", "agent/llm/admitted_decision_variants.py", "ModelDecisionWithCompatibility", RETAIN_PERSISTENCE_CONTRACT, "typed admitted decisions", "structured response admission", "historical/model response envelopes", "typed response wrapper carries a bounded compatibility payload", "remove after supported envelopes are retired"),
    _edge("W7-P07", "agent/llm/admitted_decisions.py", "ask_model_decision_with_compatibility", RETAIN_PERSISTENCE_CONTRACT, "typed decision admission", "planning/replan model response boundaries", "historical/model response envelopes", "compatibility is constrained to response decoding, not request construction", "remove after all supported response envelopes are retired"),
    _edge("W7-P08", "agent/llm/admitted_decisions.py", "_freeze_compatibility_payload", RETAIN_PERSISTENCE_CONTRACT, "typed decision admission", "response projection", "historical/model response envelopes", "freezes the bounded response compatibility payload", "remove with its response boundary"),
    _edge("W7-P09", "agent/llm/decision_contract.py", "legacy_model_decision_compatibility", RETAIN_PERSISTENCE_CONTRACT, "typed decision admission", "structured response readers", "historical/model response envelopes", "translation is limited to an explicitly admitted model-response shape", "remove after response compatibility is retired"),
    _edge("W7-P10", "agent/llm/task_definition_decision_compat.py", "_compat_initial", RETAIN_PERSISTENCE_CONTRACT, "Task Definition decision admission", "task-definition response decoding", "historical response envelopes", "field conversion is bounded to the task-definition response boundary", "remove after old response envelopes are retired"),
    _edge("W7-P11", "agent/llm/task_definition_decision_compat.py", "_compat_effect", RETAIN_PERSISTENCE_CONTRACT, "Task Definition decision admission", "task-definition response decoding", "historical response envelopes", "field conversion is bounded to the task-definition response boundary", "remove after old response envelopes are retired"),
    _edge("W7-P12", "agent/llm/task_definition_decision_compat.py", "legacy_model_decision_compatibility", RETAIN_PERSISTENCE_CONTRACT, "Task Definition decision admission", "task-definition response decoding", "historical response envelopes", "translation is bounded to an explicit response boundary", "remove after old response envelopes are retired"),
    _edge("W7-P13", "agent/planning/observation_invalidation.py", "_can_have_legacy_mutated", RETAIN_PERSISTENCE_CONTRACT, "canonical observation/result contract", "legacy result observations", "historical result fixtures", "result-shape probing is kept at an observation compatibility boundary", "remove after historical result fixtures are retired"),
    _edge("W7-P14", "agent/planning/plan_builder_compat.py", "build_legacy_initial", RETAIN_PERSISTENCE_CONTRACT, "agent.planning.plan_model.Plan", "model response plan boundary", "historical/model plan responses", "legacy plan response decoding is explicit and bounded", "remove after supported model plan shapes are retired"),
    _edge("W7-P15", "agent/planning/plan_builder_compat.py", "build_legacy_continuation", RETAIN_PERSISTENCE_CONTRACT, "agent.planning.plan_model.Plan", "model response plan boundary", "historical/model plan responses", "legacy continuation response decoding is explicit and bounded", "remove after supported model plan shapes are retired"),
    _edge("W7-P16", "agent/planning/plan_builder_compat.py", "legacy_plan", RETAIN_PERSISTENCE_CONTRACT, "agent.planning.plan_model.Plan", "model response plan boundary", "historical/model plan responses", "legacy list-shaped plans are decoded only at the model boundary", "remove after supported model plan shapes are retired"),
    _edge("W7-P17", "agent/planning/plan_model.py", "from_legacy", RETAIN_PERSISTENCE_CONTRACT, "agent.planning.plan_model.Plan", "checkpoint/model readers", "checkpoint and historical plan data", "explicit reader for a persisted/list-shaped plan", "remove after supported old plan data is retired"),
    _edge("W7-P18", "agent/planning/plan_model.py", "to_legacy", RETAIN_PERSISTENCE_CONTRACT, "agent.planning.plan_model.Plan", "checkpoint/model projections", "checkpoint and historical plan data", "explicit projection at a persisted/model boundary", "remove after supported old plan data is retired"),
    _edge("W7-P19", "agent/planning/plan_optimizer.py", "_legacy_projection", RETAIN_PERSISTENCE_CONTRACT, "agent.planning.plan_model.Plan", "optimizer result projection", "historical/model plan consumers", "optimizer preserves an explicit list-shaped boundary without changing live ownership", "remove after old plan consumers are retired"),
    _edge("W7-P20", "agent/planning/task_completion.py", "_legacy_continuation_increment", RETAIN_PERSISTENCE_CONTRACT, "canonical recovery/replan state", "continuation state restoration", "historical checkpoint counters", "counter projection is used for checkpoint compatibility only", "remove after old checkpoint counters are retired"),
    _edge("W7-P21", "agent/planning/task_semantics.py", "from_legacy", RETAIN_PERSISTENCE_CONTRACT, "canonical TaskSemantics", "checkpoint restoration", "historical task-semantic checkpoints", "explicit task-semantic reader preserves one semantic owner", "remove after historical task-semantic checkpoints are retired"),
    _edge("W7-P22", "agent/planning/task_semantics_checkpoint_authority.py", "validate_trusted_nonproof_compatibility", RETAIN_PERSISTENCE_CONTRACT, "TaskSemantics authority", "checkpoint validation", "historical non-proof checkpoint fields", "validation is a fail-closed historical checkpoint boundary", "remove after those checkpoint versions are retired"),
    _edge("W7-P23", "agent/reporting/run_receipt_builder.py", "_legacy_outcome", RETAIN_PERSISTENCE_CONTRACT, "canonical run receipt", "receipt rendering", "historical receipt fields", "receipt projection is read-only and cannot establish terminal truth", "remove after historical receipt consumers are retired"),
    _edge("W7-P24", "agent/runtime/config_repository.py", "_remove_legacy_state_paths", RETAIN_PERSISTENCE_CONTRACT, "ConfigRepository", "configuration migration", "legacy configuration files", "migration removes obsolete persisted paths without becoming a live alias", "remove after supported configuration migration window closes"),
    _edge("W7-P25", "agent/runtime/events.py", "from_legacy", RETAIN_PERSISTENCE_CONTRACT, "RuntimeEvent", "event/checkpoint readers", "historical runtime event records", "explicit read-only event decoder", "remove after supported historical event records are retired"),
    _edge("W7-P26", "agent/runtime/events.py", "to_legacy_dict", RETAIN_PERSISTENCE_CONTRACT, "RuntimeEvent", "event/checkpoint projection", "historical event records", "explicit projection at the persistence boundary", "remove after old event records are retired"),
    _edge("W7-P27", "agent/runtime/recovery.py", "restore_legacy_projection", RETAIN_PERSISTENCE_CONTRACT, "RecoveryBudgetState", "checkpoint restoration", "historical recovery projections", "restores bounded historical counters into the single recovery owner", "remove after historical recovery projections are retired"),
    _edge("W7-P28", "agent/runtime/schema_validation.py", "_legacy_property", RETAIN_PERSISTENCE_CONTRACT, "schema validation", "historical argument schema readers", "persisted/model schemas", "legacy property shape is decoded only during schema validation", "remove after old schemas are retired"),
    _edge("W7-P29", "agent/runtime/state_migration.py", "migrate_legacy_state", RETAIN_PERSISTENCE_CONTRACT, "canonical AgentState", "maintenance migration command", "legacy runtime state on disk", "explicit non-destructive migration boundary", "remove after the supported migration window closes"),
    _edge("W7-P30", "agent/state_checkpoint.py", "_restore_legacy_semantics", RETAIN_PERSISTENCE_CONTRACT, "canonical TaskSemantics", "checkpoint restoration", "historical checkpoint semantics", "restores only unambiguous persisted fields", "remove after historical checkpoint versions are retired"),
    _edge("W7-P31", "agent/state_checkpoint_counters.py", "_validate_canonical_legacy_conflicts", RETAIN_PERSISTENCE_CONTRACT, "canonical recovery state", "checkpoint validation", "historical checkpoint counters", "conflict validation protects the canonical owner while reading old fields", "remove after old counters are retired"),
    _edge("W7-P32", "agent/state_checkpoint_history.py", "_rebuild_legacy_semantics", RETAIN_PERSISTENCE_CONTRACT, "canonical TaskSemantics", "history restoration", "historical checkpoint history", "rebuild is restricted to persisted history", "remove after historical history is retired"),
    _edge("W7-P33", "agent/tools/contracts.py", "to_legacy_dict", RETAIN_PERSISTENCE_CONTRACT, "agent.tools.contracts.ToolResult", "checkpoint/history projection", "serialized tool history", "explicit projection of canonical result data", "remove after old history is retired"),
    _edge("W7-P34", "agent/tools/contracts.py", "_compat_mapping", RETAIN_PERSISTENCE_CONTRACT, "agent.tools.contracts.ToolResult", "result/reporting boundary", "historical result mappings", "mapping projection is bounded to result compatibility data", "remove after supported mapping consumers are retired"),
    _edge("W7-P35", "agent/tools/extension_catalog_errors.py", "CatalogManifestIncompatibleError", RETAIN_PERSISTENCE_CONTRACT, "extension catalog validation", "catalog migration/validation", "persisted extension manifests", "incompatibility is a persisted manifest contract, not a source alias", "remove after old manifest versions are retired"),
    _edge("W7-P36", "agent/tools/extension_catalog_errors.py", "LegacyMigrationError", RETAIN_PERSISTENCE_CONTRACT, "extension catalog migration", "maintenance migration", "legacy extension catalogs", "migration failure classification is needed for supported old catalogs", "remove after old catalogs are retired"),
    _edge("W7-P37", "agent/tools/extension_catalog_migration.py", "_read_legacy", RETAIN_PERSISTENCE_CONTRACT, "extension catalog service", "catalog migration", "legacy extension catalogs", "read-only old catalog decoder", "remove after old catalogs are retired"),
    _edge("W7-P38", "agent/tools/extension_catalog_migration.py", "migrate_legacy", RETAIN_PERSISTENCE_CONTRACT, "extension catalog service", "catalog migration", "legacy extension catalogs", "explicit migration boundary", "remove after old catalogs are retired"),
    _edge("W7-P39", "agent/tools/extension_catalog_migration.py", "_LEGACY_ENTRY_FIELDS", RETAIN_PERSISTENCE_CONTRACT, "extension catalog service", "catalog migration", "legacy extension catalogs", "field allowlist constrains persisted migration", "remove after old catalogs are retired"),
    _edge("W7-P40", "agent/tools/result_adapter.py", "to_legacy_result", RETAIN_PERSISTENCE_CONTRACT, "agent.tools.contracts.ToolResult", "checkpoint/history and extension result boundaries", "serialized tool history", "explicit projection only where a legacy mapping is required", "remove after supported serialized consumers are retired"),
    _edge("W7-P41", "agent/tools/result_adapter.py", "from_legacy_result", RETAIN_PERSISTENCE_CONTRACT, "agent.tools.contracts.ToolResult", "checkpoint/history and extension result boundaries", "serialized tool history", "explicit decoder into the canonical live result", "remove after supported serialized consumers are retired"),
    _edge("W7-P42", "agent/tools/result_adapter.py", "ensure_canonical_result", RETAIN_PERSISTENCE_CONTRACT, "agent.tools.contracts.ToolResult", "checkpoint/history and extension result boundaries", "serialized tool history", "canonicalization is kept at a data boundary, not in live execution policy", "remove after supported serialized consumers are retired"),
    _edge("W7-P43", "agent/tools/result_completeness.py", "is_legacy_complete_result", RETAIN_PERSISTENCE_CONTRACT, "canonical result completeness", "historical result readers", "historical result fixtures", "explicit legacy result completeness probe", "remove after historical result fixtures are retired"),
    _edge("W7-P44", "agent/tools/result_completeness.py", "legacy_result_successful", RETAIN_PERSISTENCE_CONTRACT, "canonical result completeness", "historical result readers", "historical result fixtures", "explicit legacy result status probe", "remove after historical result fixtures are retired"),
    _edge("W7-P45", "agent/runtime/events.py", "serialize_runtime_event", RETAIN_PERSISTENCE_CONTRACT, "RuntimeEvent", "event/checkpoint projection", "historical event records", "explicit serialized event projection at the persistence boundary", "remove after old event records are retired"),
    _edge("W7-P46", "agent/runtime/events.py", "deserialize_runtime_event", RETAIN_PERSISTENCE_CONTRACT, "RuntimeEvent", "event/checkpoint readers", "historical event records", "explicit deserialization boundary into the typed event", "remove after old event records are retired"),

    # Canonical names whose historical wording is misleading but whose live
    # behavior is already the canonical owner.
    _edge("W7-C01", "agent/llm/providers/openai_compatible.py", "OpenAICompatibleGateway", RECLASSIFY_CANONICAL, "provider gateway contract", "provider router", "none", "compatible describes provider protocol behavior, not a source facade", "no retirement; canonical provider implementation"),
    _edge("W7-C02", "agent/tools/extension_path.py", "is_compatible_with", RECLASSIFY_CANONICAL, "extension path contract", "extension catalog service", "none", "compatibility is host/platform validation, not source/API compatibility", "no retirement; canonical capability check"),
    _edge("W7-C03", "agent/workspace.py", "lint_check", RECLASSIFY_CANONICAL, "project validation service", "workspace and orchestration validation", "none", "the adapter wording describes a live canonical validation operation", "no retirement; canonical validation owner"),
    _edge("W7-C04", "agent/reporting/task_report_rendering.py", "aggregate_metrics", RECLASSIFY_CANONICAL, "task report aggregation", "reporting callers", "none", "stable aggregation API over canonical report data", "no retirement; canonical reporting owner"),
    _edge("W7-C05", "agent/runtime/event_dispatch.py", "append_state_event", RECLASSIFY_CANONICAL, "state event sink", "runtime event dispatch", "checkpoint event storage", "explicit sink is the canonical state-event boundary despite historical wording", "no retirement; canonical event sink"),
    _edge("W7-C06", "agent/task_definition/models.py", "<module>", RECLASSIFY_CANONICAL, "Task Definition models", "task-definition runtime", "serialized task definitions", "stable aggregate/model API over canonical typed fields", "no retirement; canonical Task Definition owner"),
    _edge("W7-C07", "agent/code/changes.py", "<module>", RECLASSIFY_CANONICAL, "ChangeSetTransaction", "change planning and application", "change receipts", "stable aggregation API over canonical change transactions", "no retirement; canonical change owner"),
    _edge("W7-C08", "agent/orchestration/operations.py", "_emit_checkpoint_event", RECLASSIFY_CANONICAL, "canonical runtime event owner", "checkpoint operation", "checkpoint event history", "helper now delegates only to the canonical emitter and carries no legacy field construction", "no retirement; canonical operation helper"),
    _edge("W7-C09", "agent/skills/__init__.py", "load_all_skills", RECLASSIFY_CANONICAL, "SkillRegistry", "health and regression skill collection callers", "none", "stable ordered collection projection over the canonical registry, not a compatibility facade", "no retirement; canonical collection helper"),

    # Explicitly bounded edges requiring a later coordinated contract change.
    _edge("W7-W01", "agent/runtime/paths.py", "<module>", DEFER_TO_W8_WITH_BLOCKING_EVIDENCE, "WorkspacePaths", "broad process/runtime path consumers", "legacy runtime state locations", "removal requires broad path injection beyond the current scope", "W8 path-injection design and consumer migration"),
    _edge("W7-W01A", "agent/orchestrator.py", "resolve_user_path", DEFER_TO_W8_WITH_BLOCKING_EVIDENCE, "WorkspaceManager.resolve_path", "AgentSubsystems -> ToolExecutor/StepPolicies; direct lightweight orchestrator test doubles", "workspace-scoped file operations", "the no-WorkspacePaths fallback preserves broad construction compatibility and removing it requires coordinated path injection", "W8 path-injection design, constructor contract, and consumer migration"),
    _edge("W7-W02", "agent/runtime/event_dispatch.py", "LegacyEventSinkAdapter", DEFER_TO_W8_WITH_BLOCKING_EVIDENCE, "canonical runtime event sink", "external sinks and observability tests", "historical event projections", "external sink protocol coordination is not complete", "W8 sink contract migration with downstream evidence"),
    _edge("W7-W03", "agent/tools/builtin_adapter.py", "<module>", DEFER_TO_W8_WITH_BLOCKING_EVIDENCE, "BuiltinToolAdapter", "SkillRegistry extension boundary", "installed extension metadata", "external registry adapter still translates a supported extension boundary", "W8 extension registry contract migration"),
    _edge("W7-W04", "agent/skills/policy.py", "<module>", DEFER_TO_W8_WITH_BLOCKING_EVIDENCE, "skill policy contract", "extension descriptors and legacy step validation", "persisted skill descriptors", "external extension descriptors still require a coordinated policy contract", "W8 extension policy migration"),
    _edge("W7-W05", "agent/planning/reasoning_boundary.py", "_call_extension", DEFER_TO_W8_WITH_BLOCKING_EVIDENCE, "canonical extension seam", "installed/test extensions", "none", "old extension call signatures remain at a narrow external seam", "W8 extension signature migration and downstream evidence"),
    _edge("W7-W08", "agent/planning/plan_builder.py", "legacy_reviewer", DEFER_TO_W8_WITH_BLOCKING_EVIDENCE, "typed obligation reviewer", "older orchestrator/test doubles", "none", "reviewer signature migration requires coordinated extension/test-double changes", "W8 reviewer port migration"),
    _edge("W7-W09", "agent/planning/result_bindings.py", "_resolve_ordinal", RETAIN_PERSISTENCE_CONTRACT, "typed binding resolver", "model/checkpoint binding readers", "historical binding data", "raw reference resolution is restricted to a persisted/model binding boundary", "remove after old binding shapes are retired"),
    _edge("W7-W10", "agent/runtime/failure_policy.py", "failure_fact_for_result", RETAIN_PERSISTENCE_CONTRACT, "canonical FailureFact", "result/failure boundary", "historical result records", "one explicit legacy result shape is classified without inferring policy from text", "remove after historical result records are retired"),
)


LEDGER_BY_KEY = {(edge.path, edge.symbol): edge for edge in LEDGER}


def find_edge(path: str, symbol: str) -> CompatibilityEdge | None:
    """Return the exact ledger entry for a source marker, if present."""

    return LEDGER_BY_KEY.get((path, symbol))


def validate_ledger() -> list[str]:
    """Return deterministic structural errors in the authority ledger."""

    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    for edge in LEDGER:
        if edge.edge_id in seen_ids:
            errors.append(f"duplicate edge id: {edge.edge_id}")
        seen_ids.add(edge.edge_id)
        if (edge.path, edge.symbol) in seen_keys:
            errors.append(f"duplicate edge key: {edge.surface}")
        seen_keys.add((edge.path, edge.symbol))
        if edge.disposition not in DISPOSITIONS:
            errors.append(f"invalid disposition for {edge.surface}: {edge.disposition}")
        for field_name in ("canonical_owner", "consumers", "reason", "retirement_condition"):
            if not getattr(edge, field_name).strip():
                errors.append(f"empty {field_name} for {edge.surface}")
        if edge.disposition == DEFER_TO_W8_WITH_BLOCKING_EVIDENCE and "W8" not in edge.retirement_condition:
            errors.append(f"deferred edge lacks W8 condition: {edge.surface}")
    return errors


__all__ = [
    "CompatibilityEdge",
    "DEFER_TO_W8_WITH_BLOCKING_EVIDENCE",
    "DISPOSITIONS",
    "LEDGER",
    "LEDGER_BY_KEY",
    "MIGRATE_THEN_REMOVE",
    "RECLASSIFY_CANONICAL",
    "REMOVE",
    "RETAIN_PERSISTENCE_CONTRACT",
    "find_edge",
    "validate_ledger",
]
