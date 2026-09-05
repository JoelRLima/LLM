"""AST/source gates for the Wave 12 interaction-admission ownership seams."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]

INTERACTION_FILES = (
    "agent/interaction/types.py",
    "agent/interaction/errors.py",
    "agent/interaction/model_contract.py",
    "agent/interaction/evidence.py",
    "agent/interaction/lexicon.py",
    "agent/interaction/guards.py",
    "agent/interaction/profile.py",
    "agent/interaction/continue_intent.py",
    "agent/interaction/prompt.py",
    "agent/interaction/transcript.py",
    "agent/interaction/resolver.py",
    "agent/interaction/response.py",
    "agent/interaction/admission.py",
    "agent/interaction/service.py",
)
RESOLVER_FILES = (
    "agent/interaction/resolver.py",
    "agent/interaction/prompt.py",
    "agent/interaction/model_contract.py",
)
CLI_FILES = (
    "agent/interfaces/cli/app.py",
    "agent/interfaces/cli/chat.py",
    "agent/interfaces/cli/command_handlers.py",
)

FORBIDDEN_IMPORT_PREFIXES = (
    "agent.workspace",
    "agent.memory",
    "agent.tools",
    "agent.skills",
    "agent.task_definition",
    "agent.continuity",
    "agent.checkpoint_manager",
    "agent.state_checkpoint",
    "agent.observability",
    "agent.orchestrator",
    "agent.application_run",
    "agent.runtime.task_runner",
    "agent.orchestration.task_runner",
)
FORBIDDEN_OWNER_NAMES = frozenset(
    {
        "Workspace",
        "WorkspaceContext",
        "Memory",
        "MemoryStore",
        "TaskDefinition",
        "TaskSemantics",
        "TaskRunner",
        "Orchestrator",
        "CheckpointManager",
        "ToolInvocationGateway",
        "ToolExecutor",
        "PlanExecutor",
        "ApprovalPort",
        "ApplicationAuthoritySnapshot",
        "TaskAuthoritySnapshot",
        "OperationalMode",
        "ProviderFactory",
        "resolve_model_profile",
        "resolve_gateway_model_profile",
        "create_model_gateway",
        "select_model",
        "set_model_profile",
    }
)
CHECKPOINT_CALLS = frozenset(
    {
        "load_checkpoint",
        "save_checkpoint",
        "write_checkpoint",
        "delete_checkpoint",
        "_load_checkpoint",
        "_save_checkpoint",
        "_delete_checkpoint",
    }
)


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    """One deterministic Wave 12 source-local architecture finding."""

    rule_id: str
    path: str
    detail: str
    line: int | None = None

    def format(self) -> str:
        suffix = f":{self.line}" if self.line is not None else ""
        return f"{self.rule_id} {self.path}{suffix}: {self.detail}"

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "path": self.path,
            "detail": self.detail,
            "line": self.line,
        }


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _source(root: Path, relative: str) -> str | None:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _tree(root: Path, relative: str) -> ast.Module | None:
    source = _source(root, relative)
    if source is None:
        return None
    try:
        return ast.parse(source, filename=relative)
    except SyntaxError:
        return None


def _violation(rule: str, relative: str, detail: str, node: ast.AST | None = None) -> ArchitectureViolation:
    return ArchitectureViolation(rule, relative, detail, getattr(node, "lineno", None))


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _module(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.Import):
        return node.names[0].name if node.names else ""
    return node.module or ""


def _nodes(tree: ast.AST | None) -> Iterator[ast.AST]:
    if tree is not None:
        yield from ast.walk(tree)


def _function(tree: ast.Module | None, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in _nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _has_text(root: Path, relative: str, *needles: str) -> bool:
    source = _source(root, relative)
    return source is not None and all(needle in source for needle in needles)


def _check_s1(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in RESOLVER_FILES:
        tree = _tree(root, relative)
        if tree is None:
            continue
        for node in _nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = _module(node)
                if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    findings.append(_violation("W12-S1", relative, "resolver owner imports a forbidden authority", node))
                if any(alias.name in FORBIDDEN_OWNER_NAMES for alias in node.names):
                    findings.append(_violation("W12-S1", relative, "resolver owner imports a task/workspace authority", node))
            elif _name(node) in FORBIDDEN_OWNER_NAMES:
                findings.append(_violation("W12-S1", relative, "resolver owner references a forbidden authority", node))
    return findings


def _check_s2(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in INTERACTION_FILES:
        tree = _tree(root, relative)
        for node in _nodes(tree):
            if _name(node) in {
                "Grant",
                "GrantSet",
                "Approval",
                "ApprovalPort",
                "OperationalMode",
                "ApplicationAuthoritySnapshot",
                "TaskAuthoritySnapshot",
                "bind_task_authority",
                "set_operational_mode",
                "grant_capability",
                "approve",
            }:
                findings.append(_violation("W12-S2", relative, "interaction code mutates or owns authority", node))
    return findings


def _check_s3(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in INTERACTION_FILES:
        tree = _tree(root, relative)
        for node in _nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = _module(node)
                if module.startswith(("agent.tools.invocation", "agent.orchestration.task_runner", "agent.orchestrator")):
                    findings.append(_violation("W12-S3", relative, "interaction imports an execution owner", node))
                if any(alias.name in {"TaskRunner", "Orchestrator", "ToolInvocationGateway", "ToolExecutor", "PlanExecutor"} for alias in node.names):
                    findings.append(_violation("W12-S3", relative, "interaction imports a direct task/tool executor", node))
            elif _name(node) in {"TaskRunner", "Orchestrator", "ToolInvocationGateway", "ToolExecutor", "PlanExecutor"}:
                findings.append(_violation("W12-S3", relative, "interaction references a direct task/tool executor", node))
    service = _source(root, "agent/interaction/service.py") or ""
    if "application.run(" not in service and "application.resume(" not in service:
        findings.append(_violation("W12-S3", "agent/interaction/service.py", "service has no admitted AgentApplication dispatch boundary"))
    return findings


def _check_s4(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in INTERACTION_FILES:
        tree = _tree(root, relative)
        for node in _nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and _module(node).startswith(("agent.checkpoint", "agent.state_checkpoint", "agent.continuity")):
                findings.append(_violation("W12-S4", relative, "interaction imports checkpoint/continuity body", node))
            if isinstance(node, ast.Call) and _name(node) in CHECKPOINT_CALLS:
                findings.append(_violation("W12-S4", relative, "interaction performs checkpoint I/O", node))
    return findings


def _check_s5(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in INTERACTION_FILES:
        tree = _tree(root, relative)
        for node in _nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = _module(node)
                if module.startswith(("agent.llm.provider", "agent.llm.providers")):
                    findings.append(_violation("W12-S5", relative, "interaction imports provider selection", node))
                if any(alias.name in {"resolve_model_profile", "resolve_gateway_model_profile", "create_model_gateway", "ProviderFactory", "select_model", "set_model_profile"} for alias in node.names):
                    findings.append(_violation("W12-S5", relative, "interaction imports model/profile selection", node))
            elif _name(node) in {"resolve_model_profile", "resolve_gateway_model_profile", "create_model_gateway", "ProviderFactory", "select_model", "set_model_profile"}:
                findings.append(_violation("W12-S5", relative, "interaction selects or mutates model/profile", node))
    return findings


def _check_s6(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in ("agent/interaction/admission.py", "agent/interaction/service.py"):
        tree = _tree(root, relative)
        for node in _nodes(tree):
            if isinstance(node, ast.Attribute) and node.attr == "AUTO":
                findings.append(_violation("W12-S6", relative, "fresh interaction path constructs or consumes semantic AUTO", node))
    return findings


def _check_s7(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in INTERACTION_FILES:
        tree = _tree(root, relative)
        source = _source(root, relative) or ""
        if "resolve_model_decision" in source or "extract_json_value" in source or "semantic_repair" in source:
            findings.append(_violation("W12-S7", relative, "interaction uses generic semantic repair or permissive admission"))
        for node in _nodes(tree):
            if isinstance(node, ast.Name) and node.id.casefold() in {"legacyresolver", "legacydecision", "repairresolver"}:
                findings.append(_violation("W12-S7", relative, "interaction uses a legacy/repair resolver", node))
    return findings


def _check_s8(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/runtime/task_directives.py"
    tree = _tree(root, relative)
    if tree is None:
        return [_violation("W12-S8", relative, "W11 directive owner is missing or unparsable")]
    functions = [
        node
        for node in _nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "to_checkpoint_dict"
    ]
    findings: list[ArchitectureViolation] = []
    allowed = {"schema_version", "directive", "deliberation_profile", "subject"}
    for function in functions:
        keys = {
            key.value
            for node in _nodes(function)
            if isinstance(node, ast.Dict)
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        if keys != allowed:
            findings.append(_violation("W12-S8", relative, "W11 checkpoint fields drifted", function))
    return findings


def _check_s9(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    handle = _function(_tree(root, "agent/interfaces/cli/app.py"), "_handle_input")
    if handle is not None:
        for node in _nodes(handle):
            if isinstance(node, ast.Attribute) and node.attr == "modo_agente":
                findings.append(_violation("W12-S9", "agent/interfaces/cli/app.py", "ordinary routing branches on modo_agente", node))
    commands = _source(root, "agent/interfaces/cli/commands.py") or ""
    for token in ("(\"/plan\"", "('/plan'", "(\"/do\"", "('/do'", "(\"/smart\"", "('/smart'", "(\"/cautious\"", "('/cautious'"):
        if token in commands:
            findings.append(_violation("W12-S9", "agent/interfaces/cli/commands.py", "top-level semantic W12 control is registered"))
            break
    return findings


def _check_s10(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in CLI_FILES:
        tree = _tree(root, relative)
        functions = [
            _function(tree, name)
            for name in ("run_agent_turn", "agent_command", "retry")
        ]
        for function in functions:
            for node in _nodes(function):
                if isinstance(node, ast.Call) and _name(node.func) in {"add_user_message", "add_assistant_message", "commit_one_pair"}:
                    findings.append(_violation("W12-S10", relative, "CLI adapter appends the visible transcript", node))
    return findings


def _check_s11(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in ("agent/interaction/resolver.py", "agent/interaction/response.py"):
        tree = _tree(root, relative)
        source = _source(root, relative) or ""
        for node in _nodes(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"complete_request", "consume_stream_request"}:
                findings.append(_violation("W12-S11", relative, "interaction reuses session model-call owner", node))
        if "for_session" in source:
            findings.append(_violation("W12-S11", relative, "interaction binds model calls to the main session"))
    return findings


def _check_s12(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    source = _source(root, "agent/interaction/resolver.py") or ""
    for needle in ("session.budget_ledger", "session.cancellation_token", "session.task_policy", "session.correlation"):
        if needle in source:
            findings.append(_violation("W12-S12", "agent/interaction/resolver.py", "interaction aliases a session/task owner"))
    if "CancellationToken()" not in source or "TaskBudgetLedger(" not in source or "policy_state=None" not in source or "task_policy=None" not in source:
        findings.append(_violation("W12-S12", "agent/interaction/resolver.py", "interaction-local policy/ledger/cancellation isolation is incomplete"))
    return findings


def _check_s13(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in CLI_FILES:
        source = _source(root, relative) or ""
        if "application.run(None" in source:
            findings.append(_violation("W12-S13", relative, "retry bypasses explicit W12 CONTINUE"))
    return findings


def _check_s14(root: Path) -> list[ArchitectureViolation]:
    return _check_s9(root)


def _check_s15(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    prompt = _source(root, "agent/interaction/prompt.py") or ""
    resolver = _source(root, "agent/interaction/resolver.py") or ""
    if "RESOLVER_JSON_INSTRUCTION" not in prompt or "RESOLVER_JSON_INSTRUCTION" not in resolver:
        findings.append(_violation("W12-S15", "agent/interaction", "JSON_PROMPT fallback is not sourced from a trusted contract constant"))
    return findings


def _check_s16(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/resolver.py") or ""
    calls = source.count("ModelCallService.for_context(context).complete")
    return [] if calls <= 1 and "raise ResolverInvalid" in source else [_violation("W12-S16", "agent/interaction/resolver.py", "resolver has a same-turn retry or no invalid projection")]


def _check_s17(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/llm/session_requests.py") or ""
    if "resolve_effective_reasoning_budget" not in source or "max_safe_reasoning" not in source or "final_output_reserve" not in source:
        return [_violation("W12-S17", "agent/llm/session_requests.py", "shared safe reasoning/output geometry seam is missing")]
    return []


def _check_s18(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in ("agent/llm/session_requests.py", "agent/interaction/resolver.py", "agent/interaction/response.py"):
        source = _source(root, relative) or ""
        if "build_effective_system_prompt_for_budget" not in source or "reasoning_budget=" not in source:
            findings.append(_violation("W12-S18", relative, "request field and thinking prompt do not share the effective budget"))
    return findings


def _check_s19(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/admission.py") or ""
    function = _function(_tree(root, "agent/interaction/admission.py"), "_admit_continue")
    body = ast.unparse(function) if function is not None else ""
    if "select_fresh_profile" in body or "deliberation_profile" in body:
        return [_violation("W12-S19", "agent/interaction/admission.py", "CONTINUE applies a fresh profile")]
    return [] if "DirectTaskResumeGuard" in source else [_violation("W12-S19", "agent/interaction/admission.py", "CONTINUE owner is missing")]


def _check_s20(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/admission.py") or ""
    return [] if "DirectTaskResumeGuard" in source and "ResumeClassification.DIRECT_RESUME" in source else [_violation("W12-S20", "agent/interaction/admission.py", "natural CONTINUE lacks the dedicated direct-resume guard")]


def _check_s21(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/service.py") or ""
    return [] if all(needle in source for needle in ("snapshot_visible_messages", "restore_visible_messages", "finally:")) else [_violation("W12-S21", "agent/interaction/service.py", "RUN/CONTINUE transcript transaction is not exception-safe")]


def _check_s22(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/llm/session_requests.py", "resolve_effective_reasoning_budget", "build_effective_system_prompt_for_budget") else [_violation("W12-S22", "agent/llm/session_requests.py", "W11 request builder bypasses shared geometry")]


def _check_s23(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/model_contract.py") or ""
    return [] if "object_pairs_hook" in source and "parse_constant" in source and "reject_duplicate_keys" in source else [_violation("W12-S23", "agent/interaction/model_contract.py", "strict JSON parser lacks duplicate/constant rejection")]


def _check_s24(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/interaction/lexicon.py", "EFFECT_LEXICON", "NEGATION_PREFIXES") and _has_text(root, "agent/interaction/continue_intent.py", "AMBIGUOUS_RESUMES") else [_violation("W12-S24", "agent/interaction", "closed guard vocabulary is not locally owned")]


def _check_s25(root: Path) -> list[ArchitectureViolation]:
    function = _function(_tree(root, "agent/interaction/service.py"), "interact_locked")
    source = ast.unparse(function) if function is not None else ""
    return [] if source.find("if len(visible) > MAX_STRING_LENGTH") < source.find("commit_one_pair") else [_violation("W12-S25", "agent/interaction/service.py", "input preflight occurs after transcript commit")]


def _check_s26(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/llm/session.py") or ""
    validation = source.find("validate_transcript_messages")
    assignment = source.find("self.messages = data")
    return [] if validation >= 0 and assignment > validation else [_violation("W12-S26", "agent/llm/session.py", "history assignment lacks pre-assignment structural validation")]


def _check_s27(root: Path) -> list[ArchitectureViolation]:
    function = _function(_tree(root, "agent/interfaces/cli/app.py"), "_run_once")
    source = ast.unparse(function) if function is not None else ""
    return [] if source.find("if request.action is TaskRequestAction.CONTINUE") < source.find("application = _create_application") else [_violation("W12-S27", "agent/interfaces/cli/app.py", "headless CONTINUE bootstraps before W10 preflight")]


def _check_s28(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/admission.py") or ""
    return [] if "def project_guard_result" in source and "project_guard_result(" in source else [_violation("W12-S28", "agent/interaction/admission.py", "guard results lack one central projection owner")]


def _check_s29(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/interaction/guards.py", "OperationalClassification.HYPOTHETICAL", "_standalone_plain_conditional") and _has_text(root, "agent/interaction/admission.py", "guard is not OperationalClassification.DIRECT") else [_violation("W12-S29", "agent/interaction", "conditional effects can escape inferred-DO admission")]


def _check_s30(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/admission.py") or ""
    return [] if "DirectReadRequestGuard" in source and "_admit_read" in source else [_violation("W12-S30", "agent/interaction/admission.py", "READ proof owner is missing")]


def _check_s31(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/admission.py") or ""
    return [] if all(needle in source for needle in ("DirectOperationalRequestGuard", "DirectOperationalTargetGuard", "TargetProof.PROVEN")) else [_violation("W12-S31", "agent/interaction/admission.py", "inferred DO omits speech-act or target proof")]


def _check_s32(root: Path) -> list[ArchitectureViolation]:
    return _check_s27(root)


def _check_s33(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/guards.py") or ""
    bad = "anchor in complement" in source or "any(anchor in" in source
    return [_violation("W12-S33", "agent/interaction/guards.py", "target proof scans an entire complement", None)] if bad else []


def _check_s34(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/interaction/guards.py", "_standalone_plain_conditional", "OperationalClassification.HYPOTHETICAL") else [_violation("W12-S34", "agent/interaction/guards.py", "conditional suffix guard is missing")]


def _check_s35(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/interaction/guards.py", '"TARGET_SCOPED"', '"FAMILY_ALL"') else [_violation("W12-S35", "agent/interaction/guards.py", "negative restriction scopes are not distinguished")]


def _check_s36(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/guards.py") or ""
    family = source.find('restriction.scope == "FAMILY_ALL"')
    anchor = source.find("_target_for_relation", family if family >= 0 else 0)
    return [] if family >= 0 and anchor > family else [_violation("W12-S36", "agent/interaction/guards.py", "FAMILY_ALL is not consumed before target relation")]


def _check_s37(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/interaction/guards.py", "strip_one_request_prefix", "parse_negative_restriction") else [_violation("W12-S37", "agent/interaction/guards.py", "negative scanner has no shared prefix normalization")]


def _check_s38(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/guards.py") or ""
    return [] if "value.startswith(core + \" \")" in source else [_violation("W12-S38", "agent/interaction/guards.py", "NETWORK FAMILY_ALL lacks trailing-core matching")]


def _check_s39(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/admission.py") or ""
    return [] if "MixedIntentTailGuard" in source else [_violation("W12-S39", "agent/interaction/admission.py", "READ/PLAN admission lacks same-segment mixed-intent guard")]


def _check_s40(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/interaction/guards.py", "NEGATIVE_INFINITIVE_FORMS") else [_violation("W12-S40", "agent/interaction/guards.py", "local/cross negation does not share canonical forms")]


def _check_s41(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/interaction/response.py", "bounded_prior_pairs", "build_response_request_plan") else [_violation("W12-S41", "agent/interaction/response.py", "RESPOND bypasses bounded context fitting")]


def _check_s42(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/response.py") or ""
    return [_violation("W12-S42", "agent/interaction/response.py", "response fitting slices current content")] if "current_user[" in source or "snapshot[0][" in source and "content" in source and "[:" in source else []


def _check_s43(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/application.py", "cancel_active_model_call") and _has_text(root, "agent/interaction/service.py", "_active_model_cancellation") else [_violation("W12-S43", "agent/application.py", "application cancellation lacks active interaction seam")]


def _check_s44(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/application.py") or ""
    cancel = source.find("cancel_active_model_call")
    terminal = source.find("self.orchestrator.cancel_task", cancel if cancel >= 0 else 0)
    between = source[cancel:terminal] if cancel >= 0 and terminal >= 0 else ""
    return [] if "return" in between else [_violation("W12-S44", "agent/application.py", "active interaction cancel fans out to task cancellation")]


def _check_s45(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/interaction/guards.py", "normalize_clause_for_guard") else [_violation("W12-S45", "agent/interaction/guards.py", "guards do not use shared clause normalization")]


def _check_s46(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/evidence.py") or ""
    return [] if "GUARD_TARGET_PUNCTUATION = \"()[]{}<>,:;.!?\"" in source else [_violation("W12-S46", "agent/interaction/evidence.py", "target punctuation does not include hard delimiters")]


def _check_s47(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/response.py") or ""
    return [] if "MAX_RESPONSE_CONTEXT_EXACT_PROBES = 2" in source and "bounded_prior_pairs" in source else [_violation("W12-S47", "agent/interaction/response.py", "response fitting is not bounded")]


def _check_s48(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/interaction/guards.py", "_target_fragment", "__unproven__") else [_violation("W12-S48", "agent/interaction/guards.py", "tail-closed effect target proof is missing")]


def _check_s49(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative, transport, barrier in (
        ("agent/interaction/resolver.py", "ModelCallService.for_context(context).complete", "if token.cancelled"),
        ("agent/interaction/service.py", "complete_response", "if token.cancelled"),
    ):
        source = _source(root, relative) or ""
        if transport not in source or barrier not in source:
            findings.append(_violation("W12-S49", relative, "active token post-return barrier is missing"))
    return findings


def _check_s50(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/interaction/admission.py", "DirectPlanRequestGuard", "PlanClassification.DIRECT_PLAN") else [_violation("W12-S50", "agent/interaction/admission.py", "fresh PLAN lacks the single direct proof owner")]


def _check_s51(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/guards.py") or ""
    cross = source[source.find("def cross_clause_effect_conflict") :]
    return [] if "def normalize_target_anchor_identity" in source and "_target_for_relation(" in cross else [_violation("W12-S51", "agent/interaction/guards.py", "cross-clause relation lacks canonical target identity")]


def _check_s52(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/interaction/admission.py", "LocalEffectConflictGuard", "LocalConflictClassification.CLEAR") else [_violation("W12-S52", "agent/interaction/admission.py", "inferred DO omits local contradiction guard")]


def _check_s53(root: Path) -> list[ArchitectureViolation]:
    return [] if _has_text(root, "agent/interaction/resolver.py", "INTERACTION_RESOLUTION_GBNF", "select_interaction_structured_output") else [_violation("W12-S53", "agent/interaction/resolver.py", "resolver GBNF is not sourced from the authoritative constant")]


def _check_s54(root: Path) -> list[ArchitectureViolation]:
    source = _source(root, "agent/interaction/model_contract.py") or ""
    validator = source.find("reject_unicode_surrogates(value)")
    typed = source.find("return InteractionModelDecision", validator if validator >= 0 else 0)
    return [] if validator >= 0 and typed > validator else [_violation("W12-S54", "agent/interaction/model_contract.py", "Unicode scalar validation is missing before typed admission")]


_CHECKS = tuple(globals()[f"_check_s{index}"] for index in range(1, 55))


def check_architecture(root: str | Path = ".") -> list[ArchitectureViolation]:
    resolved = Path(root).expanduser().resolve()
    findings = [finding for check in _CHECKS for finding in check(resolved)]
    try:
        from scripts import check_wave11_architecture
    except ImportError:
        try:
            import check_wave11_architecture
        except ImportError as exc:
            findings.append(_violation("W12-S8", "scripts/check_wave11_architecture.py", f"W11 checker unavailable: {type(exc).__name__}"))
            return findings

    except OSError:
        findings.append(_violation("W12-S8", "scripts/check_wave11_architecture.py", "W11 checker unavailable"))
        return findings
    findings.extend(
        ArchitectureViolation(item.rule_id, item.path, item.detail, item.line)
        for item in check_wave11_architecture.check_architecture(resolved)
    )
    return findings


def check_source(path: str | Path, root: str | Path | None = None) -> list[ArchitectureViolation]:
    resolved_root = Path(root).expanduser().resolve() if root is not None else ROOT
    source = Path(path).expanduser().resolve()
    try:
        relative = _relative(source, resolved_root)
    except ValueError:
        return [_violation("W12-S0", str(source), "source is outside repository root")]
    return [item for item in check_architecture(resolved_root) if item.path == relative]


find_violations = check_architecture
check_wave12_architecture = check_architecture


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Wave 12 interaction ownership boundaries")
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    args = parser.parse_args(list(argv) if argv is not None else None)
    violations = check_architecture(args.root)
    if violations:
        for violation in violations:
            print(violation.format())
        return 1
    print("W12 architecture checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
