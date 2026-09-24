"""Cumulative static architecture checks for the Engineering control plane."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINEERING = ROOT / "agent" / "engineering"
FORBIDDEN_CORE_PREFIXES = (
    "agent.application",
    "agent.llm",
    "agent.planning",
    "agent.discovery",
    "agent.mcp",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
    return values


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.AST:
    path = ROOT / relative
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _call_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def _all_calls_have_keyword(tree: ast.AST, names: set[str], keyword: str, value: object) -> bool:
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id in names) or (isinstance(node.func, ast.Attribute) and node.func.attr in names))]
    return bool(calls) and all(any(item.arg == keyword and isinstance(item.value, ast.Constant) and item.value.value == value for item in node.keywords) for node in calls)


def _all_calls_have_keyword_in_sources(relative_paths: tuple[str, ...], names: set[str], keyword: str, value: object) -> bool:
    calls: list[ast.Call] = []
    for relative in relative_paths:
        tree = _tree(relative)
        calls.extend(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id in names)
                or (isinstance(node.func, ast.Attribute) and node.func.attr in names)
            )
        )
    return bool(calls) and all(
        any(
            item.arg == keyword
            and isinstance(item.value, ast.Constant)
            and item.value.value == value
            for item in node.keywords
        )
        for node in calls
    )


def _append_missing(problems: list[str], condition: bool, code: str, detail: str) -> None:
    if not condition:
        problems.append(f"{code}: {detail}")


def _attribute_parts(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def _parameter_default(
    function: ast.FunctionDef | ast.AsyncFunctionDef | None,
    name: str,
) -> tuple[bool, ast.AST | None]:
    """Return whether a parameter exists and its positional/keyword default."""

    if function is None:
        return False, None
    positional = [*function.args.posonlyargs, *function.args.args]
    positional_offset = len(positional) - len(function.args.defaults)
    for index, argument in enumerate(positional):
        if argument.arg == name:
            default = (
                function.args.defaults[index - positional_offset]
                if index >= positional_offset
                else None
            )
            return True, default
    for argument, default in zip(
        function.args.kwonlyargs,
        function.args.kw_defaults,
        strict=True,
    ):
        if argument.arg == name:
            return True, default
    return False, None


def _named_function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ),
        None,
    )


def _named_class(tree: ast.AST, name: str) -> ast.ClassDef | None:
    return next(
        (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == name),
        None,
    )


def _class_method(
    tree: ast.AST,
    class_name: str,
    method_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    owner = _named_class(tree, class_name)
    if owner is None:
        return None
    return next(
        (
            node
            for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == method_name
        ),
        None,
    )


def _reachable_function_nodes(
    tree: ast.AST,
    roots: tuple[str, ...],
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]:
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    pending = list(roots)
    seen: set[str] = set()
    reachable: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        function = functions.get(name)
        if function is None:
            continue
        seen.add(name)
        reachable.append(function)
        pending.extend(_call_names(function) - seen)
    return tuple(reachable)


def _string_constants(node: ast.AST) -> set[str]:
    return {
        item.value
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }


def _has_exception_handler(node: ast.AST, names: set[str]) -> bool:
    for handler in ast.walk(node):
        if not isinstance(handler, ast.ExceptHandler) or handler.type is None:
            continue
        types = handler.type.elts if isinstance(handler.type, ast.Tuple) else (handler.type,)
        if any(isinstance(item, ast.Name) and item.id in names for item in types):
            return True
    return False


def _enum_string_values(node: ast.ClassDef | None) -> set[str]:
    if node is None:
        return set()
    values: set[str] = set()
    for statement in node.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
            values.add(statement.value.value)
    return values


def _keyword_constant(call: ast.Call, name: str) -> object:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return _MISSING


def _has_keyword(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(item, ast.Call) and any(keyword.arg == name for keyword in item.keywords)
        for item in ast.walk(node)
    )


def _call_passes_name_as_keyword(node: ast.AST, callee: str, keyword: str, value: str) -> bool:
    return any(
        isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
        and item.func.id == callee
        and any(
            argument.arg == keyword
            and isinstance(argument.value, ast.Name)
            and argument.value.id == value
            for argument in item.keywords
        )
        for item in ast.walk(node)
    )


def _imports_name_from(tree: ast.AST, module: str, name: str) -> bool:
    return any(
        isinstance(item, ast.ImportFrom)
        and item.module == module
        and any(alias.name == name and alias.asname in (None, name) for alias in item.names)
        for item in ast.walk(tree)
    )


def _bounded_reference_projection(node: ast.AST | None) -> bool:
    if node is None or "label" in _string_constants(node) or _has_attribute(node, "label"):
        return False
    projections = [
        statement.value
        for statement in ast.walk(node)
        if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Dict)
    ]
    if len(projections) != 1:
        return False
    projection = projections[0]
    fields = {
        key.value: value
        for key, value in zip(projection.keys, projection.values, strict=True)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    if set(fields) != {"owner", "kind", "reference_id", "sha256"}:
        return False
    for name in ("owner", "kind", "reference_id"):
        value = fields[name]
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_bounded"
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Attribute)
            and isinstance(value.args[0].value, ast.Name)
            and value.args[0].value.id == "reference"
            and value.args[0].attr == name
        ):
            return False
    digest = fields["sha256"]
    return (
        isinstance(digest, ast.Attribute)
        and isinstance(digest.value, ast.Name)
        and digest.value.id == "reference"
        and digest.attr == "sha256"
    )


_MISSING = object()


def _contains_name(node: ast.AST, name: str) -> bool:
    return any(isinstance(item, ast.Name) and item.id == name for item in ast.walk(node))


def _has_attribute(node: ast.AST, name: str) -> bool:
    return any(isinstance(item, ast.Attribute) and item.attr == name for item in ast.walk(node))


def _model_service_completion(call: ast.Call) -> bool:
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "complete"
        and _contains_name(call.func.value, "ModelCallService")
    )


def _semantic_owner_is_canonical() -> bool:
    """Prove the semantic Discovery seam without prescribing old source text."""

    semantic = _tree("agent/discovery/semantic.py")
    provider_factory = _tree("agent/llm/providers/factory.py")
    decision_contract = _tree("agent/llm/decision_contract.py")
    model_call = _tree("agent/runtime/model_call.py")

    discovery_owner = next(
        (
            node
            for node in ast.walk(semantic)
            if isinstance(node, ast.ClassDef) and node.name == "SemanticCommandDiscovery"
        ),
        None,
    )
    init = _named_function(discovery_owner, "__init__") if discovery_owner is not None else None
    gateway = _named_function(semantic, "_gateway")
    request_builder = _named_function(semantic, "_model_request")
    completion = _named_function(semantic, "_complete_model_call")
    rerank = _named_function(semantic, "rerank")
    validator = _named_function(semantic, "validate_command_discovery_response")
    factory = _named_function(semantic, "create_model_gateway")

    gateway_factory_present, default_gateway_factory = _parameter_default(
        init,
        "gateway_factory",
    )
    stores_factory = bool(
        init is not None
        and any(
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute)
                and target.attr == "gateway_factory"
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                for target in node.targets
            )
            for node in ast.walk(init)
        )
    )
    calls_factory_seam = bool(
        gateway is not None
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "gateway_factory"
            for node in ast.walk(gateway)
        )
    )

    imported_provider_modules = {
        target.id
        for node in (ast.walk(factory) if factory is not None else ())
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "import_module"
        and len(node.value.args) == 1
        and isinstance(node.value.args[0], ast.Constant)
        and node.value.args[0].value == "agent.llm.providers"
    }
    delegates_to_provider_factory = bool(
        factory is not None
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_model_gateway"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in imported_provider_modules
            for node in ast.walk(factory)
        )
    )
    provider_factory_owner = _named_function(provider_factory, "create_model_gateway") is not None
    model_service_class = next(
        (
            node
            for node in ast.walk(model_call)
            if isinstance(node, ast.ClassDef) and node.name == "ModelCallService"
        ),
        None,
    )
    model_service_owner = bool(
        model_service_class is not None
        and _named_function(model_service_class, "complete") is not None
    )

    model_service_imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "agent.runtime.model_call"
        and any(item.name == "ModelCallService" for item in node.names)
        for node in ast.walk(semantic)
    )
    model_service_call = bool(
        completion is not None
        and model_service_imported
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "for_context"
            and _contains_name(node.func.value, "ModelCallService")
            for node in ast.walk(completion)
        )
        and any(
            isinstance(node, ast.Call) and _model_service_completion(node)
            for node in ast.walk(completion)
        )
    )
    one_call_limit = bool(
        completion is not None
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "RuntimeLimits"
            and any(
                keyword.arg == "max_model_calls"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == 1
                for keyword in node.keywords
            )
            for node in ast.walk(completion)
        )
    )
    context_binds_gateway = bool(
        completion is not None
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "TaskExecutionContext"
            and any(
                keyword.arg == "model_gateway"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "gateway"
                for keyword in node.keywords
            )
            for node in ast.walk(completion)
        )
    )

    contract_owner = next(
        (
            node
            for node in ast.walk(decision_contract)
            if isinstance(node, ast.ClassDef) and node.name == "ModelRequestContract"
        ),
        None,
    )
    contract_member = bool(
        contract_owner is not None
        and any(
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == "COMMAND_DISCOVERY"
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
            )
            for node in ast.walk(contract_owner)
        )
    )
    request_contract_bound = bool(
        request_builder is not None
        and any(
            isinstance(node, ast.Call)
            and any(
                keyword.arg == "request_contract"
                and _attribute_parts(keyword.value)[-2:]
                == ("ModelRequestContract", "COMMAND_DISCOVERY")
                and len(_attribute_parts(keyword.value)) >= 3
                for keyword in node.keywords
            )
            for node in ast.walk(request_builder)
        )
        and contract_member
    )

    bounded_payload = bool(
        any(
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "MAX_COMMAND_DISCOVERY_PAYLOAD_BYTES" for target in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
            and node.value.value > 0
            for node in ast.walk(semantic)
        )
        and any(
            isinstance(node, ast.Name) and node.id == "MAX_COMMAND_DISCOVERY_PAYLOAD_BYTES"
            for node in ast.walk(semantic)
        )
    )
    unauthorized_before_gateway = bool(
        rerank is not None
        and any(
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.Not)
            and isinstance(node.operand, ast.Name)
            and node.operand.id == "semantic_allowed"
            for node in ast.walk(rerank)
        )
        and (
            gateway_call := next(
                (
                    node
                    for node in ast.walk(rerank)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_gateway"
                ),
                None,
            )
        ) is not None
        and next(
            node.lineno
            for node in ast.walk(rerank)
            if isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.Not)
            and isinstance(node.operand, ast.Name)
            and node.operand.id == "semantic_allowed"
        ) < gateway_call.lineno
    )
    no_direct_provider_bypass = not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"complete", "stream"}
        and not _model_service_completion(node)
        for node in ast.walk(semantic)
    )
    independent_id_validation = bool(
        validator is not None
        and any(isinstance(node, ast.Compare) and any(isinstance(op, ast.NotIn) for op in node.ops) for node in ast.walk(validator))
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "validate_command_discovery_response"
            for node in (ast.walk(rerank) if rerank is not None else ())
        )
    )
    return all(
        (
            discovery_owner is not None,
            gateway_factory_present,
            isinstance(default_gateway_factory, ast.Name)
            and default_gateway_factory.id == "create_model_gateway",
            stores_factory,
            calls_factory_seam,
            provider_factory_owner,
            delegates_to_provider_factory,
            model_service_owner,
            model_service_call,
            one_call_limit,
            context_binds_gateway,
            request_contract_bound,
            bounded_payload,
            unauthorized_before_gateway,
            no_direct_provider_bypass,
            independent_id_validation,
        )
    )


def _semantic_cli_profile_path_is_canonical() -> bool:
    """Prove CLI semantic Discovery follows the config/profile owner."""

    projection = _tree("agent/interfaces/cli/discovery_projection.py")
    app = _tree("agent/interfaces/cli/app.py")
    resolver = _named_function(projection, "_resolve_semantic_gateway_config")
    build_service = _named_function(projection, "build_service")
    run_commands = _named_function(app, "_run_commands")
    if resolver is None or build_service is None or run_commands is None:
        return False

    resolver_imports = {
        node.module
        for node in ast.walk(resolver)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    has_repository_import = "agent.runtime.config_repository" in resolver_imports
    has_paths_import = "agent.runtime.paths" in resolver_imports
    has_repository_load = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "load"
        for node in ast.walk(resolver)
    )
    has_model_profile_projection = any(
        isinstance(node, ast.Attribute) and node.attr == "model_profile"
        for node in ast.walk(resolver)
    )
    has_repository_construction = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ConfigRepository"
        for node in ast.walk(resolver)
    )
    has_resolution_call = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_resolve_semantic_gateway_config"
        and {item.arg for item in node.keywords}
        >= {"app_paths", "config_path", "profile", "home"}
        for node in ast.walk(build_service)
    )
    has_semantic_config_binding = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SemanticCommandDiscovery"
        and any(
            item.arg == "gateway_config"
            and isinstance(item.value, ast.Name)
            and item.value.id == "semantic_config"
            for item in node.keywords
        )
        for node in ast.walk(build_service)
    )
    has_cli_propagation = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "discover_commands"
        and {
            item.arg for item in node.keywords
        } >= {"config_path", "profile", "home", "semantic_allowed"}
        for node in ast.walk(run_commands)
    )
    return all(
        (
            has_repository_import,
            has_paths_import,
            has_repository_construction,
            has_repository_load,
            has_model_profile_projection,
            has_resolution_call,
            has_semantic_config_binding,
            has_cli_propagation,
        )
    )


def _availability_is_conservative() -> bool:
    """Prove an omitted availability map entry fails closed to unknown."""

    tree = _tree("agent/discovery/contracts.py")
    function = _named_function(tree, "availability_for")
    if function is None:
        return False
    has_map_lookup = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and _attribute_parts(node.func.value)[-1:] == ("availability_by_entry_id",)
        for node in ast.walk(function)
    )
    has_explicit_known_return = any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Name)
        and node.value.id == "value"
        for node in ast.walk(function)
    )
    has_unknown_return = any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "DiscoveryAvailability"
        and len(node.value.args) >= 2
        and isinstance(node.value.args[0], ast.Constant)
        and node.value.args[0].value is False
        and isinstance(node.value.args[1], ast.Name)
        and node.value.args[1].id == "DISCOVERY_AVAILABILITY_UNKNOWN"
        for node in ast.walk(function)
    )
    mentions_cli_kind = any(
        isinstance(node, ast.Name) and node.id == "DiscoverySourceKind"
        for node in ast.walk(function)
    )
    return has_map_lookup and has_explicit_known_return and has_unknown_return and not mentions_cli_kind


def _static_cli_projection_is_explicit() -> bool:
    """Prove parser-derived CLI entries receive an explicit static projection."""

    tree = _tree("agent/interfaces/cli/discovery_projection.py")
    function = _named_function(tree, "build_catalog")
    if function is None:
        return False
    has_parser_projection = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "cli_entries_from_parser"
        for node in ast.walk(function)
    )
    for_node = next(
        (
            node
            for node in ast.walk(function)
            if isinstance(node, ast.For)
            and isinstance(node.iter, ast.Name)
            and node.iter.id == "parser_entries"
        ),
        None,
    )
    if for_node is None:
        return False
    return has_parser_projection and any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Subscript)
            and _contains_name(target, "availability")
            for target in node.targets
        )
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "DiscoveryAvailability"
        and len(node.value.args) == 1
        and isinstance(node.value.args[0], ast.Constant)
        and node.value.args[0].value is True
        for node in ast.walk(for_node)
    )


def _native_completion_is_structural() -> bool:
    """Inspect the emitted completer body within its owning Python function."""

    tree = _tree("agent/interfaces/cli/completion.py")
    function = _named_function(tree, "powershell_completion_script")
    if function is None:
        return False
    literals = "\n".join(
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    required = (
        "Register-ArgumentCompleter -Native",
        "-CommandName 'llm-agent'",
        "$wordToComplete",
        "$commandAst",
        "$cursorPosition",
        "CommandElements",
        "Extent.StartOffset",
        "$prior",
        "$prefix",
        "Get-ChildItem",
        "CompletionResult",
    )
    return all(token in literals for token in required) and "Select-Object -SkipLast" not in literals


def _compatibility_exception_findings(problems: list[str]) -> None:
    """Reject package owners introduced as historical exceptions during W20."""

    unauthorized_owners = (
        ("agent/application_factory.py", "W20-COMPAT-FACTORY"),
        ("agent/interfaces/cli/interactive_session_runner.py", "W20-COMPAT-SESSION-RUNNER"),
        ("agent/interfaces/cli/workspace_flow.py", "W20-COMPAT-WORKSPACE-FLOW"),
    )
    for relative, code in unauthorized_owners:
        _append_missing(
            problems,
            not (ROOT / relative).exists(),
            code,
            f"unauthorized W20 owner remains: {relative}",
        )


def _production_python_files(relative_root: str = "agent") -> tuple[Path, ...]:
    root = ROOT / relative_root
    return tuple(sorted(item for item in root.rglob("*.py") if item.is_file()))


def _wave20_shape_findings(problems: list[str]) -> None:
    """Reject the duplicate owners called out by the frozen B/C shape."""

    required = (
        "agent/engineering/model_safe.py",
        "agent/discovery/contracts.py",
        "agent/discovery/index.py",
        "agent/discovery/ranking.py",
        "agent/discovery/store.py",
        "agent/discovery/service.py",
        "agent/discovery/semantic.py",
        "agent/interfaces/cli/discovery_projection.py",
        "agent/interfaces/cli/discovery_ui.py",
        "agent/interfaces/cli/completion.py",
        "agent/interfaces/cli/workspace_recents.py",
        "agent/interfaces/cli/mcp.py",
        "distribution/mcp_lockfiles.py",
        "distribution/mcp-windows-py312.lock",
        "scripts/verify_wave20_mcp_extra.py",
    )
    for relative in required:
        _append_missing(problems, (ROOT / relative).is_file(), "W20-SHAPE-MISSING", relative)

    exact_shapes = {
        "agent/engineering": {
            "agent/engineering/__init__.py",
            "agent/engineering/contracts.py",
            "agent/engineering/registry.py",
            "agent/engineering/policy.py",
            "agent/engineering/service.py",
            "agent/engineering/store.py",
            "agent/engineering/recovery.py",
            "agent/engineering/transactions.py",
            "agent/engineering/summary.py",
            "agent/engineering/model_safe.py",
            "agent/engineering/cli.py",
            "agent/engineering/backends/__init__.py",
            "agent/engineering/backends/repository.py",
            "agent/engineering/backends/evaluation.py",
            "agent/engineering/backends/health.py",
            "agent/engineering/backends/inspection.py",
            "agent/engineering/backends/inspection_projection.py",
        },
        "agent/discovery": {
            "agent/discovery/__init__.py",
            "agent/discovery/contracts.py",
            "agent/discovery/index.py",
            "agent/discovery/ranking.py",
            "agent/discovery/store.py",
            "agent/discovery/service.py",
            "agent/discovery/semantic.py",
        },
        "agent/interfaces/mcp": {
            "agent/interfaces/mcp/__init__.py",
            "agent/interfaces/mcp/engineering_server.py",
            "agent/interfaces/mcp/projection.py",
        },
    }
    for relative_root, expected in exact_shapes.items():
        root = ROOT / relative_root
        actual = {
            item.relative_to(ROOT).as_posix()
            for item in root.rglob("*.py")
            if item.is_file()
        } if root.is_dir() else set()
        for extra in sorted(actual - expected):
            problems.append(f"W20-SHAPE-EXTRA: {extra}")
        for missing in sorted(expected - actual):
            problems.append(f"W20-SHAPE-MISSING: {missing}")

    removed = (
        "agent/actions/command_bank.py",
        "agent/actions/discovery_catalog.py",
        "agent/actions/discovery_contracts.py",
        "agent/actions/discovery_local.py",
        "agent/actions/discovery_semantic.py",
        "agent/actions/f2.py",
        "agent/interfaces/cli/powershell_completion.py",
        "agent/interfaces/cli/recent_workspaces.py",
        "agent/evaluation/practical_receipt.py",
    )
    for relative in removed:
        _append_missing(problems, not (ROOT / relative).exists(), "W20-SHAPE-DUPLICATE", relative)
    _compatibility_exception_findings(problems)

    discovery_root = ROOT / "agent" / "discovery"
    forbidden_discovery_imports = (
        "agent.interfaces.cli",
        "agent.application",
        "agent.llm",
        "prompt_toolkit",
        "ContextManager",
    )
    for path in discovery_root.glob("*.py"):
        imports = _imports(path)
        for imported in forbidden_discovery_imports:
            if imported in imports or any(value.startswith(imported + ".") for value in imports):
                problems.append(f"W20-DISCOVERY-LEAK: {path.relative_to(ROOT)} -> {imported}")

    completion = _source("agent/interfaces/cli/completion.py")
    shell = _source("agent/interfaces/cli/interactive_shell.py")
    projection = _source("agent/interfaces/cli/discovery_projection.py")
    model_safe = _source("agent/engineering/model_safe.py")
    mcp = _source("agent/interfaces/mcp/engineering_server.py")
    application = _source("agent/application.py")
    application_wiring = _source("agent/application_wiring.py")
    extensions = _source("agent/tools/extension_bootstrap.py")
    registry = _source("agent/interfaces/cli/action_registry.py")

    _append_missing(problems, "build_parser" in completion and "_SubParsersAction" in completion, "W20-COMPLETION-PARSER", "PowerShell completion does not walk the canonical parser")
    _append_missing(problems, "ActionRegistry" not in completion and "action_registry" not in completion, "W20-COMPLETION-ACTION-LEAK", "slash Action registry leaked into native completion")
    _append_missing(problems, '"mcp engineering"' not in completion and "'mcp engineering'" not in completion, "W20-COMPLETION-HARDCODE", "MCP completion route is hardcoded outside parser metadata")
    _append_missing(problems, "request_command_palette" in shell and "validate_and_handle" in shell, "W20-F2-OWNER", "F2 does not submit through the one active prompt buffer")
    _append_missing(problems, shell.count("PromptSession(") == 1, "W20-F2-PROMPT-OWNER", "more than one PromptSession owner is present")
    _append_missing(problems, "PathCompleter" in shell and "only_directories=True" in shell and "expanduser=True" in shell, "W20-PATH-COMPLETION", "same-shell PathCompleter contract is missing")
    _append_missing(problems, "build_model_safe_engineering_service" in model_safe and "build_model_safe_internal_adapters" in model_safe, "W20-MODEL-SAFE-BUILDER", "mandatory model-safe builder/internal adapter owner is missing")
    _append_missing(problems, all(token in mcp for token in ("build_model_safe_engineering_service", "_default_adapter")), "W20-MCP-BUILDER", "MCP does not use the canonical model-safe builder")
    _append_missing(problems, not any(token in mcp for token in ("EngineeringService(", "EngineeringRunStore(", "RepositoryBackend(", "EvaluationBackend(")), "W20-MCP-SECOND-GRAPH", "MCP contains a second Engineering graph")
    _append_missing(
        problems,
        "build_application_extensions" in application
        and all(
            token in application_wiring
            for token in (
                "def build_application_extensions",
                "build_model_safe_engineering_service",
                "build_model_safe_internal_adapters",
                "internal_adapters=internal_adapters",
            )
        ),
        "W20-EXTENSION-INTERNALS",
        "application wiring does not compose trusted internal adapters",
    )
    _append_missing(problems, all(token in extensions for token in ("internal_adapters", "self._degraded", "application_extension_bootstrap_degraded")), "W20-EXTENSION-DEGRADED", "degraded extension composition does not preserve internals")
    _append_missing(problems, all(token in projection for token in ("cli_entries_from_parser", "engineering_entries", "mcp_engineering_availability")), "W20-DISCOVERY-PROJECTION", "CLI Discovery projection has a parallel catalog owner")
    _append_missing(problems, all(token in registry for token in ("/commands", "agent.interfaces.cli.discovery_ui.commands_action", "ALWAYS_LOCAL", "NOT_APPLICABLE", "NEVER")), "W20-COMMAND-BINDING", "canonical /commands binding is incomplete")
    _append_missing(problems, _native_completion_is_structural(), "W20-COMPLETION-NATIVE", "completion is not a parser-driven native PowerShell completer")
    _append_missing(problems, "Select-Object -SkipLast" not in completion, "W20-COMPLETION-PS51", "completion uses a PowerShell version-specific SkipLast pipeline")

    ranking = _tree("agent/discovery/ranking.py")
    ranking_source = _source("agent/discovery/ranking.py")
    fields = next(
        (
            node.value
            for node in ast.walk(ranking)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "_FIELDS" for target in node.targets)
        ),
        None,
    )
    expected_fields = ("preferred_invocation", "alias", "title", "keyword", "description", "example")
    actual_fields = (
        tuple(item.value for item in fields.elts if isinstance(item, ast.Constant))
        if isinstance(fields, ast.Tuple)
        and all(isinstance(item, ast.Constant) for item in fields.elts)
        else ()
    )
    _append_missing(problems, actual_fields == expected_fields, "W20-DISCOVERY-FIELD-PRIORITY", "field priority is not the frozen preferred_invocation-to-example order")
    _append_missing(problems, 'int(score.get("field_rank", len(_FIELDS)))' in ranking_source and '-int(score.get("field_rank"' not in ranking_source, "W20-DISCOVERY-FIELD-SORT", "final candidate ordering inverts the frozen field rank")

    _append_missing(problems, _availability_is_conservative(), "W20-DISCOVERY-AVAILABILITY", "missing availability does not fail closed to unknown")
    _append_missing(problems, _static_cli_projection_is_explicit(), "W20-DISCOVERY-STATIC-CLI", "parser-derived CLI entries do not receive explicit static availability")
    _append_missing(problems, _semantic_cli_profile_path_is_canonical(), "W20-DISCOVERY-CONFIG-PROFILE", "semantic discovery does not reuse canonical config/profile resolution")


def _practical_owner_is_canonical() -> bool:
    practical = _tree("agent/evaluation/practical.py")
    evidence = _tree("agent/evaluation/evidence.py")
    receipt = _tree("agent/evaluation/receipt.py")
    oracle = _tree("agent/evaluation/practical_oracle_support.py")
    comparison = _tree("agent/evaluation/comparison.py")
    identity = _tree("agent/evaluation/evaluation_identity.py")
    model_identity = _tree("agent/evaluation/model_identity.py")
    public_run = _named_function(practical, "run_practical_scripted")
    public_compare = _named_function(practical, "compare_practical_profiles")
    run = _named_function(practical, "_run_practical_scripted")
    context = _named_function(practical, "_evaluation_context")
    decorator = _named_function(practical, "_effective_decorator")
    compare = _named_function(practical, "_compare_practical_profiles")
    record = _named_function(evidence, "practical_scenario_record")
    oracle_failures = _named_function(oracle, "_oracle_failures")
    receipt_type = _named_class(receipt, "PracticalEvidenceV1")
    receipt_builder = _named_function(receipt, "build_practical_receipt")
    comparison_owner = _named_function(comparison, "compare_receipt_groups")
    comparison_route = _named_function(comparison, "compare_practical_reports")
    comparison_compatibility = _named_function(comparison, "_validate_groups_compatibility")
    candidate_owner = _named_function(identity, "candidate_identity")
    model_owner = _named_function(model_identity, "fake_model_identity")
    return (
        public_run is not None
        and public_compare is not None
        and run is not None
        and context is not None
        and decorator is not None
        and compare is not None
        and record is not None
        and oracle_failures is not None
        and receipt_type is not None
        and receipt_builder is not None
        and comparison_owner is not None
        and comparison_route is not None
        and comparison_compatibility is not None
        and candidate_owner is not None
        and model_owner is not None
        and _imports_name_from(practical, "agent.evaluation.evidence", "practical_scenario_record")
        and _imports_name_from(evidence, "agent.evaluation.practical_oracle_support", "_oracle_failures")
        and _imports_name_from(practical, "agent.evaluation.evaluation_identity", "candidate_identity")
        and _imports_name_from(practical, "agent.evaluation.evaluation_identity", "fake_model_identity")
        and _imports_name_from(identity, "agent.evaluation.model_identity", "fake_model_identity")
        and "_run_practical_scripted" in _call_names(public_run)
        and "_compare_practical_profiles" in _call_names(public_compare)
        and {"candidate_identity", "candidate_identity_string", "fake_model_identity", "practical_scenario_record", "write_evidence_report"}.issubset(_call_names(run))
        and {"_evaluation_context", "_effective_decorator"}.issubset(_call_names(run))
        and "FaultController" in _call_names(run)
        and "FaultingEvaluationGateway" in _call_names(decorator)
        and _contains_name(context, "_FAULT_EXPERIMENT_ID")
        and _has_attribute(context, "fingerprint")
        and _call_passes_name_as_keyword(compare, "compare_practical_reports", "receipt_comparer", "compare_receipt_groups")
        and "receipt_comparer" in _call_names(comparison_route)
        and "_validate_groups_compatibility" in _call_names(comparison_owner)
        and {"_oracle_failures", "build_evaluation_receipt", "with_practical_evidence", "PracticalEvidenceV1"}.issubset(_call_names(record))
        and _has_keyword(record, "evaluator_passed")
        and _contains_name(oracle_failures, "CapabilityScenario")
        and _contains_name(receipt_type, "candidate_identity")
        and _contains_name(receipt_type, "comparison_result")
        and _contains_name(receipt_builder, "PracticalEvidenceV1")
        and _contains_name(comparison_compatibility, "candidate_identity")
        and _contains_name(comparison_compatibility, "model_identity")
    )


def _fault_closed_owner_is_canonical() -> bool:
    contracts = _tree("agent/evaluation/contracts.py")
    policy = _tree("agent/engineering/policy.py")
    effect = _named_class(contracts, "FaultEffect")
    step_validation = _class_method(contracts, "FaultStep", "__post_init__")
    plan = _named_class(contracts, "FaultPlanV1")
    parser = _named_function(contracts, "parse_fault_json")
    plan_from_dict = _class_method(contracts, "FaultPlanV1", "from_dict")
    fingerprint = _class_method(contracts, "FaultPlanV1", "fingerprint")
    authorize = _class_method(contracts, "FaultPlanV1", "authorize")
    policy_entry = _named_function(policy, "fault_plan_error")
    policy_validation = _named_function(policy, "_validate_closed_plan")
    policy_authorization = _named_function(policy, "_invoke_authorize")
    expected_effects = {"timeout", "provider_error", "invalid_structured_response"}
    effect_values = _enum_string_values(effect)
    effect_is_closed = bool(
        effect is not None
        and any(isinstance(base, ast.Name) and base.id == "Enum" for base in effect.bases)
        and effect_values == expected_effects
    )
    return (
        effect_is_closed
        and plan is not None
        and step_validation is not None
        and _contains_name(step_validation, "FaultEffect")
        and plan_from_dict is not None
        and "FaultEffect" in _call_names(plan_from_dict)
        and _has_exception_handler(plan_from_dict, {"TypeError", "ValueError"})
        and parser is not None
        and _contains_name(parser, "FaultPlanV1")
        and fingerprint is not None
        and "canonical_json" in _call_names(fingerprint)
        and "sha256" in _call_names(fingerprint)
        and authorize is not None
        and _contains_name(authorize, "PermissionError")
        and policy_entry is not None
        and _contains_name(policy_entry, "FaultPlanV1")
        and policy_validation is not None
        and "_authorize_plan" in _call_names(policy_validation)
        and policy_authorization is not None
        and "authorize" in _call_names(policy_authorization)
    )


def _model_safe_projection_is_canonical() -> bool:
    safe = _tree("agent/engineering/model_safe.py")
    health = _tree("agent/engineering/backends/health.py")
    descriptors = next(
        (
            node.value
            for node in ast.walk(safe)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "MODEL_SAFE_TOOL_DESCRIPTORS" for target in node.targets)
        ),
        None,
    )
    expected_idempotence = {
        "engineering_list": True,
        "engineering_describe": True,
        "engineering_result": True,
        "engineering_inspect_completed": False,
        "engineering_health_offline": False,
    }
    metadata_ok = False
    if isinstance(descriptors, ast.Tuple):
        observed: dict[str, ast.Call] = {}
        for item in descriptors.elts:
            if isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "ToolDescriptor" and item.args and isinstance(item.args[0], ast.Constant) and isinstance(item.args[0].value, str):
                observed[item.args[0].value] = item
        metadata_ok = set(observed) == set(expected_idempotence) and all(
            _keyword_constant(call, "cacheable") is False
            and _keyword_constant(call, "result_data_schema") is None
            and _keyword_constant(call, "idempotent") is expected_idempotence[name]
            for name, call in observed.items()
        )
    result = _named_function(safe, "engineering_result")
    reference = _named_function(safe, "_project_reference")
    health_projection = _named_function(health, "project_health")
    health_fields: set[str] = set()
    if health_projection is not None:
        health_fields = {
            key.value
            for node in ast.walk(health_projection)
            if isinstance(node, ast.Dict)
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    workspace_guard = bool(
        result is not None
        and all(_contains_name(result, name) for name in ("trusted_workspace", "observed_workspace", "observed_environment"))
        and all(
            any(
                isinstance(node, ast.Compare)
                and any(isinstance(operator, ast.NotEq) for operator in node.ops)
                and _contains_name(node, left)
                and _contains_name(node, right)
                for node in ast.walk(result)
            )
            for left, right in (("observed_workspace", "trusted_workspace"), ("observed_environment", "trusted_workspace"))
        )
    )
    reference_redaction = _bounded_reference_projection(reference)
    bounder = _named_function(safe, "_bounded")
    result_projection = _named_function(safe, "_project_result")
    project_health = _named_function(safe, "_project_health")
    health_method = _class_method(safe, "ModelSafeEngineering", "engineering_health_offline")
    builder = _named_function(safe, "build_model_safe_engineering_service")
    return (
        _model_safe_tool_shape_is_exact()
        and metadata_ok
        and workspace_guard
        and health_projection is not None
        and {"id", "status"}.issubset(health_fields)
        and reference_redaction
        and bounder is not None
        and any(
            isinstance(item, ast.Subscript)
            and _contains_name(item, "MAX_MODEL_SAFE_TEXT")
            for item in ast.walk(bounder)
        )
        and result_projection is not None
        and "_project_reference" in _call_names(result_projection)
        and project_health is not None
        and "project_health" in _call_names(project_health)
        and health_method is not None
        and "project_health" in _call_names(health_method)
        and _imports_name_from(safe, "agent.engineering.backends.health", "project_health")
        and builder is not None
        and "_BoundModelSafeEngineering" in _call_names(builder)
    )


def _wave20b_findings(problems: list[str]) -> None:
    """Static ownership checks for B's deterministic seams."""

    practical = _source("agent/evaluation/practical.py")
    health = _source("agent/engineering/backends/health.py")
    inspection = _source("agent/engineering/backends/inspection.py")
    inspection_projection = _source("agent/engineering/backends/inspection_projection.py")
    inspection_tree = _tree("agent/engineering/backends/inspection.py")
    policy = _source("agent/engineering/policy.py")
    safe = _source("agent/engineering/model_safe.py")
    campaign = _source("tests/unit/engineering/test_wave20b_campaign.py")
    c_campaign = _source("tests/unit/interfaces/test_wave20c_campaign.py")

    _append_missing(problems, _practical_owner_is_canonical(), "W20-B-PRACTICAL-OWNER", "PRACTICAL receipt/oracle/fault identity is not canonical")
    _append_missing(problems, all(token in practical for token in ("compare_receipt_groups", "candidate_identity", "model_identity", "envelopes", "Mapping")), "W20-B-COMPARISON-ENVELOPE", "comparison does not pass identity-bound Mapping envelopes")
    _append_missing(problems, _fault_closed_owner_is_canonical(), "W20-B-FAULT-CLOSED", "FaultPlanV1 does not own the closed practical fault vocabulary")
    _append_missing(problems, all(token in health for token in ("run_standalone_health_check", "write_report=False", "online=False")), "W20-B-HEALTH-OWNER", "health.offline does not reuse standalone offline owner")
    inspection_owner = (
        all(token in inspection for token in ("InspectionService", "trace_run_id", "limit", "project_inspection"))
        and "_PROJECTION_KEYS" in inspection_projection
        and _imports_name_from(
            inspection_tree,
            "agent.engineering.backends.inspection_projection",
            "project_inspection",
        )
        and "project_inspection" in _call_names(inspection_tree)
    )
    _append_missing(problems, inspection_owner, "W20-B-INSPECTION-OWNER", "inspection.completed-run does not delegate to the pure bounded InspectionService projection")
    schema_shape = all(token in policy for token in ("valid_schema_value", "_valid_object", "_valid_array", "additionalProperties"))
    _append_missing(problems, schema_shape, "W20-B-SCHEMA-RECURSIVE", "Engineering preflight lacks recursive closed-schema validation")
    _append_missing(problems, _model_safe_projection_is_canonical(), "W20-B-MODEL-SAFE-PROJECTION", "model-safe metadata/equality/redaction owner is incomplete")
    reference_source = safe.split("def _project_reference", 1)
    reference_body = reference_source[1].split("def ", 1)[0] if len(reference_source) == 2 else ""
    _append_missing(problems, "label" not in reference_body, "W20-B-REFERENCE-REDACTION", "model-safe references expose label")
    _append_missing(problems, _semantic_owner_is_canonical(), "W20-B-SEMANTIC-OWNER", "semantic Discovery lacks canonical factory/contract/boundary ownership")
    _append_missing(problems, "_source(" not in campaign and "_source(" not in c_campaign, "W20-CAMPAIGN-SOURCE-ONLY", "permanent W20 campaign still contains source-string-only assertions")
    _append_missing(problems, "def test_B01_" in campaign and "def test_B48_" in campaign, "W20-B-CAMPAIGN-MATERIAL", "B campaign endpoints are missing")


def _engineering_findings(problems: list[str]) -> None:
    core = ("contracts.py", "registry.py", "policy.py", "service.py", "store.py", "recovery.py", "transactions.py", "summary.py")
    for name in core:
        path = ENGINEERING / name
        if not path.exists():
            if name == "store.py":
                continue
            problems.append(f"W20-A-MISSING: {path.relative_to(ROOT)}")
            continue
        for imported in _imports(path):
            if imported.startswith(FORBIDDEN_CORE_PREFIXES):
                problems.append(f"W20-A-FORBIDDEN-IMPORT: {path.relative_to(ROOT)} -> {imported}")
        if name == "service.py" and "agent.engineering.backends" in path.read_text(encoding="utf-8"):
            problems.append("W20-A-SERVICE-BACKEND-DIRECTION: service imports concrete backend")

    if not problems:
        store = _source("agent/engineering/store.py")
        service = _source("agent/engineering/service.py")
        policy = _source("agent/engineering/policy.py")
        summary = _source("agent/engineering/summary.py")
        registry = _source("agent/engineering/registry.py")
        recovery = _source("agent/engineering/recovery.py")
        transactions = _source("agent/engineering/transactions.py")
        transition = store + transactions
        backend = _source("agent/engineering/backends/repository.py")
        cli = _source("agent/engineering/cli.py")
        verifier = _source("scripts/verify_installed_package.py")
        _append_missing(problems, all(token in store for token in ("class EngineeringRunStore", "def result_for_workspace", "def _publish_active", "def _write_recovery_tombstone")) and "def reconcile_existing" in recovery and all(token in transactions for token in ("def commit_terminal_record", "def terminalize_dead_active", "def write_recovery_tombstone")), "W20-A-STORE-OWNER", "store facade, transaction implementation or recovery orchestration is incomplete")
        recovery_imports = _imports(ENGINEERING / "recovery.py")
        transaction_imports = _imports(ENGINEERING / "transactions.py")
        recovery_calls = _call_names(_tree("agent/engineering/recovery.py"))
        _append_missing(
            problems,
            not recovery_imports.intersection({"agent.memory.json_persistence", "agent.runtime.instance_lock", "agent.runtime.lock_filesystem"})
            and not recovery_calls.intersection({"open", "read_bytes", "write_bytes", "unlink", "replace", "mkdir", "_bounded_document", "write_text_atomic", "InstanceLock"}),
            "W20-C-RECOVERY-NO-DURABLE-IO",
            "recovery orchestration directly owns durable reads, writes, or locks",
        )
        _append_missing(problems, "agent.engineering.transactions" not in recovery_imports and not transaction_imports.intersection({"agent.engineering.store", "agent.engineering.recovery"}), "W20-C-TRANSACTION-DIRECTION", "recovery/transaction dependencies bypass the store facade or form a cycle")
        _append_missing(problems, "class EngineeringRunStore" not in (recovery + transactions) and "class EngineeringService" not in transactions, "W20-C-ONE-PUBLIC-FACADE", "recovery or transactions defines a second public Engineering facade")
        _append_missing(
            problems,
            all(token in store for token in ("def result_for_workspace", "scoped_run_document(paths, run_id, workspace.workspace_id, _belongs_to_workspace)", "if scoped is None:", "return scoped_run_exists(paths, run_id, workspace_id, _belongs_to_workspace)"))
            and all(token in transactions for token in ("def scoped_run_document", "summary = _summary_from_record(document)", "except (EngineeringRecordError, OSError):", "return scoped_run_document(paths, run_id, workspace_id, belongs_to_workspace) is not None"))
            and "if not store._scoped_run_exists" in recovery,
            "W20-C-SCOPED-RECOVERY-TRANSACTION",
            "workspace visibility is not proven before recovery or rechecked after corruption",
        )
        capacity_owner = _named_class(_tree("agent/engineering/policy.py"), "EngineeringCapacityPolicy")
        capacity_calls = _call_names(capacity_owner) if capacity_owner is not None else set()
        policy_calls = _call_names(_tree("agent/engineering/policy.py"))
        policy_imports = _imports(ENGINEERING / "policy.py")
        durable_calls = {"iterdir", "scandir", "listdir", "open", "read_bytes", "read_text", "write_bytes", "write_text", "_bounded_document", "inspect_final_path", "sync_parent_directory", "unlink_if_observed", "unlink", "replace", "write_text_atomic"}
        _append_missing(
            problems,
            capacity_owner is not None
            and not capacity_calls.intersection(durable_calls)
            and not policy_calls.intersection(durable_calls)
            and not policy_imports.intersection({"agent.runtime.filesystem_primitives", "agent.runtime.lock_filesystem", "agent.memory.json_persistence"})
            and "def _snapshot" not in policy
            and all(token in transactions for token in ("def enforce_capacity", "def _capacity_snapshot", "select_evictable", "unlink_if_observed", "sync_parent_directory")),
            "W20-C-CAPACITY-TRANSACTION",
            "policy observes durable capacity state or transaction eviction is absent",
        )
        size_limits = {"store.py": 360, "recovery.py": 300, "transactions.py": 360}
        for name, limit in size_limits.items():
            lines = len((ENGINEERING / name).read_text(encoding="utf-8").splitlines())
            _append_missing(problems, lines <= limit, "W20-C-ENGINEERING-SIZE", f"{name} has {lines} lines; limit is {limit}")
        baseline = json.loads((ROOT / "quality/baseline.json").read_text(encoding="utf-8"))
        module_size = baseline["module_size"]
        allowances = module_size["allowed"]
        store_lines = len(store.splitlines())
        transaction_lines = len(transactions.splitlines())
        expected_store_allowance = store_lines if store_lines > 300 else None
        expected_transaction_allowance = transaction_lines if transaction_lines > 300 else None
        _append_missing(
            problems,
            module_size["max_lines"] == 300
            and allowances.get("agent/engineering/store.py") == expected_store_allowance
            and allowances.get("agent/engineering/transactions.py") == expected_transaction_allowance
            and (store_lines > 300 or "agent/engineering/store.py" not in allowances)
            and (transaction_lines > 300 or "agent/engineering/transactions.py" not in allowances)
            and "agent/engineering/recovery.py" not in allowances,
            "W20-C-EXACT-STORE-RATCHET",
            "Engineering size baseline is not the exact final store/transaction LOC or global threshold changed",
        )
        _append_missing(problems, all(token in store for token in ("engineering_store_lock_file", "def history", "def result")), "W20-A-STORE-LOCK-OWNERSHIP", "history/result lack store-lock ownership proof")
        lock_sources = ("agent/engineering/store.py", "agent/engineering/transactions.py")
        _append_missing(problems, _all_calls_have_keyword_in_sources(lock_sources, {"create", "write_text_atomic"}, "create_parent", False), "W20-A-NO-PARENT-LOCK", "store lock/write calls must use create_parent=False")
        _append_missing(problems, "def prove_success" in transactions and transactions.count("if inspect_final_path(marker).exists:") == 2 and "sync_parent_directory(marker)" in transactions, "W20-A-SUCCESS-REPAIR", "success projection lacks marker absence durability repair")
        _append_missing(problems, "def resource_fingerprint" in policy and "hashlib.sha256" in policy, "W20-A-RESOURCE-HASH", "resource lock identity is not digest-derived")
        _append_missing(problems, "source.root" in backend and "sys.executable" in backend and '"shell": False' in backend, "W20-A-FIXED-LAUNCH", "repository launch is not request-controlled and fixed")
        _append_missing(problems, 'value["candidate_identity"] != trusted_identity' in backend, "W20-A-CANDIDATE-EQUALITY", "verifier identity is not compared with run-start identity")
        _append_missing(problems, "EngineeringBackendStateIndeterminateError" in backend and "_cleanup" in backend, "W20-A-CLEANUP-INDIFFERENT", "managed cleanup uncertainty lacks typed indeterminate mapping")
        _append_missing(problems, not any(token in (store + service + policy + summary + transactions).lower() for token in ("run_index", "mutable index", "engineering_index")), "W20-A-MUTABLE-INDEX", "mutable run-index authority detected")
        _append_missing(problems, not ("subprocess.run" in backend or "shell=True" in backend or "['python'" in backend), "W20-A-ARBITRARY-LAUNCH", "arbitrary subprocess launch detected")
        _append_missing(problems, "terminate_process" in backend and "process_group_id" in backend and "def terminate_process" not in backend, "W20-A-PROCESS-TREE-OWNER", "second process-tree implementation detected")
        _append_missing(problems, all(token in summary for token in ("expected_active", "expected_terminal", "canonical_document_text")), "W20-A-SERIALIZER-FIELDS", "V1 serializer field checks are incomplete")
        _append_missing(problems, all(token in verifier for token in ("candidate_identity", "summary-json", "clean-acceptance", "installed_deterministic")), "W20-A-INSTALLED-PROBES", "canonical verifier lacks installed probes")
        _append_missing(problems, "ACCEPTANCE_INSTALLED_PACKAGE" in registry and "network=True" in registry, "W20-A-PRODUCTION-DESCRIPTOR", "installed acceptance descriptor is incomplete")
        _append_missing(problems, "relative_verifier" in cli and "scripts/verify_installed_package.py" in cli, "W20-A-SOURCE-CONTAINMENT", "trusted verifier containment is not literal")
        _append_missing(problems, "def _spawn" in backend and "def _wait" in backend, "W20-A-PROCESS-LIFECYCLE", "repository process lifecycle is incomplete")
        _append_missing(problems, "def _safe_point" not in service and "safe_point_error" in service, "W20-A-SAFE-POINT-OWNER", "cancellation/deadline safe-point owner is duplicated")
        _append_missing(problems, "_observe_reconciliation_safe_point" in store and "observe_safe_point" in transactions and "_observe_reconciliation_safe_point" in recovery, "W20-A-RECONCILIATION-SAFE-POINT", "cleanup/reconciliation does not observe lifecycle safe points without abandoning evidence")
        _append_missing(problems, "EngineeringTransitionMode.TERMINAL_PENDING" in (transition + recovery) and "EngineeringTransitionMode.MANAGED_EXECUTION_GUARD" in transition, "W20-A-TRANSITION-MODES", "transition marker modes are incomplete")
        _append_missing(problems, "validate_transition" in (store + transition + recovery), "W20-A-TRANSITION-VALIDATION", "managed marker validation is not canonical")


def _literal_string_tuple(tree: ast.AST, name: str) -> tuple[str, ...]:
    value = next(
        (
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
        ),
        None,
    )
    if not isinstance(value, ast.Tuple) or not all(
        isinstance(item, ast.Constant) and isinstance(item.value, str)
        for item in value.elts
    ):
        return ()
    return tuple(
        item.value
        for item in value.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    )


def _model_safe_tool_shape_is_exact() -> bool:
    tree = _tree("agent/engineering/model_safe.py")
    operations = _literal_string_tuple(tree, "MODEL_SAFE_OPERATIONS")
    descriptors_value = next(
        (
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "MODEL_SAFE_TOOL_DESCRIPTORS"
                for target in node.targets
            )
        ),
        None,
    )
    descriptor_names = (
        tuple(
            item.args[0].value
            for item in descriptors_value.elts
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id == "ToolDescriptor"
            and item.args
            and isinstance(item.args[0], ast.Constant)
            and isinstance(item.args[0].value, str)
        )
        if isinstance(descriptors_value, ast.Tuple)
        else ()
    )
    expected = (
        "engineering_list",
        "engineering_describe",
        "engineering_result",
        "engineering_inspect_completed",
        "engineering_health_offline",
    )
    return operations == expected and descriptor_names == expected


def _mcp_workspace_binding_is_static() -> bool:
    tree = _tree("agent/interfaces/mcp/engineering_server.py")
    binding = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "MCPWorkspaceBinding"
        ),
        None,
    )
    from_path = _named_function(binding, "from_path") if binding is not None else None
    context = _named_function(tree, "_context")
    default_adapter = _named_function(tree, "_default_adapter")
    binding_fields = {
        node.target.id
        for node in ast.walk(binding) if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    } if binding is not None else set()
    creates_workspace_context = bool(
        from_path is not None
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create"
            and _contains_name(node.func.value, "WorkspaceContext")
            for node in ast.walk(from_path)
        )
    )
    binds_context = bool(
        context is not None
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "EngineeringWorkspaceContext"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Attribute)
            and isinstance(node.args[0].value, ast.Name)
            and node.args[0].value.id == "binding"
            and node.args[0].attr == "workspace_id"
            and isinstance(node.args[1], ast.Attribute)
            and isinstance(node.args[1].value, ast.Name)
            and node.args[1].value.id == "binding"
            and node.args[1].attr == "root"
            for node in ast.walk(context)
        )
    )
    passes_binding = bool(
        default_adapter is not None
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_model_safe_engineering_service"
            and any(
                item.arg == "workspace"
                and isinstance(item.value, ast.Name)
                and item.value.id == "binding"
                for item in node.keywords
            )
            for node in ast.walk(default_adapter)
        )
    )
    return binding_fields == {"root", "workspace_id"} and creates_workspace_context and binds_context and passes_binding


def _base_installed_w20c_probes_are_wired() -> bool:
    tree = _tree("scripts/verify_installed_package.py")
    probe = _named_function(tree, "_verify_w20c_base_surfaces")
    verifier = _named_function(tree, "verify_installed_package")
    if probe is None or verifier is None:
        return False
    literals = {
        node.value
        for function in _reachable_function_nodes(tree, ("_verify_w20c_base_surfaces",))
        for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    required = {
        "discovery-import",
        "build-parser",
        "commands-local-first",
        "commands-json-first",
        "completion-powershell-first",
        "mcp-command-discovery",
    }
    wired = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_verify_w20c_base_surfaces"
        for node in ast.walk(verifier)
    )
    return required.issubset(literals) and wired


def _mcp_findings(problems: list[str]) -> None:
    mcp_server = ROOT / "agent/interfaces/mcp/engineering_server.py"
    mcp_lock = ROOT / "distribution/mcp-windows-py312.lock"
    mcp_validator = ROOT / "distribution/mcp_lockfiles.py"
    extra_verifier = ROOT / "scripts/verify_wave20_mcp_extra.py"
    parser = _source("agent/interfaces/cli/parser.py")
    cli_mcp_path = ROOT / "agent/interfaces/cli/mcp.py"
    cli_mcp = _source("agent/interfaces/cli/mcp.py") if cli_mcp_path.is_file() else ""
    completion = _source("agent/interfaces/cli/completion.py")
    project = _source("pyproject.toml")
    _append_missing(problems, mcp_server.is_file(), "W20-C-MCP-SERVER", "low-level MCP server owner is missing")
    _append_missing(problems, mcp_validator.is_file() and mcp_lock.is_file(), "W20-C-MCP-LOCK", "MCP union lock/validator is missing")
    _append_missing(problems, extra_verifier.is_file(), "W20-C-MCP-VERIFIER", "installed MCP verifier is missing")
    _append_missing(problems, _model_safe_tool_shape_is_exact(), "W20-C-FIVE-TOOLS", "model-safe Engineering does not expose exactly the frozen five tools")
    _append_missing(problems, _mcp_workspace_binding_is_static(), "W20-C-WORKSPACE-ISOLATION", "MCP workspace binding is not immutable and canonical")
    _append_missing(problems, _base_installed_w20c_probes_are_wired(), "W20-C-BASE-PROBES", "base installed Discovery/commands/completion probes are missing or unwired")
    if mcp_server.is_file():
        mcp_text = mcp_server.read_text(encoding="utf-8")
        _append_missing(problems, "mcp.server.lowlevel.server" in mcp_text and "mcp.server.stdio" in mcp_text, "W20-C-LOW-LEVEL", "server does not use the required low-level stdio API")
        _append_missing(problems, not any(token in mcp_text for token in ("FastMCP", "MCPServer", "on_list_resources", "on_list_prompts", "streamable_http")), "W20-C-TOOLS-ONLY", "forbidden MCP capability/server wrapper is present")
        _append_missing(problems, "MODEL_SAFE_TOOL_DESCRIPTORS" in mcp_text and "MODEL_SAFE_TOOL_DESCRIPTORS" in _source("agent/engineering/model_safe.py"), "W20-C-SCHEMA-OWNER", "MCP tool schemas are not tied to model-safe metadata")
        _append_missing(problems, "MCP_ABANDON_ON_CANCEL = False" in mcp_text and "abandon_on_cancel=MCP_ABANDON_ON_CANCEL" in mcp_text, "W20-C-CANCELLATION", "MCP bridge does not prove non-abandoning sync semantics")
        _append_missing(problems, "lease.close()" in mcp_text and "begin_startup" in mcp_text, "W20-C-LIFECYCLE", "MCP lifecycle close is not explicit")
        _append_missing(problems, "AgentApplication" not in mcp_text and "agent.llm" not in mcp_text, "W20-C-NO-HEAVY-BOOTSTRAP", "MCP adapter imports application/model runtime")
    for relative in ("agent/interfaces/cli/parser.py", "agent/interfaces/cli/app.py", "agent/actions", "agent/discovery"):
        path = ROOT / relative
        paths = path.rglob("*.py") if path.is_dir() else (path,)
        for item in paths:
            imports = _imports(item)
            if any(value == "mcp" or value.startswith("mcp.") for value in imports):
                problems.append(f"W20-C-OPTIONAL-LEAK: {item.relative_to(ROOT)} imports MCP from base path")
    _append_missing(problems, "mcp = [" in project and '"mcp==2.2.0"' in project and '"mcp==2.2.0"' not in project.split("[project.optional-dependencies]", 1)[0], "W20-C-OPTIONAL-BOUNDARY", "MCP is not isolated in the optional dependency table")
    _append_missing(problems, "required=True" in parser and "mcp_engineering" in parser, "W20-C-WORKSPACE-ADMISSION", "MCP parser workspace contract is missing")
    _append_missing(problems, "ENGINEERING_MCP_EXTRA_REQUIRED" in cli_mcp and "find_spec" in cli_mcp, "W20-C-MISSING-EXTRA", "missing-extra behavior is not base-safe")
    _append_missing(problems, "build_parser" in completion and "_SubParsersAction" in completion and "mcp engineering" not in completion, "W20-C-COMPLETION", "completion does not derive MCP command metadata")
    if mcp_validator.is_file():
        validator_text = mcp_validator.read_text(encoding="utf-8")
        _append_missing(problems, all(token in validator_text for token in ("changed base package", "duplicate logical package", "required_version")), "W20-C-UNION-RULES", "union validator lacks base/conflict/required package checks")
    if extra_verifier.is_file():
        verifier_text = extra_verifier.read_text(encoding="utf-8")
        _append_missing(problems, all(token in verifier_text for token in ("tools/list", "stdio_roundtrip", "mcp_version")), "W20-C-INSTALLED-VERIFIER", "MCP installed verifier lacks required evidence")


def _campaign_findings(problems: list[str]) -> None:
    for prefix, start, end in (("A", 1, 56), ("B", 1, 48), ("C", 1, 24)):
        found: set[str] = set()
        for item in (ROOT / "tests").rglob("*.py"):
            tree = ast.parse(item.read_text(encoding="utf-8"), filename=str(item))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                match = re.fullmatch(rf"test_({prefix}\d{{2}})_.+", node.name)
                if match is not None:
                    found.add(match.group(1))
        expected = {f"{prefix}{index:02d}" for index in range(start, end + 1)}
        _append_missing(problems, found == expected, f"W20-{prefix}-CAMPAIGN", f"campaign identity set differs: missing={sorted(expected - found)} extra={sorted(found - expected)}")


def findings() -> list[str]:
    problems: list[str] = []
    _engineering_findings(problems)
    _wave20_shape_findings(problems)
    _wave20b_findings(problems)
    _mcp_findings(problems)
    _campaign_findings(problems)
    return problems


def main() -> int:
    problems = findings()
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print("Engineering architecture check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
