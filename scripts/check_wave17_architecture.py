"""Deterministic, model-free structural gate for the interactive CLI.

The checker intentionally inspects ownership seams and call shapes instead of
depending on source line numbers.  It is a safety net for the product
boundary; it does not replace the focused behavioural tests.
"""

from __future__ import annotations

import ast
import re
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
    "query_git.py",
    "query_plane.py",
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


def _calls(tree: ast.AST) -> Iterable[ast.Call]:
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
    relative = f"{CLI_ROOT}/query_plane.py"
    source = _combined_source(
        root,
        relative,
        f"{CLI_ROOT}/query_executor.py",
        f"{CLI_ROOT}/query_git.py",
    )
    findings: list[ArchitectureViolation] = []
    if any(token in source for token in ("ToolInvocationGateway", "tool_invocation_gateway", "orchestrator")):
        findings.append(_violation("W17-ARCH-04", relative, "query plane calls or owns the active task gateway"))
    for token in ("shell=False", "stdin=subprocess.DEVNULL", "GIT_TERMINAL_PROMPT", "GIT_EXTERNAL_DIFF", "core.fsmonitor=false"):
        if token not in source:
            findings.append(_violation("W17-ARCH-17", relative, f"bounded Git query contract is missing {token}"))
    if not re.search(r"Queue\(maxsize=[12]\)", source) or "_result_pending" not in source:
        findings.append(_violation("W17-ARCH-17", relative, "query executor lacks one bounded unsettled-result lane"))
    query_submit = _function_source(root, f"{CLI_ROOT}/interactive_rendering.py", "submit_query")
    input_handler = _function_source(root, f"{CLI_ROOT}/interactive_admission.py", "handle_input")
    if "executor.submit" not in query_submit or "service.execute" in input_handler:
        findings.append(_violation("W17-ARCH-17", f"{CLI_ROOT}/interactive_rendering.py", "query work must leave the prompt thread through the bounded executor"))
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
    manifest_relative = f"{CLI_ROOT}/manifest.py"
    manifest = _source(root, manifest_relative)
    if "SubmissionEnvelope" in manifest or "ToolInvocationGateway" in manifest or ".grant(" in manifest:
        findings.append(_violation("W17-ARCH-09", manifest_relative, "command registry contains runtime authority instead of routing metadata"))
    input_relative = f"{CLI_ROOT}/interactive_admission.py"
    input_handler = _function_source(root, input_relative, "handle_input")
    if "controller.submit" not in _source(root, input_relative) or "execute_submission" not in _source(root, input_relative):
        findings.append(_violation("W17-ARCH-11", input_relative, "agentic input does not pass through the single controller"))
    if "application.interact" in input_handler:
        findings.append(_violation("W17-ARCH-11", input_relative, "interactive handler directly invokes the application boundary"))
    if "AGENTIC_SUBMIT" not in manifest or "PENDING_EXACT_TEXT" not in manifest or "PENDING_TYPED_PAYLOAD" not in manifest:
        findings.append(_violation("W17-ARCH-11", manifest_relative, "agentic busy-submit metadata is incomplete"))
    return findings


def _check_manifest(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    try:
        sys.path.insert(0, str(root))
        from agent.interfaces.cli.manifest import DEFAULT_COMMAND_REGISTRY

        seen: set[str] = set()
        for entry in DEFAULT_COMMAND_REGISTRY.entries:
            if entry.canonical_command_id in seen:
                findings.append(_violation("W17-ARCH-12", f"{CLI_ROOT}/manifest.py", "duplicate canonical command id"))
            seen.add(entry.canonical_command_id)
            for alias in entry.aliases:
                resolved, _ = DEFAULT_COMMAND_REGISTRY.lookup(alias)
                if resolved is None or resolved.canonical_command_id != entry.canonical_command_id:
                    findings.append(_violation("W17-ARCH-12", f"{CLI_ROOT}/manifest.py", f"alias is not accounted for: {alias}"))
            if entry.handler_owner:
                try:
                    if DEFAULT_COMMAND_REGISTRY.resolve_handler(entry) is None:
                        findings.append(_violation("W17-ARCH-12", f"{CLI_ROOT}/manifest.py", f"handler owner is unavailable: {entry.handler_owner}"))
                except (AttributeError, ImportError, ValueError, TypeError):
                    findings.append(_violation("W17-ARCH-12", f"{CLI_ROOT}/manifest.py", f"handler owner is not importable: {entry.handler_owner}"))
    except (ImportError, OSError, ValueError, TypeError) as exc:
        findings.append(_violation("W17-ARCH-12", f"{CLI_ROOT}/manifest.py", f"registry cannot be validated: {exc}"))
    return findings


def _check_worker_output(root: Path) -> list[ArchitectureViolation]:
    relative = f"{CLI_ROOT}/interactive_worker.py"
    source = _source(root, relative)
    controller = _source(root, f"{CLI_ROOT}/controller.py")
    rendering = _source(root, f"{CLI_ROOT}/interactive_rendering.py")
    stream = _source(root, f"{CLI_ROOT}/worker_stream.py")
    findings: list[ArchitectureViolation] = []
    if any(token in source for token in ("print(", "console.print", "sys.stdout", "sys.stderr")):
        findings.append(_violation("W17-ARCH-14", relative, "worker thread writes directly into the composer terminal"))
    if "InteractiveWorkerResult" not in source or "bind_worker_output" not in source:
        findings.append(_violation("W17-ARCH-14", relative, "worker output is not captured through the canonical UI seam"))
    if "redirect_stdout" in source or "redirect_stderr" in source:
        findings.append(_violation("W17-ARCH-14", relative, "worker output uses process-global stream redirection"))
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
