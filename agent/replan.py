"""
Replanner: ajuste automático de planos quando uma ferramenta falha repetidamente.

Fase 4C, item 5 — Resiliência.

Fluxo:
    erro → classificar (ErrorCategory) → RetryPolicy autoriza?
    ├── sim → heurística determinística
    │   ├── resolveu → novo passo validado pelo executor
    │   └── não resolveu → LLM (se budget permitir)
    └── não → abortar

Heurísticas implementadas:
    - FileNotFoundError: grep pelo nome do arquivo → directory_lister no diretório pai
    - SandboxError: não há heurística segura → delega ao LLM

Política:
    MAX_TOTAL_REPLANS = 2
    MAX_HEURISTIC_REPLANS = 2
    MAX_LLM_REPLANS = 1
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from agent.plan_optimizer import PlanOptimizer
from agent.plan_validator import PlanValidator
from agent.tool_metadata import TOOL_METADATA
from logger import logger


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

class ErrorCategory(Enum):
    """Classificação determinística de erros para o replanejamento."""
    FILE_NOT_FOUND = "FileNotFoundError"
    SANDBOX = "SandboxError"
    SCHEMA = "SchemaError"
    TOOL_BLOCKED = "ToolBlocked"
    TIMEOUT = "TimeoutError"
    UNKNOWN = "Unknown"


@dataclass
class ReplanContext:
    """Estado completo do replanejamento, usado pela RetryPolicy."""
    task: str
    current_step: Dict[str, Any]
    tool_history: List[Dict[str, Any]]
    heuristic_replans: int = 0
    llm_replans: int = 0
    last_exception: Optional[str] = None
    last_tool_result: Optional[Dict[str, Any]] = None
    budget_remaining: Optional[int] = None


@dataclass
class ReplanAction:
    """Um ou mais passos gerados pelo replanejador."""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    source: str = ""  # "heuristic" | "llm"
    reason: str = ""


class RetryPolicy:
    """Define quantas tentativas de replanejamento são permitidas."""

    def __init__(self, max_total: int = 2, max_heuristic: int = 2, max_llm: int = 1):
        self.max_total = max_total
        self.max_heuristic = max_heuristic
        self.max_llm = max_llm

    def allows_heuristic(self, ctx: ReplanContext) -> bool:
        total = ctx.heuristic_replans + ctx.llm_replans
        return total < self.max_total and ctx.heuristic_replans < self.max_heuristic

    def allows_llm(self, ctx: ReplanContext) -> bool:
        total = ctx.heuristic_replans + ctx.llm_replans
        return total < self.max_total and ctx.llm_replans < self.max_llm


# ---------------------------------------------------------------------------
# Classificador de erros
# ---------------------------------------------------------------------------

def classify_error(error_message: str) -> ErrorCategory:
    """
    Classifica uma mensagem de erro em uma categoria.
    Baseado em substrings confiáveis — o sistema não tem acesso ao
    objeto de exceção original nesta camada.
    """
    msg = (error_message or "").lower()

    if "filenotfounderror" in msg or "arquivo não encontrado" in msg or "no such file" in msg:
        return ErrorCategory.FILE_NOT_FOUND
    if "sandbox" in msg or "fail-closed" in msg or "traversal" in msg or "absoluto" in msg:
        return ErrorCategory.SANDBOX
    if "schema" in msg or "campo obrigatório" in msg or "argumentos inválidos" in msg:
        return ErrorCategory.SCHEMA
    if "não permitida" in msg or "não está permitida" in msg:
        return ErrorCategory.TOOL_BLOCKED
    if "timeout" in msg or "excedeu" in msg:
        return ErrorCategory.TIMEOUT
    return ErrorCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Heurísticas determinísticas
# ---------------------------------------------------------------------------

def try_heuristic(category: ErrorCategory, tool: str, args: Dict[str, Any]) -> Optional[ReplanAction]:
    """
    Tenta corrigir o erro sem chamar o LLM.
    Retorna ReplanAction com um ou mais passos, ou None.
    """

    # --- FileNotFoundError: grep + directory_lister ---
    if category == ErrorCategory.FILE_NOT_FOUND:
        file_path = args.get("file_path") or args.get("target") or ""
        if not file_path:
            return None

        import os
        fname = os.path.basename(file_path)
        parent = os.path.dirname(file_path) or "."

        return ReplanAction(
            steps=[
                {"tool": "grep", "args": {"pattern": fname, "path": "."}},
                {"tool": "directory_lister", "args": {"path": parent}},
            ],
            source="heuristic",
            reason=f"FileNotFound: '{file_path}' — tentando localizar com grep + directory_lister.",
        )

    # --- Sandbox, Schema, ToolBlocked, Timeout, Unknown: sem heurística segura ---
    return None


# ---------------------------------------------------------------------------
# Último recurso: LLM
# ---------------------------------------------------------------------------

def ask_llm_for_alternative(
    original_step: Dict[str, Any],
    error_message: str,
    orchestrator: Any,
) -> Optional[ReplanAction]:
    """
    Pede ao LLM para sugerir um passo alternativo.
    Retorna ReplanAction se o LLM responder com um JSON válido, ou None.
    """
    if not hasattr(orchestrator, "context_manager"):
        return None

    prompt = (
        f"O passo '{original_step.get('tool')}' falhou com o erro:\n"
        f"{error_message}\n\n"
        "Sugira um passo alternativo para atingir o mesmo objetivo. "
        "Responda APENAS com um JSON: {\"tool\": \"...\", \"args\": {...}}"
    )

    try:
        decision = orchestrator.context_manager.ask_model(
            prompt,
            step_type="tool_decision",
            base_prompt=getattr(orchestrator, "_cached_base_prompt", None),
            log_metric_callback=orchestrator._log_metric if hasattr(orchestrator, "_log_metric") else None,
        )
        if not isinstance(decision, dict):
            return None
        if isinstance(decision, dict) and "tool" in decision:
            return ReplanAction(
                steps=[{"tool": decision["tool"], "args": decision.get("args", {})}],
                source="llm",
                reason=f"LLM sugeriu '{decision['tool']}' após erro: {error_message[:150]}",
            )
    except Exception as e:
        import traceback
        logger.warning(f"Replanner: falha ao consultar LLM: {e}\n{traceback.format_exc()}")

    return None


# ---------------------------------------------------------------------------
# Reaproveitamento de PlanValidator / PlanOptimizer para novos passos
# ---------------------------------------------------------------------------

def _validate_and_optimize_new_steps(action: Optional[ReplanAction], orchestrator: Any) -> Optional[ReplanAction]:
    """Reaplica o mesmo pipeline de diagnóstico/otimização usado para o
    plano original (PlanValidator -> PlanOptimizer -> PlanValidator) aos
    passos recém-sugeridos pelo Replanner (heurística ou LLM), antes de
    devolvê-los ao chamador (PlanExecutor ou Orchestrator).

    Passos individualmente bloqueados pelo PlanValidator são descartados
    (nunca é seguro injetar um passo com ferramenta inexistente/não
    permitida, schema inválido, ou que esvaziaria 'analysis_notes.md').
    Se nenhum passo sobreviver, retorna None — sinalizando ao chamador que
    esta tentativa de replanejamento falhou e a próxima estratégia (ou o
    abort) deve assumir.
    """
    if not action or not action.steps:
        return action

    skills = getattr(orchestrator, "skills", {}) or {}
    active_skills = getattr(orchestrator, "active_skills", []) or []
    validator = PlanValidator(skills, active_skills)

    pre_report = validator.validate(action.steps)
    for w in pre_report.warnings:
        logger.info(f"[VALIDATOR] (replan) {w}")
    for e in pre_report.errors:
        logger.warning(f"[VALIDATOR] (replan) {e}")
    for b in pre_report.blocked_steps:
        logger.warning(f"[VALIDATOR] (replan) Passo sugerido {b.index + 1} descartado: {b.reason}")

    blocked_indexes = {b.index for b in pre_report.blocked_steps}
    surviving_steps = [s for idx, s in enumerate(action.steps) if idx not in blocked_indexes]
    if not surviving_steps:
        logger.warning("[VALIDATOR] (replan) Todos os passos sugeridos pelo replanner foram bloqueados.")
        return None

    optimizer = PlanOptimizer(TOOL_METADATA)
    opt_report = optimizer.optimize(surviving_steps)
    if opt_report.changed:
        logger.info(
            f"[OPTIMIZER] (replan) custo {opt_report.cost_before} → {opt_report.cost_after}, "
            f"{len(opt_report.transformations)} otimização(ões), "
            f"{opt_report.removed_duplicates} duplicata(s) removida(s)."
        )

    post_report = validator.validate(opt_report.optimized_steps)
    for e in post_report.errors:
        logger.warning(f"[VALIDATOR] (replan, pós-otimização) {e}")
    post_blocked_indexes = {b.index for b in post_report.blocked_steps}
    for b in post_report.blocked_steps:
        logger.warning(f"[VALIDATOR] (replan, pós-otimização) Passo {b.index + 1} descartado: {b.reason}")

    final_steps = [s for idx, s in enumerate(opt_report.optimized_steps) if idx not in post_blocked_indexes]
    if not final_steps:
        return None

    action.steps = final_steps
    return action


# ---------------------------------------------------------------------------
# Ponto de entrada único
# ---------------------------------------------------------------------------

def replan(
    ctx: ReplanContext,
    error_message: str,
    orchestrator: Any,
    retry_policy: RetryPolicy = None,
) -> Optional[ReplanAction]:
    """
    Ponto de entrada do replanejamento.

    Fluxo:
        1. Classifica o erro.
        2. Consulta a RetryPolicy.
        3. Tenta heurística.
        4. Se falhar, tenta LLM.
        5. Registra o resultado no logger.
    """
    if retry_policy is None:
        retry_policy = RetryPolicy()

    category = classify_error(error_message)

    # Heurística
    if retry_policy.allows_heuristic(ctx):
        action = try_heuristic(category, ctx.current_step.get("tool", ""), ctx.current_step.get("args", {}))
        if action is not None:
            # Reaproveita PlanValidator + PlanOptimizer sobre os passos
            # sugeridos, seguindo o mesmo pipeline usado para o plano
            # original (ver Parte 5 da especificação).
            action = _validate_and_optimize_new_steps(action, orchestrator)
        if action is not None:
            ctx.heuristic_replans += 1
            logger.info(
                f"[REPLAN] step={len(ctx.tool_history)+1} "
                f"tool={ctx.current_step.get('tool')} "
                f"error={category.value} "
                f"strategy=heuristic "
                f"replacement={[s['tool'] for s in action.steps]}"
            )
            return action

    # LLM
    if retry_policy.allows_llm(ctx):
        action = ask_llm_for_alternative(ctx.current_step, error_message, orchestrator)
        if action is not None:
            action = _validate_and_optimize_new_steps(action, orchestrator)
        if action is not None:
            ctx.llm_replans += 1
            logger.info(
                f"[REPLAN] step={len(ctx.tool_history)+1} "
                f"tool={ctx.current_step.get('tool')} "
                f"error={category.value} "
                f"strategy=llm "
                f"replacement={[s['tool'] for s in action.steps]}"
            )
            return action

    logger.warning(
        f"[REPLAN] step={len(ctx.tool_history)+1} "
        f"tool={ctx.current_step.get('tool')} "
        f"error={category.value} "
        f"strategy=abort "
        f"reason=RetryPolicy esgotada ou nenhuma alternativa encontrada"
    )
    return None