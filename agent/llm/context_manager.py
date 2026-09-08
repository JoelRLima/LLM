import datetime as dt
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.error_handler import ErrorHandler
from agent.llm.admitted_decisions import AdmittedModelDecision, ModelDecisionValue
from agent.llm.context_manager_auxiliary import ContextAuxiliaryMixin
from agent.llm.context_model_call import run_model_call
from agent.llm.context_pressure import ContextPressureResult
from agent.llm.context_projection import (
    UNTRUSTED_MEMORY,
    UNTRUSTED_SESSION,
    ContextSourceRecord,
    ModelContextProjection,
    fixed_untrusted_data_policy,
    render_untrusted_context_envelope,
)
from agent.llm.context_views import (
    build_compact_view,
    discover_project_context,
    get_file_hints,
)
from agent.llm.decision_contract import ModelRequestContract
from agent.llm.grammars import AUTO_GRAMMAR, AutoGrammar
from agent.llm.prompts import AGENT_SYSTEM_PROMPT
from agent.llm.session import ChatSession
from agent.memory.prompt_context import (
    DEFAULT_MEMORY_PROMPT_BUDGET_TOKENS,
    build_memory_prompt_context,
)
from agent.memory.semantic_memory import SemanticMemory
from agent.runtime.event_dispatch import dispatch_runtime_event
from agent.runtime.events import RuntimeEvent
from agent.runtime.hardware import resolve_hardware_profile
from agent.runtime.logging import logger
from agent.runtime.recovery import RecoveryScope
from agent.state import AgentState

STEP_BUDGETS = {
    "plan": 4096,
    "final": 4096,
    "tool_decision": 2048,
    "tool_discovery": 1024,
}
DEFAULT_AGENT_MAX_TOKENS = 2048
class ContextManager(ContextAuxiliaryMixin):
    def __init__(
        self,
        session: ChatSession,
        agent_state: AgentState,
        verbose: bool = False,
        task_context_resolver: Any | None = None,
        workspace_root: str | Path = ".",
        metric_callback: Callable[[Dict[str, Any]], None] | None = None,
    ):
        self.session = session
        self.agent_state = agent_state
        self.verbose = verbose
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.task_context_resolver = task_context_resolver
        self._metric_callback = metric_callback
        self.hardware_profile = resolve_hardware_profile(self.session.config)
        self._cached_project_context: Optional[str] = None
        self._last_context_projection: ModelContextProjection | None = None
        self._last_context_pressure: ContextPressureResult | None = None
        self._last_context_metrics: tuple[dict[str, Any], ...] = ()
        self.semantic: SemanticMemory | None = None
        if bool(self.session.config.get("semantic_memory_enabled", False)):
            try:
                self.semantic = SemanticMemory(
                    self.agent_state.memory,
                    model_name=str(
                        self.session.config.get(
                            "semantic_memory_model", "all-MiniLM-L6-v2"
                        )
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "Busca semântica indisponível; usando hints determinísticos: %s",
                    type(exc).__name__,
                )
    def get_project_context(self) -> str:
        if self._cached_project_context is not None:
            return self._cached_project_context
        self._cached_project_context = discover_project_context(self.workspace_root)
        return self._cached_project_context
    def estimate_conversation_tokens(self) -> int:
        """Return a rough context-pressure estimate, not provider usage."""
        total_chars = sum(
            len(str(m.get("content", ""))) for m in self.session.messages
        )
        return total_chars // 4
    def maybe_compress_context(self) -> None:
        """Mark the old compatibility seam without mutating task context.

        W15 model-actionable calls are fitted request-locally by
        ``run_model_call``.  The legacy summary implementation remains
        available only to explicit compatibility callers of
        ``compress_conversation``; this task seam never invokes it.
        """

        return None
    def build_compact_view(self) -> List[Dict[str, Any]]:
        return build_compact_view(
            self.session.messages,
            self.agent_state.tool_history,
            self.agent_state.memory.state,
            workspace_root=self.workspace_root,
        )
    def get_file_hints(self, objective: str) -> str:
        return get_file_hints(objective, self.semantic, self.workspace_root)

    @property
    def last_context_projection(self) -> ModelContextProjection | None:
        return self._last_context_projection

    def record_context_projection(self, projection: ModelContextProjection) -> None:
        """Retain bounded projection metrics without logging auxiliary bytes."""

        self._last_context_projection = projection
        self._last_context_metrics = tuple(
            {
                "source_kind": record.source_kind,
                "included": record.included,
                "truncated": record.truncated,
                "freshness": record.freshness,
            }
            for record in projection.source_records
        )
        logger.debug(
            "W13 context projection: estimated_tokens=%s sources=%s optional_truncated=%s fit_proven=%s",
            projection.estimated_tokens,
            len(projection.source_records),
            projection.optional_truncated,
            projection.fit_proven,
        )

    @property
    def last_context_metrics(self) -> tuple[dict[str, Any], ...]:
        return self._last_context_metrics

    def record_context_pressure(self, result: ContextPressureResult) -> None:
        """Retain only bounded pressure metadata for reporting/inspection."""

        self._last_context_pressure = result
        payload = result.to_dict()
        self._emit_runtime_projection("context_pressure_decision", payload)
        if result.decision.value == "COMPACT":
            self._emit_runtime_projection("context_projection_compacted", payload)
        callback = self._metric_callback
        if callable(callback):
            try:
                callback({"metric_type": "context_pressure_events", **payload})
                callback(
                    {
                        "metric_type": (
                            "context_compact_requests"
                            if result.decision.value == "COMPACT"
                            else "context_full_requests"
                            if result.decision.value == "FULL"
                            else "context_mandatory_overflows"
                        ),
                        "reason_code": result.reason_code,
                    }
                )
            except Exception:
                pass

    @property
    def last_context_pressure(self) -> ContextPressureResult | None:
        return self._last_context_pressure

    def refresh_for_convergence(self) -> None:
        """Invalidate only derived context views for one plateau refresh."""

        self._cached_project_context = None
        self._last_context_metrics = ()
        self._emit_runtime_projection(
            "context_refresh",
            {"reason": "no_progress_plateau"},
        )

    def _emit_runtime_projection(self, event_type: str, data: Mapping[str, Any]) -> None:
        correlation = getattr(self.agent_state, "runtime_correlation", None)
        sink = getattr(self.session, "event_sink", None)
        if correlation is None or sink is None:
            return
        try:
            dispatch_runtime_event(
                sink,
                RuntimeEvent.from_fields(event_type, correlation, data),
            )
        except Exception:
            # Context projections are observers and cannot change task truth.
            return

    def build_trusted_task_context(self) -> str:
        reference = getattr(self.agent_state, 'task_definition_ref', None)
        resolver = self.task_context_resolver
        if reference is None or resolver is None:
            return ''
        materialization = resolver.resolve(reference)
        return str(materialization.trusted_text)
    def check_prompt_size(self, context_limit: int | None = None) -> None:
        effective_limit = (
            self.hardware_profile.context_limit
            if context_limit is None
            else context_limit
        )
        system_content = self.session.messages[0]["content"]
        estimated_tokens = len(system_content) // 4
        threshold = int(effective_limit * 0.8)
        pct = estimated_tokens / effective_limit * 100
        if self.verbose:
            print(
                f"📏 [AUDITORIA] Prefixo estimado: ~{estimated_tokens} tokens ({pct:.1f}% do limite de {effective_limit})"
            )
        if estimated_tokens > threshold:
            logger.warning(
                f"Prefixo grande: ~{estimated_tokens} tokens ({pct:.1f}%)"
            )
            if self.verbose:
                print(
                    "⚠️  Atenção: prefixo acima de 80%! Considere limpar memória ou reduzir histórico."
                )
    def count_tokens_text_estimate(self, text: str) -> Optional[int]:
        """Compatibility text estimate; never the exact count of a chat request."""
        try:
            count = self.session.gateway.count_tokens(text)
            return int(count) if count is not None else None
        except Exception as e:
            logger.warning(f"Não foi possível contar tokens pelo provider: {e}")
            return None
    def _authorize_structured_response_repair(self) -> bool:
        policy = getattr(self.session, "task_policy", None)
        if policy is not None:
            return bool(
                policy.authorize_recovery(
                    RecoveryScope.STRUCTURED_RESPONSE_REPAIRS
                ).allowed
            )
        budget = getattr(self.agent_state, "recovery_budget", None)
        if budget is None:
            return True
        return bool(budget.try_consume(RecoveryScope.STRUCTURED_RESPONSE_REPAIRS))
    def build_base_system_prompt(
        self, persona_prompt: str, tools_desc: str
    ) -> str:
        now_str = dt.datetime.now().strftime("%A, %d de %B de %Y %H:%M")
        datetime_context = f"\n\n[SISTEMA] Data e hora atual: {now_str}. Use esta informação para responder perguntas sobre datas."
        return (
            persona_prompt
            + "\n\n"
            + AGENT_SYSTEM_PROMPT.format(tools_description=tools_desc)
            + datetime_context
            + "\n\n"
            + fixed_untrusted_data_policy()
        )
    def build_context(self, objective: str = "") -> str:
        memory_budget = min(
            DEFAULT_MEMORY_PROMPT_BUDGET_TOKENS,
            max(0, self.hardware_profile.context_limit // 8),
        )
        memory_projection = build_memory_prompt_context(
            self.agent_state.memory.state,
            objective=objective,
            budget_tokens=memory_budget,
            workspace_root=self.workspace_root,
        )
        records: list[ContextSourceRecord] = []
        if self.agent_state.conversation_history:
            turns = self.agent_state.conversation_history[
                -self.agent_state.max_history_turns :
            ]
            records.append(
                ContextSourceRecord(
                    source_id="session:recent-history",
                    source_kind="session_history",
                    trust_class=UNTRUSTED_SESSION,
                    reason="bounded recent session data",
                    data={"turns": turns},
                )
            )
        if memory_projection:
            records.append(
                ContextSourceRecord(
                    source_id="memory:legacy-projection",
                    source_kind="memory_projection",
                    trust_class=UNTRUSTED_MEMORY,
                    reason="existing bounded memory owner projected as data",
                    data={"content": memory_projection},
                )
            )
        return render_untrusted_context_envelope(records) if records else ""

    def ask_model(
        self,
        prompt: str,
        step_type: str = "tool_decision",
        base_prompt: str | None = None,
        log_metric_callback: Callable[[Dict[str, Any]], None] | None = None,
        grammar: str | None | AutoGrammar = AUTO_GRAMMAR,
        request_contract: ModelRequestContract | str | None = None,
        typed: bool = False,
        include_task_definition: bool = True,
    ) -> Dict[str, Any] | ModelDecisionValue:
        """
        Prepara o contexto e resolve a decisão no ModelGateway canônico.
        Args:
            grammar: gramática GBNF a usar. Por padrão (AUTO_GRAMMAR), a
                gramática é escolhida automaticamente com base em
                `step_type`. Passe uma string para sobrescrever, ou None
                para desabilitar a gramática nesta chamada.
        """
        return run_model_call(
            self,
            prompt,
            step_type=step_type,
            base_prompt=base_prompt,
            log_metric_callback=log_metric_callback,
            grammar=grammar,
            request_contract=request_contract,
            typed=typed,
            include_task_definition=include_task_definition,
            step_budgets=STEP_BUDGETS,
            default_max_tokens=DEFAULT_AGENT_MAX_TOKENS,
        )
    def ask_model_typed(
        self,
        prompt: str,
        *,
        request_contract: ModelRequestContract | str,
        step_type: str | None = None,
        include_task_definition: bool = True,
        **kwargs: Any,
    ) -> ModelDecisionValue | None:
        """Return only a successful canonical typed decision projection."""

        effective_step_type = step_type or (
            request_contract.value
            if isinstance(request_contract, ModelRequestContract)
            else request_contract
        )
        result = self.ask_model(
            prompt,
            step_type=effective_step_type,
            request_contract=request_contract,
            typed=True,
            include_task_definition=include_task_definition,
            **kwargs,
        )
        return result if isinstance(result, AdmittedModelDecision) else None
    def purge_stale_context(self) -> None:
        ErrorHandler.purge_stale_context(self.session, self.verbose)
