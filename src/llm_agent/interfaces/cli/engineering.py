"""Trusted CLI adapter helpers for Engineering."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

import llm_agent
from llm_agent.application.actions import (
    compare_practical_evaluation,
    prepare_engineering_fault,
    resolve_source_candidate_identity,
)
from llm_agent.application.actions.engineering import (
    EngineeringActionOutcome,
    describe_engineering_operation,
    engineering_operation_ids,
    list_engineering_operations,
    query_engineering_history,
    query_engineering_result,
    run_engineering,
)
from llm_agent.application.approval import ApprovalResponse
from llm_agent.application.context import AppPaths, WorkspaceContext
from llm_agent.application.home_operations import startup_home_operation
from llm_agent.application.pathing import inspect_final_path, resolve_workspace_path
from llm_agent.interfaces.cli.approval_input import parse_console_approval
from llm_agent.interfaces.cli.interactive_shell import prompt_from
from llm_agent.interfaces.cli.ui import console


def discover_source_repository() -> tuple[Path, str] | None:
    try:
        package_file = Path(llm_agent.__file__).resolve(strict=True)
        candidate_root = package_file.parent.parent
        if candidate_root.name == "src":
            candidate_root = candidate_root.parent
        candidate_root = candidate_root.resolve(strict=True)
        verifier = resolve_workspace_path(candidate_root, Path("scripts/verify_installed_package.py"), require_file=True)
        if inspect_final_path(verifier).is_link_like:
            return None
        return candidate_root, resolve_source_candidate_identity(candidate_root)
    except (OSError, RuntimeError, ValueError):
        return None


def discover_source_repository_context() -> tuple[Path, str] | None:
    """Keep the CLI discovery seam while returning only neutral source data."""
    return discover_source_repository()


def _duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parameters(raw: str | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if len(raw.encode("utf-8")) > 16_384:
        raise ValueError("Engineering parameters are too large")
    try:
        value = json.loads(raw, object_pairs_hook=_duplicates)
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise ValueError("invalid Engineering parameters JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Engineering parameters must be a JSON object")
    return value


def _interactive(args: argparse.Namespace) -> bool:
    return bool(
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and not getattr(args, "json_output", False)
        and not getattr(args, "no_input", False)
    )


def discovery_operation_views(paths: AppPaths, workspace: WorkspaceContext | None = None) -> tuple[dict[str, Any], ...]:
    """Return neutral discovery projections without starting storage lifecycle."""
    source = discover_source_repository_context()
    outcome = list_engineering_operations(
        paths,
        interactive=True,
        workspace_id=workspace.workspace_id if workspace is not None else None,
        workspace_root=workspace.root if workspace is not None else None,
        source_root=source[0] if source is not None else None,
        source_identity=source[1] if source is not None else None,
    )
    return tuple(outcome.payload["operations"])


def _run_engineering_operation(
    args: argparse.Namespace,
    paths: AppPaths,
    source: tuple[Path, str] | None,
    interactive: bool,
    print_json: Callable[[Any], None],
) -> int:
    if getattr(args, "workspace", None):
        raise ValueError("--workspace is not valid for a source_repository Engineering operation")
    operation_id = _operation_id(args, interactive)
    parameters = _parameters(getattr(args, "params_json", None))
    fault_input = prepare_engineering_fault(getattr(args, "fault_json", None), parameters)
    if fault_input.fault is not None:
        parameters["fault"] = fault_input.fault

    allow_external = bool(getattr(args, "allow_external", False))
    allow_network = bool(getattr(args, "allow_network", False))
    allow_external, allow_network, denied = _collect_interactive_permissions(
        operation_id,
        paths,
        interactive,
        source,
        allow_external,
        allow_network,
    )
    if denied == "external":
        allow_external = False
    elif denied == "network":
        allow_network = False

    def startup_guard(execute: Callable[[], EngineeringActionOutcome]) -> EngineeringActionOutcome:
        with startup_home_operation(paths):
            return execute()

    result = run_engineering(
        operation_id,
        parameters,
        paths,
        interactive=interactive,
        allow_external=allow_external,
        allow_network=allow_network,
        allow_fault_injection=fault_input.requires_inject_fault_permission,
        source_root=source[0] if source is not None else None,
        source_identity=source[1] if source is not None else None,
        denied_permission=denied,
        run_guard=startup_guard,
    )
    return _emit(result.payload, args, print_json, result.exit_code)


def run_engineering_cli(args: argparse.Namespace, *, print_json: Callable[[Any], None]) -> int:
    paths = AppPaths.discover(app_home=getattr(args, "home", None))
    source = discover_source_repository_context()
    interactive = _interactive(args)
    command = args.test_command

    if command == "compare":
        outcome = compare_practical_evaluation(source[0] if source is not None else None)
        return _emit(outcome.document, args, print_json, outcome.exit_code)
    if command == "list":
        result = list_engineering_operations(
            paths,
            interactive=interactive,
            source_root=source[0] if source is not None else None,
            source_identity=source[1] if source is not None else None,
        )
        return _emit(result.payload, args, print_json, result.exit_code)
    if command == "describe":
        result = describe_engineering_operation(
            args.operation_id,
            paths,
            interactive=interactive,
            source_root=source[0] if source is not None else None,
            source_identity=source[1] if source is not None else None,
        )
        return _emit(result.payload, args, print_json, result.exit_code)
    if command == "history":
        result = query_engineering_history(
            paths,
            interactive=interactive,
            source_root=source[0] if source is not None else None,
            source_identity=source[1] if source is not None else None,
        )
        return _emit(result.payload, args, print_json, result.exit_code)
    if command == "result":
        result = query_engineering_result(
            args.run_id,
            paths,
            interactive=interactive,
            source_root=source[0] if source is not None else None,
            source_identity=source[1] if source is not None else None,
        )
        return _emit(result.payload, args, print_json, result.exit_code)
    return _run_engineering_operation(args, paths, source, interactive, print_json)


def _operation_id(args: argparse.Namespace, interactive: bool) -> str:
    operation_id = getattr(args, "operation_id", None)
    if operation_id is not None:
        return str(operation_id)
    if not interactive:
        raise ValueError("operation_id is required in headless mode")
    operations: tuple[str, ...] = engineering_operation_ids()
    if not operations:
        raise ValueError("no Engineering operations are available")
    for index, item in enumerate(operations, 1):
        console.print(f"{index}. {item}", markup=False)
    answer = prompt_from(console, f"Operation [1-{len(operations)}, blank to cancel]: ")
    if not isinstance(answer, str) or not answer.isascii() or not answer.isdecimal():
        raise ValueError("Engineering operation selection cancelled")
    if not 1 <= int(answer) <= len(operations):
        raise ValueError("Engineering operation selection cancelled")
    return operations[int(answer) - 1]


def _collect_interactive_permissions(
    operation_id: str,
    paths: AppPaths,
    interactive: bool,
    source: tuple[Path, str] | None,
    allow_external: bool,
    allow_network: bool,
) -> tuple[bool, bool, str | None]:
    if not interactive:
        return allow_external, allow_network, None
    view = describe_engineering_operation(
        operation_id,
        paths,
        interactive=interactive,
        source_root=source[0] if source is not None else None,
        source_identity=source[1] if source is not None else None,
    ).payload
    effects = view.get("effects")
    if not isinstance(effects, Mapping):
        return allow_external, allow_network, None
    required = [
        (effects.get("managed_external_process") is True, allow_external, "external", "Allow external process execution? [yes/no]: "),
        (effects.get("network") is True, allow_network, "network", "Allow network use? [yes/no]: "),
    ]
    grants = [allow_external, allow_network]
    for index, (needed, already_allowed, kind, label) in enumerate(required):
        if needed and not already_allowed and parse_console_approval(str(prompt_from(console, label) or "")) is not ApprovalResponse.APPROVE:
            return grants[0], grants[1], kind
        if needed:
            grants[index] = True
    return grants[0], grants[1], None


def _emit(payload: Mapping[str, Any], args: argparse.Namespace, print_json: Callable[[Any], None], code: int) -> int:
    if getattr(args, "json_output", False):
        print_json(dict(payload))
    else:
        console.print_json(data=dict(payload))
    return code


__all__ = ["discovery_operation_views", "discover_source_repository", "run_engineering_cli"]
