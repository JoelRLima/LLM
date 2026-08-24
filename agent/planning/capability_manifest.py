"""Compact model-facing projection of the active orchestration contract.

The renderer is intentionally derived from the live planning view and the
callable orchestration owners.  The prose below documents the protocol that
those owners already implement; it is not a second capability registry.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

_MUTATION_CAPABILITIES = frozenset(
    {"write", "vcs_write", "package_install", "validate"}
)


def render_active_harness_capabilities(
    orchestrator: Any, *, planner_kind: str = "linear"
) -> str:
    """Render only capabilities exposed by the current runtime snapshot.

    The tool names come from the already-derived planning view.  The
    orchestration rows are guarded by the callable runtime owners, so this is
    a projection rather than a second feature registry.
    """

    context = getattr(orchestrator, "planning_context", None)
    view = (
        getattr(orchestrator, "get_planning_view", lambda _kind: None)(planner_kind)
        if context is not None
        else None
    )
    names = sorted(getattr(view, "presented_names", ()) or ())
    tools = tuple(getattr(view, "tools", ()) or ())
    allowed = getattr(context, "allowed_capabilities", None)
    if allowed is None:
        allowed = getattr(orchestrator, "allowed_capabilities", frozenset())
    allowed = frozenset(allowed or ())
    mode = str(getattr(orchestrator, "operational_mode_label", "FULL"))

    lines = ["ACTIVE HARNESS CAPABILITIES", f"Authority: {mode}"]
    if _has_mutation(tools, allowed):
        lines.append(
            "- mutation-capable tools are active; normal authority and approval checks still apply"
        )
    else:
        lines.append("- mutation-capable tools are unavailable in this planning view")

    lines.append("Planning:")
    gateway = getattr(orchestrator, "execution_gateway", None)
    if (
        callable(getattr(gateway, "execute_validated_plan", None))
        or callable(getattr(gateway, "validate_and_optimize_plan", None))
        or getattr(orchestrator, "tool_invocation_gateway", None) is not None
    ):
        lines.append("- static multi-step planning: one bounded plan is persisted and executed")
    binding_available = _binding_available(orchestrator)
    if binding_available:
        lines.extend(_render_binding_manual())
    plan_builder = getattr(orchestrator, "plan_builder", None)
    state = getattr(orchestrator, "agent_state", None)
    max_reasoning = _configured_positive(orchestrator, "max_reasoning_turns")
    if callable(getattr(plan_builder, "continue_after_observation", None)) and (
        getattr(state, "continuation_attempts", 0) is not None
    ):
        lines.extend(
            [
                "semantic continuation — EXISTS: choose the next transition after an observation; "
                "WHEN: a new decision is needed, not mechanical copying; HOW: one execute, "
                "complete_without_effect, or blocked JSON; WRONG: no final answer or value transfer.",
            ]
        )
    if callable(getattr(gateway, "_recover", None)):
        lines.extend(
            [
                "validation repair — EXISTS: one bounded pre-execution correction; WHEN: only the "
                "rejected fields change and tool/valid fields stay fixed; HOW: one same-tool object; "
                "WRONG: no tool replacement, valid-field mutation, or placeholders.",
                "semantic recovery/replan — EXISTS: bounded strategy choice after an executed tool failure; "
                "WHEN: the tool actually ran and failed; HOW: propose a validated alternative strategy; "
                "WRONG: schema-blocked steps are not tool-failure evidence.",
            ]
        )
    if max_reasoning and callable(getattr(plan_builder, "continue_after_reasoning_boundary", None)):
        lines.append("- bounded reasoning continuation is enabled by current runtime configuration")

    lines.extend(
        [
            "Evidence:",
            "- executed invocation evidence describes what actually ran",
            "- observation evidence describes what tools actually returned",
            "- objective and plan text are not execution proof",
            "Tools: " + (", ".join(names) if names else "none active"),
        ]
    )
    return "\n".join(lines)


def render_validation_repair_manual(
    orchestrator: Any,
    *,
    tool: str,
    frozen_args: Mapping[str, Any],
    repairable_fields: Iterable[str],
    prior_steps: Sequence[Any] = (),
    frozen_bindings: Mapping[str, Any] | None = None,
) -> str:
    fields = tuple(sorted({str(field) for field in repairable_fields if str(field)}))
    lines = [
        "VALIDATION REPAIR",
        f"Authority: {getattr(orchestrator, 'operational_mode_label', 'FULL')}",
        "WHAT: one bounded correction for a plan step rejected before execution.",
        f"Tool: {tool}",
        "Keep unchanged:",
    ]
    if frozen_args:
        lines.extend(
            f"- {key}={json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)}"
            for key, value in sorted(frozen_args.items(), key=lambda item: str(item[0]))
        )
    else:
        lines.append("- all already-valid arguments (none were rendered)")
    lines.extend(
        [
            "Repair the representation/source of "
            + (", ".join(fields) if fields else "the reported invalid field")
            + " only.",
            "Return one complete action='tool' JSON object for the same tool; do not add another step.",
            "Use only the grounded provenance forms named by the validator detail; never invent a future value.",
            "SOURCES: known concrete value -> args; value supplied by ResultBinding -> bindings.",
            "A binding satisfies its target argument. If a field is in bindings, omit that field from args.",
            "NEVER put the same argument name in both args and bindings; same target in both is INVALID.",
            "Repair may move the rejected field from invalid/missing literal to canonical bindings; no replacement literal in args.",
        ]
    )
    if tool == "grep" and "pattern" in fields:
        lines.append("For grep.pattern, prefer an exact user literal already in the objective; never invent regex or bind an array result to this string field.")
    if _binding_available(orchestrator):
        target = fields[0] if fields else "field"
        right_args = dict(frozen_args)
        right_bindings: dict[str, Any] = dict(frozen_bindings or {})
        right_example: dict[str, Any] = {
            "action": "tool",
            "tool": tool,
            "args": right_args,
            "bindings": right_bindings,
        }
        right_bindings[target] = {"from_step": 1, "path": []}
        wrong_args = dict(right_args)
        wrong_args[target] = "literal-value"
        wrong_example = dict(right_example)
        wrong_example["args"] = wrong_args
        lines.extend(
            [
                "RIGHT: "
                + json.dumps(right_example, ensure_ascii=False, separators=(",", ":")),
                "WRONG (same target in args and bindings): "
                + json.dumps(wrong_example, ensure_ascii=False, separators=(",", ":")),
                'WRONG: {"action":"tool","tool":"consumer","args":{"value":"${1.value}"}}',
                "from_step: 1 is the first ToolStep in this same plan; path: [] is the complete canonical data value.",
                "Do not use ${...}, $ref, {{...}}, or custom interpolation strings.",
                "Do not replace this step with another tool.",
            ]
        )
    else:
        lines.append(
            "This context has no prior-result binding capability; do not use ${...}, $ref, {{...}}, or custom interpolation."
        )
    if prior_steps:
        lines.append(
            "Available prior steps (structural candidates; choose; do not auto-bind; data only):"
        )
        lines.append("Shown inputs are not future results.")
        for fallback_index, item in enumerate(prior_steps, start=1):
            index = fallback_index
            step = item
            if (
                isinstance(item, tuple)
                and len(item) == 2
                and type(item[0]) is int
                and isinstance(item[1], Mapping)
            ):
                index, step = item
            if not isinstance(step, Mapping) or not isinstance(step.get("tool"), str):
                continue
            args = step.get("args")
            input_values = args if isinstance(args, Mapping) else {}
            rendered_args = ", ".join(
                f"{key}={json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)}"
                for key, value in sorted(input_values.items(), key=lambda item: str(item[0]))
            )[:256]
            lines.append(f"{index}. {step['tool']}")
            lines.append(f"   known input: {rendered_args or 'none'}")
            lines.append("   future result: unavailable before execution.")
            result_data_schema = _result_data_schema_for_tool(orchestrator, step["tool"])
            lines.append(f"   result.data shape: {_render_result_shape(result_data_schema)}")
            if _schema_type(result_data_schema) == "string":
                lines.append("   bindable whole-data path: []")
    return "\n".join(lines)


def _binding_available(orchestrator: Any) -> bool:
    gateway = getattr(orchestrator, "execution_gateway", None)
    return callable(getattr(gateway, "_bind_deferred_references", None))


def _render_binding_manual() -> list[str]:
    """Return the compact, grammar-shaped ResultBinding micro-API."""

    return [
        "ORCHESTRATION API (separate from individual tool schemas)",
        "prior-result binding",
        "EXISTS: copy or select an exact value from an earlier successful ToolStep.",
        "WHEN: use it when a later argument is unknown now but must come mechanically from prior tool data; no reasoning is needed.",
        "HOW: known-now values go in args; dependent values go in bindings (not in args).",
        "binding syntax: bindings maps each dependent argument to {from_step, path}.",
        'RIGHT: {"tool":"consumer","args":{"fixed":true},"bindings":{"value":{"from_step":1,"path":[]}}}',
        'WRONG: {"tool":"consumer","args":{"value":"${1.text}"}}',
        "from_step: 1 means the first ToolStep in this same plan (public numbering is 1-based).",
        "path: [] means the complete canonical ToolResult.data value; a non-empty path selects a nested key or list index.",
        "Do not put a bound field in args, guess its future value, or use ${...}, $ref, {{...}}, or custom interpolation.",
        "Prefer binding for mechanical copying; use semantic continuation only when a new model decision is needed after interpreting an observation.",
    ]


def _result_data_schema_for_tool(orchestrator: Any, tool_name: str) -> Mapping[str, Any] | None:
    context = getattr(orchestrator, "planning_context", None)
    for tool in getattr(context, "tools", ()) or ():
        if getattr(tool, "name", None) == tool_name:
            schema = getattr(tool, "result_data_schema", None)
            if isinstance(schema, Mapping):
                return schema
    registry = getattr(orchestrator, "tool_registry", None)
    descriptor = None
    if registry is not None:
        try:
            descriptor = registry.descriptor(tool_name)
        except (AttributeError, KeyError):
            descriptor = None
    if descriptor is None:
        return None
    schema = getattr(descriptor, "result_data_schema", None)
    if isinstance(schema, Mapping):
        return schema
    spec = getattr(descriptor, "spec", None)
    schema = getattr(spec, "result_data_schema", None)
    return schema if isinstance(schema, Mapping) else None


def _schema_type(schema: Mapping[str, Any] | None) -> str | None:
    schema_type = schema.get("type") if isinstance(schema, Mapping) else None
    return schema_type if isinstance(schema_type, str) else None


def _render_result_shape(schema: Mapping[str, Any] | None, depth: int = 0) -> str:
    if not isinstance(schema, Mapping) or depth > 8:
        return "unknown"
    schema_type = _schema_type(schema)
    if schema_type == "array":
        return f"array<{_render_result_shape(schema.get('items'), depth + 1)}>"
    if schema_type == "object":
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return "object"
        rendered = ",".join(
            f"{name}:{_render_result_shape(value, depth + 1)}"
            for name, value in sorted(properties.items(), key=lambda item: str(item[0]))
            if isinstance(name, str)
        )
        return f"object{{{rendered}}}"
    return schema_type or "unknown"


def _configured_positive(orchestrator: Any, key: str) -> bool:
    config = getattr(getattr(orchestrator, "session", None), "config", {}) or {}
    try:
        return int(config.get(key, 0)) > 0
    except (TypeError, ValueError):
        return False


def _has_mutation(tools: Iterable[Any], allowed: frozenset[str]) -> bool:
    return any(
        bool(frozenset(getattr(tool, "required_capabilities", ())) & _MUTATION_CAPABILITIES)
        and frozenset(getattr(tool, "required_capabilities", ())).issubset(allowed)
        for tool in tools
    )


__all__ = [
    "render_active_harness_capabilities",
    "render_validation_repair_manual",
]
