"""Deterministic, model-free structural gate for the interactive CLI.

The checker intentionally inspects ownership seams and call shapes instead of
depending on source line numbers.  It is a safety net for the product
boundary; it does not replace the focused behavioural tests.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
CLI_ROOT = "agent/interfaces/cli"
PRIMARY_FILES = (
    "app.py",
    "attention.py",
    "command_handlers.py",
    "commands.py",
    "controller.py",
    "interactive_admission.py",
    "interactive_rendering.py",
    "interactive_resources.py",
    "interactive_session.py",
    "interactive_worker.py",
    "query_executor.py",
    "ui_plane.py",
)
STDIN_COMPATIBILITY_FILES = {"interactive_shell.py"}
FORBIDDEN_UI_IMPORTS = {
    "agent.evaluation",
    "agent.llm.providers",
}


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    rule_id: str
    path: str
    detail: str

    def format(self) -> str:
        return f"{self.rule_id} {self.path}: {self.detail}"


def _source(root: Path, relative: str) -> str:
    try:
        return (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _tree(root: Path, relative: str) -> ast.Module | None:
    source = _source(root, relative)
    if not source:
        return None
    try:
        return ast.parse(source, filename=relative)
    except SyntaxError:
        return None


def _violation(rule_id: str, path: str, detail: str) -> ArchitectureViolation:
    return ArchitectureViolation(rule_id, path, detail)


def _calls(tree: ast.AST | None) -> Iterable[ast.Call]:
    if tree is None:
        return ()
    return (node for node in ast.walk(tree) if isinstance(node, ast.Call))


def _call_name(node: ast.Call) -> str:
    value: ast.AST = node.func
    parts: list[str] = []
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def _function(tree: ast.AST | None, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    if tree is None:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _function_source(root: Path, relative: str, name: str) -> str:
    tree = _tree(root, relative)
    function = _function(tree, name)
    if function is None:
        return ""
    source = _source(root, relative)
    lines = source.splitlines()
    return "\n".join(lines[function.lineno - 1 : function.end_lineno or function.lineno])


@dataclass(frozen=True, slots=True)
class _FunctionRef:
    relative: str
    function: ast.FunctionDef | ast.AsyncFunctionDef


def _module_relative(root: Path, module_name: str) -> str | None:
    if not module_name:
        return None
    module_path = root.joinpath(*module_name.split("."))
    for candidate in (module_path.with_suffix(".py"), module_path / "__init__.py"):
        if candidate.is_file():
            return candidate.relative_to(root).as_posix()
    return None


def _qualified_import_name(relative: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    module_parts = relative.removesuffix(".py").replace("\\", "/").split("/")
    package = module_parts[:-1]
    if node.level > 1:
        package = package[: -(node.level - 1)] if node.level - 1 <= len(package) else []
    if node.module:
        package.extend(node.module.split("."))
    return ".".join(package)


def _import_target(
    root: Path,
    relative: str,
    node: ast.ImportFrom,
    imported_name: str,
) -> tuple[str, bool] | None:
    module_name = _qualified_import_name(relative, node)
    submodule = _module_relative(root, f"{module_name}.{imported_name}" if module_name else imported_name)
    if submodule is not None:
        return submodule, True
    module = _module_relative(root, module_name)
    if module is None:
        return None
    return module, False


def _from_import_binding(tree: ast.Module, local_name: str) -> tuple[ast.ImportFrom, ast.alias] | None:
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name != "*" and (alias.asname or alias.name) == local_name:
                return node, alias
    return None


def _plain_import_binding(tree: ast.Module, local_name: str) -> ast.alias | None:
    for node in tree.body:
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            if (alias.asname or alias.name.split(".")[0]) == local_name:
                return alias
    return None


def _resolve_from_binding(
    root: Path,
    relative: str,
    binding: tuple[ast.ImportFrom, ast.alias],
    function_name: str,
    seen: tuple[tuple[str, str], ...] = (),
) -> _FunctionRef | None:
    node, alias = binding
    target = _import_target(root, relative, node, alias.name)
    if target is None:
        return None
    target_relative, is_submodule = target
    target_name = function_name if is_submodule else alias.name
    return _resolve_exported_function(root, target_relative, target_name, seen)


def _resolve_plain_binding(
    root: Path,
    alias: ast.alias | None,
    function_name: str,
    seen: tuple[tuple[str, str], ...] = (),
) -> _FunctionRef | None:
    if alias is None:
        return None
    imported_relative = _module_relative(root, alias.name)
    if imported_relative is None:
        return None
    return _resolve_exported_function(root, imported_relative, function_name, seen)


def _resolve_imported_function(
    root: Path,
    relative: str,
    tree: ast.Module,
    name: str,
    seen: tuple[tuple[str, str], ...],
) -> _FunctionRef | None:
    from_binding = _from_import_binding(tree, name)
    if from_binding is not None:
        return _resolve_from_binding(root, relative, from_binding, name, seen)
    return _resolve_plain_binding(root, _plain_import_binding(tree, name), name, seen)


def _resolve_exported_function(
    root: Path,
    relative: str,
    name: str,
    seen: tuple[tuple[str, str], ...] = (),
) -> _FunctionRef | None:
    marker = (relative, name)
    if marker in seen:
        return None
    tree = _tree(root, relative)
    function = _function(tree, name)
    if function is not None:
        return _FunctionRef(relative, function)
    if tree is None:
        return None
    return _resolve_imported_function(root, relative, tree, name, (*seen, marker))


def _resolve_bare_submission_call(
    root: Path,
    relative: str,
    tree: ast.Module,
    local_name: str,
) -> _FunctionRef | None:
    local_function = _function(tree, local_name)
    if local_function is not None:
        return _FunctionRef(relative, local_function)
    return _resolve_imported_function(root, relative, tree, local_name, ())


def _resolve_module_alias(
    root: Path,
    relative: str,
    tree: ast.Module,
    module_alias: str,
    function_name: str,
) -> _FunctionRef | None:
    from_binding = _from_import_binding(tree, module_alias)
    if from_binding is not None:
        node, alias = from_binding
        target = _import_target(root, relative, node, alias.name)
        if target is not None:
            target_relative, _ = target
            return _resolve_exported_function(root, target_relative, function_name)
        return None
    return _resolve_plain_binding(root, _plain_import_binding(tree, module_alias), function_name)


def _resolve_module_submission_call(
    root: Path,
    relative: str,
    tree: ast.Module,
    module_alias: str,
) -> _FunctionRef | None:
    return _resolve_module_alias(root, relative, tree, module_alias, "submit_query")


def _resolve_submission_call(root: Path, relative: str, call: ast.Call) -> _FunctionRef | None:
    call_parts = _call_name(call).split(".")
    if not call_parts or call_parts[-1] != "submit_query":
        return None
    tree = _tree(root, relative)
    if tree is None:
        return None
    if len(call_parts) == 1:
        return _resolve_bare_submission_call(root, relative, tree, call_parts[0])
    return _resolve_module_submission_call(root, relative, tree, call_parts[-2])


_QUERY_SERVICE_RECEIVERS = frozenset({"service", "query_service", "workspace_query_service"})
_QUERY_SERVICE_METHODS = frozenset({"execute", "list_files", "read_file", "find", "git_status", "diff"})


def _direct_query_calls(tree: ast.AST | None) -> list[ast.Call]:
    if tree is None:
        return []
    findings: list[ast.Call] = []
    for call in _calls(tree):
        if not isinstance(call.func, ast.Attribute) or call.func.attr not in _QUERY_SERVICE_METHODS:
            continue
        call_name = _call_name(call)
        if any(part in _QUERY_SERVICE_RECEIVERS for part in call_name.split(".")):
            findings.append(call)
    return findings


def _reachable_functions(tree: ast.Module | None, entry_name: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    if tree is None:
        return []
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    pending = [entry_name]
    seen: set[str] = set()
    reachable: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    while pending:
        name = pending.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        function = functions[name]
        reachable.append(function)
        for call in _calls(function):
            if isinstance(call.func, ast.Name) and call.func.id in functions:
                pending.append(call.func.id)
    return reachable


def _bounded_submission_call(function: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.Call | None:
    for call in _calls(function):
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "submit":
            continue
        if not isinstance(call.func.value, ast.Name) or call.func.value.id not in {"executor", "query_executor"}:
            continue
        for keyword in call.keywords:
            if keyword.arg != "execute" or not isinstance(keyword.value, ast.Attribute):
                continue
            if keyword.value.attr == "execute" and isinstance(keyword.value.value, ast.Name):
                if keyword.value.value.id in _QUERY_SERVICE_RECEIVERS:
                    return call
    return None


def _combined_source(root: Path, *relatives: str) -> str:
    return "\n".join(_source(root, relative) for relative in relatives)


def _check_primary_stdin(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for filename in PRIMARY_FILES:
        relative = f"{CLI_ROOT}/{filename}"
        tree = _tree(root, relative)
        if tree is None:
            findings.append(_violation("W17-ARCH-01", relative, "module is missing or unparsable"))
            continue
        for call in _calls(tree):
            name = _call_name(call)
            if name in {"input", "console.input", "sys.stdin.readline", "sys.stdin.read"}:
                findings.append(_violation("W17-ARCH-01", relative, "primary chat path reads normal stdin directly"))
    return findings


def _check_single_execution_owner(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    app = _source(root, f"{CLI_ROOT}/app.py")
    session = _combined_source(
        root,
        f"{CLI_ROOT}/interactive_session.py",
        f"{CLI_ROOT}/interactive_resources.py",
    )
    controller = _source(root, f"{CLI_ROOT}/controller.py")
    if app.count("InteractiveExecutionController()") != 0 or session.count("InteractiveExecutionController()") != 1:
        findings.append(_violation("W17-ARCH-02", f"{CLI_ROOT}/interactive_session.py", "interactive controller must have one product construction site"))
    if controller.count("class InteractiveExecutionController") != 1 or controller.count("Thread(") != 1:
        findings.append(_violation("W17-ARCH-02", f"{CLI_ROOT}/controller.py", "agentic execution must have one bounded worker owner"))
    if "daemon=False" not in controller:
        findings.append(_violation("W17-ARCH-02", f"{CLI_ROOT}/controller.py", "agent worker must settle as a non-daemon thread"))
    return findings


def _check_ui_projection(root: Path) -> list[ArchitectureViolation]:
    relative = f"{CLI_ROOT}/ui_plane.py"
    source = _source(root, relative)
    forbidden = ("agent_state", "ToolInvocationGateway", "tool_invocation_gateway", "orchestrator")
    if any(token in source for token in forbidden):
        return [_violation("W17-ARCH-03", relative, "live UI projection reaches mutable task internals")]
    return []


def _check_query_plane(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/application_services/queries.py"
    source = _combined_source(
        root,
        relative,
        "agent/application_services/query_git.py",
        f"{CLI_ROOT}/query_executor.py",
    )
    findings: list[ArchitectureViolation] = []
    if any(token in source for token in ("ToolInvocationGateway", "tool_invocation_gateway", "orchestrator")):
        findings.append(_violation("W17-ARCH-04", relative, "query plane calls or owns the active task gateway"))
    for token in ("shell=False", "stdin=subprocess.DEVNULL", "GIT_TERMINAL_PROMPT", "GIT_EXTERNAL_DIFF", "core.fsmonitor=false"):
        if token not in source:
            findings.append(_violation("W17-ARCH-17", relative, f"bounded Git query contract is missing {token}"))
    executor_tree = _tree(root, f"{CLI_ROOT}/query_executor.py")
    executor_class = next(
        (
            node
            for node in ast.walk(executor_tree)
            if isinstance(node, ast.ClassDef) and node.name == "BoundedQueryExecutor"
        ),
        None,
    ) if executor_tree is not None else None
    executor_init = _function(executor_class, "__init__")
    has_bounded_lane = any(
        _call_name(call) == "Queue"
        and any(
            keyword.arg == "maxsize"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value in {1, 2}
            for keyword in call.keywords
        )
        for call in _calls(executor_init)
    )
    has_result_pending = any(
        isinstance(target, ast.Attribute)
        and target.attr == "_result_pending"
        for node in ast.walk(executor_init)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
    ) if executor_init is not None else False
    if not has_bounded_lane or not has_result_pending:
        findings.append(_violation("W17-ARCH-17", relative, "query executor lacks one bounded unsettled-result lane"))
    admission_relative = f"{CLI_ROOT}/interactive_admission.py"
    admission_tree = _tree(root, admission_relative)
    prompt_functions = _reachable_functions(admission_tree, "handle_input")
    submission_calls = [
        call
        for function in prompt_functions
        for call in _calls(function)
        if _call_name(call).split(".")[-1] == "submit_query"
    ]
    if not submission_calls:
        findings.append(
            _violation(
                "W17-ARCH-17",
                admission_relative,
                "interactive input has no query submission boundary",
            )
        )
        return findings
    if any(_direct_query_calls(function) for function in prompt_functions):
        findings.append(
            _violation(
                "W17-ARCH-17",
                admission_relative,
                "query work executes directly on the prompt thread",
            )
        )
    for submission_call in submission_calls:
        submission = _resolve_submission_call(root, admission_relative, submission_call)
        if submission is None:
            findings.append(
                _violation(
                    "W17-ARCH-17",
                    admission_relative,
                    "query submission owner cannot be resolved through the CLI boundary",
                )
            )
            continue
        if _direct_query_calls(submission.function) or _bounded_submission_call(submission.function) is None:
            findings.append(
                _violation(
                    "W17-ARCH-17",
                    submission.relative,
                    "query work must leave the prompt thread through the bounded executor",
                )
            )
    return findings


def _check_approval_and_shell(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    attention = _source(root, f"{CLI_ROOT}/attention.py")
    if any(token in attention for token in ("console.input", "sys.stdin", "input(")):
        findings.append(_violation("W17-ARCH-05", f"{CLI_ROOT}/attention.py", "approval broker reads a competing stdin"))
    shell_relative = f"{CLI_ROOT}/interactive_shell.py"
    shell = _source(root, shell_relative)
    if "mouse_support=False" not in shell or "mouse_support=True" in shell:
        findings.append(_violation("W17-ARCH-06", shell_relative, "product shell must not capture the mouse by default"))
    if any(token in shell for token in ("full_screen=True", "alternate_screen", "enable_page_navigation=True")):
        findings.append(_violation("W17-ARCH-06", shell_relative, "product shell must preserve native scrollback"))
    return findings


def _check_pending_and_cancel(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    controller_relative = f"{CLI_ROOT}/controller.py"
    controller = _source(root, controller_relative)
    if "def send_pending" not in controller or "self.pending.consume" not in controller:
        findings.append(_violation("W17-ARCH-07", controller_relative, "pending send must be explicit and consume-on-accept"))
    if "self.pending.consume" in _source(root, f"{CLI_ROOT}/app.py"):
        findings.append(_violation("W17-ARCH-07", f"{CLI_ROOT}/app.py", "chat path auto-consumes pending follow-ups"))
    cancel_relative = f"{CLI_ROOT}/command_handlers.py"
    cancel = _function_source(root, cancel_relative, "cancel")
    if not cancel:
        cancel_relative = f"{CLI_ROOT}/interactive_commands.py"
        cancel = _function_source(root, cancel_relative, "cancel")
    if "request_cancel" not in cancel or any(token in cancel for token in ("cancel_task", "checkpoint", "terminal")):
        findings.append(_violation("W17-ARCH-13", cancel_relative, "UI cancel must be request-only"))
    app = _source(root, f"{CLI_ROOT}/app.py")
    if "orchestrator.cancel_task" in app or "orchestrator.checkpoint" in app:
        findings.append(_violation("W17-ARCH-13", f"{CLI_ROOT}/app.py", "chat cancellation calls terminal/checkpoint logic"))
    return findings


def _check_headless_and_boundaries(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    app_relative = f"{CLI_ROOT}/app.py"
    run_once = _function_source(root, app_relative, "_run_once")
    run_chat = _function_source(root, app_relative, "_run_chat")
    session = _combined_source(
        root,
        f"{CLI_ROOT}/interactive_session.py",
        f"{CLI_ROOT}/interactive_resources.py",
    )
    if "InteractiveShell" in run_once or "PromptSession" in run_once:
        findings.append(_violation("W17-ARCH-08", app_relative, "headless run path initializes prompt-toolkit"))
    if "InteractiveTTYRequiredError" not in run_chat or run_chat.find("InteractiveTTYRequiredError") > run_chat.find("_create_application") >= 0:
        findings.append(_violation("W17-ARCH-15", app_relative, "chat TTY gate is not before application/prompt setup"))
    if "shell_enabled = interactive and real_tty" not in session or "get_shell() if shell_enabled else None" not in session:
        findings.append(_violation("W17-ARCH-15", f"{CLI_ROOT}/interactive_session.py", "PTK shell is not TTY-gated"))
    for relative in (f"{CLI_ROOT}/{name}" for name in PRIMARY_FILES):
        tree = _tree(root, relative)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [alias.name for alias in node.names]
                module = node.module or "" if isinstance(node, ast.ImportFrom) else ""
                for imported in [*modules, module]:
                    if any(imported == prefix or imported.startswith(prefix + ".") for prefix in FORBIDDEN_UI_IMPORTS):
                        findings.append(_violation("W17-ARCH-10", relative, f"unnecessary live/evaluation import: {imported}"))
    return findings


def _check_agentic_boundary(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    registry_relative = f"{CLI_ROOT}/action_registry.py"
    registry = _source(root, registry_relative)
    if "SubmissionEnvelope" in registry or "ToolInvocationGateway" in registry or ".grant(" in registry:
        findings.append(_violation("W17-ARCH-09", registry_relative, "command registry contains runtime authority instead of routing metadata"))
    input_relative = f"{CLI_ROOT}/interactive_admission.py"
    input_handler = _function_source(root, input_relative, "handle_input")
    if "controller.submit" not in _source(root, input_relative) or "execute_submission" not in _source(root, input_relative):
        findings.append(_violation("W17-ARCH-11", input_relative, "agentic input does not pass through the single controller"))
    if "application.interact" in input_handler:
        findings.append(_violation("W17-ARCH-11", input_relative, "interactive handler directly invokes the application boundary"))
    if "AGENTIC_SUBMIT" not in registry or "PENDING_EXACT_TEXT" not in registry or "PENDING_TYPED_PAYLOAD" not in registry:
        findings.append(_violation("W17-ARCH-11", registry_relative, "agentic busy-submit metadata is incomplete"))
    return findings


def _check_manifest(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    try:
        sys.path.insert(0, str(root))
        from agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY

        seen: set[str] = set()
        for binding in DEFAULT_CLI_ACTION_REGISTRY._bindings:
            if binding.action_id in seen:
                findings.append(_violation("W17-ARCH-12", f"{CLI_ROOT}/action_registry.py", "duplicate canonical action id"))
            seen.add(binding.action_id)
            for alias in binding.aliases:
                resolved = DEFAULT_CLI_ACTION_REGISTRY.match(" ".join(alias))
                if resolved is None or resolved.action_id != binding.action_id:
                    findings.append(_violation("W17-ARCH-12", f"{CLI_ROOT}/action_registry.py", f"alias is not accounted for: {' '.join(alias)}"))
            if binding.handler_owner:
                try:
                    if DEFAULT_CLI_ACTION_REGISTRY.resolve_handler(binding) is None:
                        findings.append(_violation("W17-ARCH-12", f"{CLI_ROOT}/action_registry.py", f"handler owner is unavailable: {binding.handler_owner}"))
                except (AttributeError, ImportError, ValueError, TypeError):
                    findings.append(_violation("W17-ARCH-12", f"{CLI_ROOT}/action_registry.py", f"handler owner is not importable: {binding.handler_owner}"))
    except (ImportError, OSError, ValueError, TypeError) as exc:
        findings.append(_violation("W17-ARCH-12", f"{CLI_ROOT}/action_registry.py", f"registry cannot be validated: {exc}"))
    return findings


def _check_worker_thread_output(relative: str, source: str) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    if any(token in source for token in ("print(", "console.print", "sys.stdout", "sys.stderr")):
        findings.append(_violation("W17-ARCH-14", relative, "worker thread writes directly into the composer terminal"))
    if "InteractiveWorkerResult" not in source or "bind_worker_output" not in source:
        findings.append(_violation("W17-ARCH-14", relative, "worker output is not captured through the canonical UI seam"))
    if "redirect_stdout" in source or "redirect_stderr" in source:
        findings.append(_violation("W17-ARCH-14", relative, "worker output uses process-global stream redirection"))
    return findings


def _check_worker_stream(
    relative: str,
    source: str,
    controller: str,
    rendering: str,
    stream: str,
) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    if (
        "BoundedWorkerStream" not in controller
        or "stream_channel.publish(envelope.run_generation" not in source
        or '"assistant"' not in source
        or '"diagnostic"' not in source
    ):
        findings.append(_violation("W17-ARCH-18", relative, "worker output lacks the bounded typed stream channel"))
    if (
        "finish_generation(envelope.run_generation)" not in controller
        or "poll_stream" not in controller
        or "drain_worker_stream" not in rendering
        or "render_worker_stream" not in rendering
    ):
        findings.append(_violation("W17-ARCH-18", f"{CLI_ROOT}/interactive_rendering.py", "UI stream consumer is not generation-bound"))
    if "MAX_WORKER_STREAM_CHARS" not in stream or "MAX_WORKER_STREAM_ITEMS" not in stream:
        findings.append(_violation("W17-ARCH-18", f"{CLI_ROOT}/worker_stream.py", "worker stream bounds are not explicit"))
    if "StringIO" in source:
        findings.append(_violation("W17-ARCH-18", relative, "worker stream must not accumulate an unbounded terminal buffer"))
    return findings


def _check_worker_stdin(root: Path, relative: str) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative_file in (f"{CLI_ROOT}/{name}" for name in PRIMARY_FILES):
        if relative_file == relative:
            continue
        tree = _tree(root, relative_file)
        if tree is None:
            continue
        for call in _calls(tree):
            if _call_name(call) in {"input", "console.input", "sys.stdin.readline", "sys.stdin.read"}:
                findings.append(_violation("W17-ARCH-16", relative_file, "interactive handler retains a direct stdin reader"))
    return findings


def _check_worker_output(root: Path) -> list[ArchitectureViolation]:
    relative = f"{CLI_ROOT}/interactive_worker.py"
    source = _source(root, relative)
    controller = _source(root, f"{CLI_ROOT}/controller.py")
    rendering = _source(root, f"{CLI_ROOT}/interactive_rendering.py")
    stream = _source(root, f"{CLI_ROOT}/worker_stream.py")
    findings = _check_worker_thread_output(relative, source)
    findings.extend(_check_worker_stream(relative, source, controller, rendering, stream))
    findings.extend(_check_worker_stdin(root, relative))
    return findings


def check_architecture(root: Path = ROOT) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for check in (
        _check_primary_stdin,
        _check_single_execution_owner,
        _check_ui_projection,
        _check_query_plane,
        _check_approval_and_shell,
        _check_pending_and_cancel,
        _check_headless_and_boundaries,
        _check_agentic_boundary,
        _check_manifest,
        _check_worker_output,
    ):
        findings.extend(check(root))
    return findings


def main() -> int:
    findings = check_architecture()
    if findings:
        for finding in findings:
            print(finding.format())
        return 1
    print("W17_ARCHITECTURE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
