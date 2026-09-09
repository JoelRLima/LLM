"""Static ownership and mutation gates for Wave 15.5.

The gate is intentionally source/AST based.  It protects the three closure
owners (credential reference, task workspace, and bounded audit projection)
and proves that each guard is live on an isolated temporary source copy.
"""

from __future__ import annotations

import argparse
import ast
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ARCHITECTURE_RULES = tuple(f"W155-C{index:02d}" for index in range(1, 17))
REQUIRED_MUTATION_ARMS = tuple(f"W155-M{index:02d}" for index in range(1, 17))
CORRECTIVE_MUTATION_ARMS = tuple(f"W155-CORR-M{index:02d}" for index in range(1, 4))

_SECRET_REFERENCE = "agent/runtime/secret_reference.py"
_CONFIG_SCHEMA = "agent/runtime/config_schema.py"
_MODEL_PROFILE = "agent/llm/model_profile.py"
_PROVIDER = "agent/llm/providers/openai_compatible.py"
_INPUT_TOKENS = "agent/llm/providers/openai_input_tokens.py"
_MODEL_CALL = "agent/runtime/model_call.py"
_MODEL_CALL_RECORD = "agent/runtime/model_call_record.py"
_SESSION_REQUESTS = "agent/llm/session_requests.py"
_APP = "agent/interfaces/cli/app.py"
_CONTINUITY = "agent/interfaces/cli/task_continuity.py"
_WORKSPACE_ENTRY = "agent/interfaces/cli/workspace_entry.py"
_AUDIT = "agent/observability/audit_projection.py"
_APPLICATION_RESULT = "agent/application_result.py"
_INVOCATION_COMMIT = "agent/tools/invocation_commit.py"
_EVENT_KINDS = "agent/runtime/event_kinds.py"
_PRESENTATION_PROJECTOR = "agent/presentation/projector.py"
_INSTALLED_GATE = "scripts/verify_installed_package.py"


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    rule_id: str
    path: str
    detail: str
    line: int | None = None

    def format(self) -> str:
        suffix = f":{self.line}" if self.line is not None else ""
        return f"{self.rule_id} {self.path}{suffix}: {self.detail}"


@dataclass(frozen=True, slots=True)
class MutationArm:
    arm_id: str
    description: str
    mutate: Callable[[Path], bool]


def _source(root: Path, relative: str) -> str | None:
    try:
        return (root / relative).read_text(encoding="utf-8")
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


def _nodes(node: ast.AST | None) -> Iterator[ast.AST]:
    if node is not None:
        yield from ast.walk(node)


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _functions(tree: ast.AST | None, name: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in _nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]


def _function(tree: ast.AST | None, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    values = _functions(tree, name)
    return values[0] if values else None


def _imports(tree: ast.AST | None) -> tuple[str, ...]:
    values: list[str] = []
    for node in _nodes(tree):
        if isinstance(node, ast.Import):
            values.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            values.append(node.module or "")
    return tuple(values)


def _calls(tree: ast.AST | None) -> Iterator[ast.Call]:
    for node in _nodes(tree):
        if isinstance(node, ast.Call):
            yield node


def _literal_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _violation(rule: str, path: str, detail: str, node: ast.AST | None = None) -> ArchitectureViolation:
    return ArchitectureViolation(rule, path, detail, getattr(node, "lineno", None))


def _required(root: Path, rule: str, relative: str) -> tuple[ast.Module | None, list[ArchitectureViolation]]:
    tree = _tree(root, relative)
    if tree is None:
        return None, [_violation(rule, relative, "required owner is missing, unreadable, or unparsable")]
    return tree, []


def _check_c01_credential_boundary(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C01"
    findings: list[ArchitectureViolation] = []
    secret_tree, required = _required(root, rule, _SECRET_REFERENCE)
    findings.extend(required)
    if secret_tree is not None and _function(secret_tree, "resolve_secret_reference") is None:
        findings.append(_violation(rule, _SECRET_REFERENCE, "late credential resolver is missing"))
    for relative in (_CONFIG_SCHEMA, _MODEL_PROFILE):
        tree, missing = _required(root, rule, relative)
        findings.extend(missing)
        if tree is None:
            continue
        if any(_qualified_name(call.func) == "resolve_secret_reference" for call in _calls(tree)):
            findings.append(_violation(rule, relative, "credential value is resolved during config/profile construction"))
        source = _source(root, relative) or ""
        if "os.environ" in source or "os.getenv" in source:
            findings.append(_violation(rule, relative, "config/profile owner reads ambient credential values"))
    provider, missing = _required(root, rule, _PROVIDER)
    findings.extend(missing)
    provider_source = _source(root, _PROVIDER) or ""
    if provider is not None and _function(provider, "_send_payload") is None:
        findings.append(_violation(rule, _PROVIDER, "provider transport boundary is missing"))
    if "resolve_secret_reference" not in provider_source or "Authorization" not in provider_source:
        findings.append(_violation(rule, _PROVIDER, "provider transport does not resolve the reference into the bearer header"))
    return findings


def _check_c02_secret_context(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C02"
    findings: list[ArchitectureViolation] = []
    for relative in (_MODEL_CALL, _MODEL_CALL_RECORD, _SESSION_REQUESTS):
        tree, missing = _required(root, rule, relative)
        findings.extend(missing)
        source = _source(root, relative) or ""
        if "credential_ref" in source or "resolve_secret_reference" in source:
            findings.append(_violation(rule, relative, "credential reference/value enters prompt, model-call, or event state"))
        if tree is not None:
            for call in _calls(tree):
                if _qualified_name(call.func) in {"getenv", "environ.get"}:
                    findings.append(_violation(rule, relative, "model-call path reads ambient secret state", call))
    return findings


def _check_c03_explicit_workspace(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C03"
    findings: list[ArchitectureViolation] = []
    entry, missing = _required(root, rule, _WORKSPACE_ENTRY)
    findings.extend(missing)
    entry_source = _source(root, _WORKSPACE_ENTRY) or ""
    required_fn = _function(entry, "require_task_workspace") if entry is not None else None
    required_text = ast.unparse(required_fn) if required_fn is not None else ""
    for token in ("TaskWorkspaceRequiredError", "value is None", "return Path(str(value)).expanduser()"):
        if token not in required_text:
            findings.append(_violation(rule, _WORKSPACE_ENTRY, f"explicit task workspace guard is missing: {token}"))
    if "def argument_workspace" not in entry_source:
        findings.append(_violation(rule, _WORKSPACE_ENTRY, "interactive compatibility workspace adapter is missing"))
    for relative, function_name in ((_APP, "_run_once"), (_APP, "_run_chat"), (_CONTINUITY, "run_task_resume")):
        tree, missing = _required(root, rule, relative)
        findings.extend(missing)
        owner = _function(tree, function_name) if tree is not None else None
        text = ast.unparse(owner) if owner is not None else ""
        if "require_task_workspace(args)" not in text:
            findings.append(_violation(rule, relative, f"{function_name} does not fail closed before task application", owner))
        for call in _calls(owner):
            if _qualified_name(call.func) == "Path.cwd":
                findings.append(_violation(rule, relative, f"{function_name} silently derives task workspace from CWD", call))
    return findings


def _check_c04_audit_read_only(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C04"
    tree, findings = _required(root, rule, _AUDIT)
    if tree is None:
        return findings
    forbidden_modules = (
        "agent.llm.providers",
        "agent.llm.session",
        "agent.tools.invocation_gateway",
        "agent.tools.tool_registry",
        "agent.interfaces.cli",
    )
    for module in _imports(tree):
        if module.startswith(forbidden_modules):
            findings.append(_violation(rule, _AUDIT, f"audit projection imports executable owner: {module}"))
    forbidden_calls = {"complete", "complete_request", "stream", "invoke", "run", "execute", "send", "post"}
    for call in _calls(tree):
        if _name(call.func) in forbidden_calls:
            findings.append(_violation(rule, _AUDIT, "audit projection calls model/tool execution", call))
    return findings


def _check_c05_audit_authority_separation(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C05"
    findings: list[ArchitectureViolation] = []
    for path in sorted((root / "agent").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if not (relative.startswith("agent/planning/") or relative in {"agent/tools/authority.py", "agent/approval.py"}):
            continue
        source = _source(root, relative) or ""
        for token in ("RunAuditReceipt", "build_run_audit_receipt", "run_audit_receipt"):
            if token in source:
                findings.append(_violation(rule, relative, "audit receipt is consumed by authority/approval/planning"))
                break
    return findings


def _check_c06_dispatcher_and_sink(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C06"
    findings: list[ArchitectureViolation] = []
    application_source = _source(root, _APPLICATION_RESULT) or ""
    if 'emitter("run_audit_receipt"' not in application_source:
        findings.append(_violation(rule, _APPLICATION_RESULT, "final audit receipt is not emitted through the orchestrator dispatcher"))
    if "TraceStore" in application_source and "run_audit_receipt" in application_source:
        findings.append(_violation(rule, _APPLICATION_RESULT, "application execution appends the audit event directly to TraceStore"))
    commit_source = _source(root, _INVOCATION_COMMIT) or ""
    commit_tree = _tree(root, _INVOCATION_COMMIT)
    if "RuntimeEvent.from_fields" not in commit_source or "dispatcher.emit" not in commit_source:
        findings.append(_violation(rule, _INVOCATION_COMMIT, "tool semantic events do not use the existing dispatcher"))
    if commit_tree is None:
        findings.append(_violation(rule, _INVOCATION_COMMIT, "invocation commit owner is unparsable"))
    return findings


def _check_c07_safe_payload(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C07"
    tree, findings = _required(root, rule, _AUDIT)
    if tree is None:
        return findings
    forbidden_fragments = ("args", "result", "content", "diff", "output", "prompt", "completion", "credential", "secret")
    for node in _nodes(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                text = _literal_key(key) if key is not None else None
                if text and any(fragment in text.casefold() for fragment in forbidden_fragments):
                    findings.append(_violation(rule, _AUDIT, f"raw audit payload field is present: {text}", node))
        if isinstance(node, ast.Attribute) and node.attr in {"content", "args", "result", "diff", "output", "prompt"}:
            findings.append(_violation(rule, _AUDIT, f"audit projection reads raw field: {node.attr}", node))
    return findings


def _check_c08_descriptor_versions(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C08"
    tree, findings = _required(root, rule, _AUDIT)
    if tree is None:
        return findings
    owner = _function(tree, "project_tool_descriptor")
    text = ast.unparse(owner) if owner is not None else ""
    for token in ("source_version", "protocol_version"):
        if token not in text:
            findings.append(_violation(rule, _AUDIT, f"ToolDescriptor version provenance is missing: {token}", owner))
    return findings


def _check_c09_observed_identity(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C09"
    tree, findings = _required(root, rule, _AUDIT)
    if tree is None:
        return findings
    owner = _function(tree, "_observed_model_projection")
    text = ast.unparse(owner) if owner is not None else ""
    if owner is None or "observed_provider_model_id" not in text:
        findings.append(_violation(rule, _AUDIT, "observed provider identity is not sourced from response metadata", owner))
    if "declared_model" in text or "project_model_identity" in text:
        findings.append(_violation(rule, _AUDIT, "declared model identity is used as observed identity", owner))
    return findings


def _audit_emit_calls(tree: ast.AST | None) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for call in _calls(tree):
        if _name(call.func) not in {"_emit", "emitter"}:
            continue
        if any(isinstance(argument, ast.Constant) and argument.value == "run_audit_receipt" for argument in call.args):
            calls.append(call)
    return calls


def _check_c10_receipt_timing(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C10"
    tree, findings = _required(root, rule, _APPLICATION_RESULT)
    if tree is None:
        return findings
    build_calls = [call for call in _calls(tree) if _name(call.func) == "build_canonical_run_snapshot"]
    emit_calls = _audit_emit_calls(tree)
    if not build_calls or not emit_calls:
        findings.append(_violation(rule, _APPLICATION_RESULT, "receipt timing lacks canonical snapshot or audit emission"))
    elif min(call.lineno for call in emit_calls) <= min(call.lineno for call in build_calls):
        findings.append(_violation(rule, _APPLICATION_RESULT, "receipt is emitted before the canonical terminal snapshot"))
    return findings


def _check_c11_observer_failure(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C11"
    tree, findings = _required(root, rule, _APPLICATION_RESULT)
    if tree is None:
        return findings
    target = _function(tree, "finalize_application_result")
    audit_tries = [
        node
        for node in _nodes(target)
        if isinstance(node, ast.Try)
        and any(_name(call.func) == "build_run_audit_receipt" for call in _calls(node))
    ]
    audit_handlers = [handler for node in audit_tries for handler in node.handlers]
    if not audit_handlers:
        findings.append(_violation(rule, _APPLICATION_RESULT, "audit projection failure boundary is missing", target))
    for handler in audit_handlers:
        for node in _nodes(handler):
            if isinstance(node, ast.Return):
                findings.append(_violation(rule, _APPLICATION_RESULT, "observer failure returns/rewrites the canonical task result", node))
            if isinstance(node, ast.Assign):
                targets = {_name(target) for target in node.targets}
                if targets & {"status", "error", "receipt", "snapshot"}:
                    findings.append(_violation(rule, _APPLICATION_RESULT, "observer failure rewrites task outcome state", node))
    return findings


def _check_c12_bounds(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C12"
    source = _source(root, _AUDIT) or ""
    findings: list[ArchitectureViolation] = []
    for token in ("MAX_AUDIT_TEXT", "MAX_AUDIT_CAPABILITIES", "MAX_AUDIT_TOOLS", "MAX_AUDIT_ARTIFACTS", "MAX_AUDIT_PATHS", "MAX_AUDIT_MODEL_IDS", "omitted", "truncated"):
        if token not in source:
            findings.append(_violation(rule, _AUDIT, f"bounded audit collection marker is missing: {token}"))
    for token in ("observed_ids[:MAX_AUDIT_MODEL_IDS]", "tool_identities[:MAX_AUDIT_TOOLS]"):
        if token not in source:
            findings.append(_violation(rule, _AUDIT, f"audit repeated field is not explicitly bounded: {token}"))
    if "len(refs) >= MAX_AUDIT_ARTIFACTS" not in source:
        findings.append(_violation(rule, _AUDIT, "artifact references are unbounded"))
    return findings


def _check_c13_projection_purity(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C13"
    source = _source(root, _AUDIT) or ""
    findings: list[ArchitectureViolation] = []
    for token in ("TraceStore", "requests", "subprocess", "write_text", "unlink", "os.system"):
        if token in source:
            findings.append(_violation(rule, _AUDIT, f"audit projection performs I/O or process work: {token}"))
    return findings


def _check_c14_terminal_and_authority_sources(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C14"
    tree, findings = _required(root, rule, _AUDIT)
    if tree is None:
        return findings
    receipt_builder = _function(tree, "build_run_audit_receipt")
    text = ast.unparse(receipt_builder) if receipt_builder is not None else ""
    if "_terminal_projection" not in text or "_authority_projection" not in text:
        findings.append(_violation(rule, _AUDIT, "receipt does not consume canonical terminal/authority projections", receipt_builder))
    if any(token in text for token in ("approve", "authorize", "grant", "plan")):
        findings.append(_violation(rule, _AUDIT, "receipt builder acts as an authority/approval/planning owner", receipt_builder))
    return findings


def _check_c15_presentation_reuse(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C15"
    findings: list[ArchitectureViolation] = []
    event_source = _source(root, _EVENT_KINDS) or ""
    projector_source = _source(root, _PRESENTATION_PROJECTOR) or ""
    for relative, source in ((_EVENT_KINDS, event_source), (_PRESENTATION_PROJECTOR, projector_source)):
        if "run_audit_receipt" not in source:
            findings.append(_violation(rule, relative, "generic presentation/event support does not know the semantic receipt"))
    if '"run_audit_receipt": "audit"' not in projector_source or '"run_audit_receipt"' not in projector_source:
        findings.append(_violation(rule, _PRESENTATION_PROJECTOR, "receipt is not searchable/readable through the existing projector"))
    return findings


def _check_c16_deterministic_gate(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C16"
    source = _source(root, _INSTALLED_GATE) or ""
    findings: list[ArchitectureViolation] = []
    lowered = source.casefold()
    for token in ("qwen", "real-model-epoch-2"):
        if token in lowered:
            findings.append(_violation(rule, _INSTALLED_GATE, f"deterministic readiness path contains forbidden live-model marker: {token}"))
    if "ThreadingHTTPServer" not in source or "127.0.0.1" not in source:
        findings.append(_violation(rule, _INSTALLED_GATE, "installed readiness gate lacks a local deterministic fixture boundary"))
    return findings


def _function_containing_call(tree: ast.AST | None, target: ast.Call) -> ast.AST | None:
    candidates: list[ast.AST] = []
    for node in _nodes(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(candidate is target for candidate in _calls(node)):
            candidates.append(node)
    return min(
        candidates,
        key=lambda node: (
            int(getattr(node, "end_lineno", 0)) - int(getattr(node, "lineno", 0)),
            int(getattr(node, "lineno", 0)),
        ),
        default=None,
    )


def _check_c17_receipt_run_scope(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C17"
    tree, findings = _required(root, rule, _APPLICATION_RESULT)
    if tree is None:
        return findings
    owner = _function(tree, "finalize_application_result")
    if owner is None:
        return [_violation(rule, _APPLICATION_RESULT, "application result finalization owner is missing")]
    names = {
        node.id
        for node in _nodes(owner)
        if isinstance(node, ast.Name)
    }
    attributes = {
        node.attr
        for node in _nodes(owner)
        if isinstance(node, ast.Attribute)
    }
    strings = {
        node.value
        for node in _nodes(owner)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    if (
        "_audit_receipt_emitted" in names
        or "_audit_receipt_emitted" in attributes
        or "_audit_receipt_emitted" in strings
    ):
        findings.append(
            _violation(
                rule,
                _APPLICATION_RESULT,
                "final audit receipt guard is application-scoped rather than run-scoped",
                owner,
            )
        )
    text = ast.unparse(owner)
    for token in ("current_run_id", "snapshot.correlation.run_id", "_audit_receipt_emitted_run_id"):
        if token not in text:
            findings.append(_violation(rule, _APPLICATION_RESULT, f"run-scoped receipt identity is missing: {token}", owner))
    return findings


def _check_c18_provider_http_boundary(root: Path) -> list[ArchitectureViolation]:
    rule = "W155-C18"
    findings: list[ArchitectureViolation] = []
    provider, missing = _required(root, rule, _PROVIDER)
    findings.extend(missing)
    if provider is not None and _function(provider, "_request_headers") is None:
        findings.append(_violation(rule, _PROVIDER, "provider credential-aware HTTP header boundary is missing"))
    provider_root = root / "agent" / "llm" / "providers"
    if not provider_root.is_dir():
        findings.append(_violation(rule, "agent/llm/providers", "provider package is missing"))
        return findings
    for path in sorted(provider_root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        tree = _tree(root, relative)
        if tree is None:
            findings.append(_violation(rule, relative, "provider HTTP owner is unparsable"))
            continue
        for call in _calls(tree):
            if _qualified_name(call.func) != "requests.post":
                continue
            owner = _function_containing_call(tree, call)
            owner_text = ast.unparse(owner) if owner is not None else ""
            if owner is None or "_request_headers" not in owner_text:
                findings.append(
                    _violation(
                        rule,
                        relative,
                        "provider-owned HTTP request bypasses the credential-aware header boundary",
                        call,
                    )
                )
    return findings


_CHECKS: tuple[Callable[[Path], list[ArchitectureViolation]], ...] = (
    _check_c01_credential_boundary,
    _check_c02_secret_context,
    _check_c03_explicit_workspace,
    _check_c04_audit_read_only,
    _check_c05_audit_authority_separation,
    _check_c06_dispatcher_and_sink,
    _check_c07_safe_payload,
    _check_c08_descriptor_versions,
    _check_c09_observed_identity,
    _check_c10_receipt_timing,
    _check_c11_observer_failure,
    _check_c12_bounds,
    _check_c13_projection_purity,
    _check_c14_terminal_and_authority_sources,
    _check_c15_presentation_reuse,
    _check_c16_deterministic_gate,
    _check_c17_receipt_run_scope,
    _check_c18_provider_http_boundary,
)


def check_architecture(root: str | Path = ROOT) -> list[ArchitectureViolation]:
    resolved = Path(root).expanduser().resolve()
    findings = [finding for check in _CHECKS for finding in check(resolved)]
    return sorted(findings, key=lambda item: (item.rule_id, item.path, item.line or 0, item.detail))


find_violations = check_architecture
check_wave15_5_architecture = check_architecture


def _replace_once(path: Path, old: str, new: str) -> bool:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if old not in source:
        return False
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return True


def _append(path: Path, text: str) -> bool:
    try:
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
        return True
    except (OSError, UnicodeError):
        return False


def _replace_in_function(path: Path, function_name: str, old: str, new: str) -> bool:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    start = source.find(f"def {function_name}(")
    if start < 0:
        return False
    next_function = source.find("\ndef ", start + 5)
    end = len(source) if next_function < 0 else next_function
    section = source[start:end]
    if old not in section:
        return False
    section = section.replace(old, new, 1)
    path.write_text(source[:start] + section + source[end:], encoding="utf-8")
    return True


def _mutation_arms() -> tuple[MutationArm, ...]:
    return (
        MutationArm("W155-M01", "resolve credential during config load", lambda root: _append(root / _CONFIG_SCHEMA, "\nfrom agent.runtime.secret_reference import resolve_secret_reference\ndef _mutant_m01(document):\n    return resolve_secret_reference(document)\n")),
        MutationArm("W155-M02", "persist resolved secret into model profile", lambda root: _append(root / _MODEL_PROFILE, "\nfrom agent.runtime.secret_reference import resolve_secret_reference\ndef _mutant_m02(reference):\n    return resolve_secret_reference(reference)\n")),
        MutationArm("W155-M03", "inject secret into event payload", lambda root: _append(root / _MODEL_CALL, "\ndef _mutant_m03(profile):\n    return {'credential_ref': profile.credential_ref}\n")),
        MutationArm("W155-M04", "headless run falls back to Path.cwd", lambda root: _replace_in_function(root / _APP, "_run_once", "require_task_workspace(args)", "argument_workspace(args)")),
        MutationArm("W155-M05", "task resume falls back to Path.cwd", lambda root: _replace_in_function(root / _CONTINUITY, "run_task_resume", "require_task_workspace(args)", "argument_workspace(args)")),
        MutationArm("W155-M06", "noninteractive chat falls back to CWD", lambda root: _replace_in_function(root / _APP, "_run_chat", "require_task_workspace(args)", "argument_workspace(args)")),
        MutationArm("W155-M07", "audit projection calls model", lambda root: _append(root / _AUDIT, "\nfrom agent.llm.session import ChatSession\n_mutant_m07 = ChatSession.complete\n")),
        MutationArm("W155-M08", "audit projection calls tool gateway", lambda root: _append(root / _AUDIT, "\nfrom agent.tools.invocation_gateway import ToolInvocationGateway\n_mutant_m08 = ToolInvocationGateway.run\n")),
        MutationArm("W155-M09", "audit receipt is consumed by authority/planning", lambda root: _append(root / "agent/tools/authority.py", "\nfrom agent.observability.audit_projection import RunAuditReceipt\n_mutant_m09 = RunAuditReceipt\n")),
        MutationArm("W155-M10", "production appends audit directly to TraceStore", lambda root: _append(root / _APPLICATION_RESULT, "\nfrom agent.observability.trace_store import TraceStore\n_mutant_m10 = (TraceStore, 'run_audit_receipt')\n")),
        MutationArm("W155-M11", "audit includes raw args/result/artifact content", lambda root: _append(root / _AUDIT, "\ndef _mutant_m11(args, result, artifact):\n    return {'raw_args': args, 'raw_result': result, 'artifact_content': artifact.content}\n")),
        MutationArm("W155-M12", "tool version/protocol source is omitted", lambda root: _replace_once(root / _AUDIT, '"source_version": _safe_string(get("source_version")),\n        "protocol_version": _safe_string(get("protocol_version")),', '"source_version": None,')),
        MutationArm("W155-M13", "observed model id is inferred from declared model", lambda root: _replace_once(root / _AUDIT, 'identity = observed_provider_model_id(item.get("provider_metadata"))', 'identity = observed_provider_model_id(item.get("declared_model"))')),
        MutationArm("W155-M14", "receipt is emitted before canonical snapshot", lambda root: _replace_once(root / _APPLICATION_RESULT, '    snapshot = getattr(orchestrator, "_canonical_run_snapshot", None)', '    orchestrator._emit("run_audit_receipt", {})\n    snapshot = getattr(orchestrator, "_canonical_run_snapshot", None)')),
        MutationArm("W155-M15", "observer failure rewrites task result/status", lambda root: _replace_once(root / _APPLICATION_RESULT, '            logger.warning("Run audit receipt projection failed: %s", type(exc).__name__)', '            return logger.warning("Run audit receipt projection failed: %s", type(exc).__name__)')),
        MutationArm("W155-M16", "audit collection is silently unbounded", lambda root: _replace_once(root / _AUDIT, 'model_emitted = observed_ids[:MAX_AUDIT_MODEL_IDS]', 'model_emitted = observed_ids')),
    )


def _corrective_mutation_arms() -> tuple[MutationArm, ...]:
    return (
        MutationArm(
            "W155-CORR-M01",
            "receipt guard becomes application-lifetime boolean",
            lambda root: _replace_once(
                root / _APPLICATION_RESULT,
                '        and getattr(orchestrator, "_audit_receipt_emitted_run_id", None) != current_run_id',
                '        and not getattr(orchestrator, "_audit_receipt_emitted", False)',
            ),
        ),
        MutationArm(
            "W155-CORR-M02",
            "input-token HTTP path bypasses credential header boundary",
            lambda root: _replace_in_function(
                root / _INPUT_TOKENS,
                "_count_request_input_tokens",
                "headers = gateway._request_headers()",
                "headers = None",
            ),
        ),
        MutationArm(
            "W155-CORR-M03",
            "text-tokenizer HTTP path bypasses credential header boundary",
            lambda root: _replace_in_function(
                root / _PROVIDER,
                "count_tokens",
                "headers = self._request_headers()",
                "headers = None",
            ),
        ),
    )


def _copy_for_mutation(root: Path, destination: Path) -> None:
    source_agent = root / "agent"
    if source_agent.is_dir():
        shutil.copytree(source_agent, destination / "agent", ignore=shutil.ignore_patterns("__pycache__"))
    for relative in (_INSTALLED_GATE,):
        source = root / relative
        if source.is_file():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _run_mutation_campaign(
    root: str | Path,
    arms: tuple[MutationArm, ...],
    *,
    schema_version: str,
) -> dict[str, object]:
    resolved = Path(root).expanduser().resolve()
    results: list[dict[str, object]] = []
    for arm in arms:
        with tempfile.TemporaryDirectory(prefix=f"{arm.arm_id.lower()}-") as temporary:
            mutant_root = Path(temporary)
            _copy_for_mutation(resolved, mutant_root)
            applied = arm.mutate(mutant_root)
            findings = check_architecture(mutant_root) if applied else []
            results.append(
                {
                    "arm_id": arm.arm_id,
                    "description": arm.description,
                    "mutation_applied": applied,
                    "detected": bool(findings),
                    "finding_rule_ids": sorted({finding.rule_id for finding in findings}),
                }
            )
    detected = sum(1 for result in results if result["detected"] is True)
    return {
        "schema_version": schema_version,
        "total": len(results),
        "detected": detected,
        "failed": len(results) - detected,
        "arms": results,
    }


def run_mutation_campaign(root: str | Path = ROOT) -> dict[str, object]:
    return _run_mutation_campaign(
        root,
        _mutation_arms(),
        schema_version="W155-MUTATION-V1",
    )


def run_corrective_mutation_campaign(root: str | Path = ROOT) -> dict[str, object]:
    return _run_mutation_campaign(
        root,
        _corrective_mutation_arms(),
        schema_version="W155-CORRECTIVE-MUTATION-V1",
    )


check_mutation_arms = run_mutation_campaign


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Wave 15.5 release-readiness architecture")
    parser.add_argument("root", nargs="?", default=str(ROOT))
    parser.add_argument("--mutation-campaign", action="store_true")
    parser.add_argument("--corrective-mutation-campaign", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    findings = check_architecture(args.root)
    for finding in findings:
        print(finding.format())
    if findings:
        return 1
    print("W155 architecture checker: PASS on canonical candidate")
    exit_code = 0
    if args.mutation_campaign:
        campaign = run_mutation_campaign(args.root)
        print(f"W155-M01..M16: {campaign['detected']}/{campaign['total']} mutants detected")
        if campaign["failed"] != 0:
            exit_code = 1
    if args.corrective_mutation_campaign:
        campaign = run_corrective_mutation_campaign(args.root)
        print(f"W155-CORR-M01..M03: {campaign['detected']}/{campaign['total']} mutants detected")
        if campaign["failed"] != 0:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
