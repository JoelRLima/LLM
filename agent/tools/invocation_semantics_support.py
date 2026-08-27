"""Concrete argument refinement for invocation semantics."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any

from agent.capabilities import WRITE_CAPABILITIES, Capability, capability_values
from agent.resources.contracts import (
    WORKSPACE_RESOURCE,
    ResourceAccess,
    ResourceMode,
    ResourceProvenance,
    normalize_resource_id,
)

CODE_READ_ACTIONS = frozenset({"analyze", "review"})
CODE_WRITE_ACTIONS = frozenset({"generate", "modify", "repair", "refactor"})
_MEMORY_WRITE_ACTIONS = frozenset({"set", "delete"})
_CODE_ACTION_CAPABILITIES = {
    "analyze": capability_values((Capability.READ, Capability.ANALYZE)),
    "review": capability_values((Capability.READ, Capability.ANALYZE)),
    "generate": capability_values((Capability.READ, Capability.WRITE)),
    "modify": capability_values((Capability.READ, Capability.WRITE, Capability.VALIDATE)),
    "repair": capability_values((Capability.READ, Capability.WRITE, Capability.VALIDATE)),
    "refactor": capability_values((Capability.READ, Capability.WRITE, Capability.VALIDATE)),
    "multitask": capability_values((Capability.READ, Capability.WRITE, Capability.VALIDATE)),
}
_TEMPLATE_CAPABILITIES = {
    "parallel_analyze": capability_values((Capability.READ, Capability.ANALYZE)),
    "parallel_review": capability_values((Capability.READ, Capability.ANALYZE)),
    "analyze_then_modify": capability_values((Capability.READ, Capability.WRITE, Capability.VALIDATE)),
}
CODE_TASK_ACTIONS = frozenset(
    {*CODE_READ_ACTIONS, *CODE_WRITE_ACTIONS, "multitask", "template"}
)
CODE_COMMAND_ACTIONS = frozenset(
    {*CODE_READ_ACTIONS, *CODE_WRITE_ACTIONS, "template"}
)


def _action(args: Mapping[str, Any]) -> str | None:
    value = args.get("action")
    return str(value).strip().casefold() if value is not None else None


def _descriptor_capabilities(descriptor: Any) -> frozenset[str]:
    """Normalize builtin capabilities while preserving external identifiers."""

    raw = getattr(descriptor, "capabilities", ())
    try:
        return capability_values(raw)
    except (TypeError, ValueError):
        # Extension descriptors may carry a transport-specific capability
        # namespace. Those values remain serialized boundary data; they must
        # not make the builtin semantic kernel fail open or fail closed.
        if isinstance(raw, (str, bytes, bytearray)):
            return frozenset()
        try:
            return frozenset(
                str(item).strip().casefold()
                for item in raw
                if isinstance(item, str) and item.strip()
            )
        except TypeError:
            return frozenset()


def _code_task_mutates(args: Mapping[str, Any]) -> bool:
    action = _action(args)
    if action in CODE_WRITE_ACTIONS:
        return True
    if action == "template":
        template = str(args.get("template", "")).casefold()
        if template in {"parallel_analyze", "parallel_review"}:
            return False
        return True
    if action == "multitask":
        return True
    if action in CODE_READ_ACTIONS:
        return False
    return True


def _code_task_capabilities(args: Mapping[str, Any]) -> frozenset[str] | None:
    action = _action(args)
    if action != "template":
        return _CODE_ACTION_CAPABILITIES.get(action) if action is not None else None
    return _TEMPLATE_CAPABILITIES.get(str(args.get("template", "")).casefold())


def _targets(args: Mapping[str, Any]) -> tuple[str, ...]:
    raw = args.get("targets")
    if not isinstance(raw, (list, tuple)):
        raw = [args.get(key) for key in ("file_path", "target", "path", "directory")]
    targets = [normalize_resource_id(value) for value in raw if isinstance(value, str) and value.strip()]
    return tuple(dict.fromkeys(targets))


def _shell_read_only(args: Mapping[str, Any]) -> bool:
    """Recognize the registered shell read surface without importing skill code."""

    command = str(args.get("command", "")).strip().casefold()
    if not command:
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False
    command_name = tokens[0].replace("\\", "/").rsplit("/", 1)[-1]
    if command_name == "tree":
        return True
    if command_name == "git":
        return len(tokens) >= 2 and tokens[1] == "log"
    if command_name == "ruff":
        return len(tokens) >= 2 and tokens[1] == "check"
    return False


def resolve_invocation_components(
    descriptor: Any, arguments: Mapping[str, Any],
) -> tuple[str, str | None, set[str], list[str], bool, tuple[ResourceAccess, ...]]:
    name = str(getattr(descriptor, "name", "")).strip().casefold()
    action = _action(arguments)
    capabilities, durable, read_only = _resolve_base_semantics(
        descriptor, name, action, arguments
    )
    if name == "code_task":
        _refine_code_task_semantics(action, arguments, capabilities, durable, read_only)
        read_only = _code_task_read_only(action, durable, read_only)

    if name == "session_memory" and action in _MEMORY_WRITE_ACTIONS:
        capabilities.add("memory")
    accesses = _resolve_accesses(name, action, targets=_targets(arguments), read_only=read_only)
    return name, action, capabilities, durable, read_only, accesses


def _resolve_base_semantics(
    descriptor: Any,
    name: str,
    action: str | None,
    arguments: Mapping[str, Any],
) -> tuple[set[str], list[str], bool]:
    capabilities = set(_descriptor_capabilities(descriptor))
    if name == "code_task":
        return _resolve_code_task_base(arguments, capabilities)
    if name == "session_memory":
        return _resolve_memory_base(action, capabilities)
    return _resolve_generic_base(name, arguments, capabilities)


def _resolve_code_task_base(
    arguments: Mapping[str, Any], capabilities: set[str],
) -> tuple[set[str], list[str], bool]:
    operation_capabilities = _code_task_capabilities(arguments)
    if operation_capabilities is not None:
        capabilities = set(operation_capabilities)
    durable: list[str] = []
    read_only = not _code_task_mutates(arguments)
    if not read_only:
        durable.append("write")
    return capabilities, durable, read_only


def _resolve_memory_base(
    action: str | None, capabilities: set[str],
) -> tuple[set[str], list[str], bool]:
    durable = ["memory_write"] if action not in {"get", "keys"} else []
    return capabilities, durable, not durable


def _resolve_generic_base(
    name: str, arguments: Mapping[str, Any], capabilities: set[str],
) -> tuple[set[str], list[str], bool]:
    if name == "git_reader":
        return capabilities, [], True
    if name == "shell" and _shell_read_only(arguments):
        capabilities -= {item.value for item in WRITE_CAPABILITIES}
        return capabilities, [], True
    if name == "shell" or capabilities & {item.value for item in WRITE_CAPABILITIES}:
        return capabilities, ["write"], False
    # NETWORK, PROCESS, PACKAGE_INSTALL, and VALIDATE describe external or
    # validation work; they do not, by themselves, authorize a durable
    # workspace mutation.  Only an explicit WRITE/VCS_WRITE capability owns
    # the workspace-write effect here.
    return capabilities, [], not bool(
        capabilities & {item.value for item in WRITE_CAPABILITIES}
    )


def _refine_code_task_semantics(
    action: str | None,
    arguments: Mapping[str, Any],
    capabilities: set[str],
    durable: list[str],
    read_only: bool,
) -> None:
    if action in CODE_READ_ACTIONS or (action == "template" and not durable):
        capabilities -= {item.value for item in WRITE_CAPABILITIES}
        capabilities.discard("validate")
        durable.clear()
    elif action == "multitask" and not durable:
        capabilities -= {item.value for item in WRITE_CAPABILITIES}
        capabilities.discard("validate")
    elif action == "generate":
        capabilities.discard("validate")
    if not read_only and bool(arguments.get("include_tests")):
        capabilities.add("process")


def _code_task_read_only(action: str | None, durable: list[str], current: bool) -> bool:
    if action in CODE_READ_ACTIONS or (action == "template" and not durable):
        return True
    return current


def _resolve_accesses(
    name: str, action: str | None, *, targets: tuple[str, ...], read_only: bool,
) -> tuple[ResourceAccess, ...]:
    if name == "session_memory" and action in _MEMORY_WRITE_ACTIONS:
        return (
            ResourceAccess("memory", ResourceMode.WRITE, ResourceProvenance.TRUSTED_DERIVED),
        )
    if name == "session_memory" and action in {"get", "keys"}:
        return (
            ResourceAccess("memory", ResourceMode.READ, ResourceProvenance.TRUSTED_DERIVED),
        )
    if not targets and not read_only:
        return (
            ResourceAccess(WORKSPACE_RESOURCE, ResourceMode.WRITE, ResourceProvenance.TRUSTED_DERIVED),
        )
    if not targets and read_only and name in {"code_task", "shell", "git_reader"}:
        return (
            ResourceAccess(WORKSPACE_RESOURCE, ResourceMode.READ, ResourceProvenance.TRUSTED_DERIVED),
        )
    mode = ResourceMode.WRITE if not read_only else ResourceMode.READ
    return tuple(ResourceAccess(target, mode, ResourceProvenance.TRUSTED_DERIVED) for target in targets)


__all__ = [
    "CODE_COMMAND_ACTIONS",
    "CODE_READ_ACTIONS",
    "CODE_TASK_ACTIONS",
    "CODE_WRITE_ACTIONS",
    "resolve_invocation_components",
]
