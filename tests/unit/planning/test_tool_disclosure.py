from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.llm.context_manager import ContextManager
from agent.llm.contracts import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    StructuredOutputMode,
)
from agent.llm.session import ChatSession
from agent.llm.tool_discovery_contract import (
    MAX_DISCLOSED_TOOLS,
    valid_tool_discovery,
)
from agent.planning.plan_validator import PlanValidator
from agent.planning.planning_context import PlanningContextSnapshot, PlanningTool
from agent.planning.tool_disclosure import disclose_tools, render_tool_guidance
from agent.state import AgentState
from agent.tools.contracts import ToolOriginKind
from agent.tools.runtime_identity import RuntimeSnapshotIdentity


def _context(
    *, include_writer: bool = False, names: list[str] | None = None
) -> PlanningContextSnapshot:
    names = names or ["tool_a", "tool_b", "tool_c", "tool_d", "tool_e"]
    tools = [
        PlanningTool(
            name=name,
            description=f"Purpose for {name}",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
            usage_examples=(
                {"args": {"value": name}, "purpose": "synthetic positive case"},
            ),
        )
        for name in names
    ]
    if include_writer:
        names.append("writer")
        tools.append(
            PlanningTool(
                name="writer",
                description="Mutates an approved target.",
                required_capabilities=frozenset({"write"}),
                input_schema={"type": "object"},
                origin_kind=ToolOriginKind.BUILTIN,
            )
        )
    identity = RuntimeSnapshotIdentity("registry", "workspace")
    return PlanningContextSnapshot(
        snapshot_id="context",
        registry_identity="registry",
        authority_identity="authority",
        tools=tuple(tools),
        eligible_names=frozenset(names),
        runtime_identity=identity,
        allowed_capabilities=frozenset({"read"}),
    )


def _owner(context: PlanningContextSnapshot, responses: list[object]) -> SimpleNamespace:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def ask_model(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return responses.pop(0)

    owner = SimpleNamespace(
        planning_context=context,
        get_planning_view=lambda kind: context.present(kind),
        context_manager=SimpleNamespace(ask_model=ask_model),
        session=SimpleNamespace(
            hardware_profile=SimpleNamespace(context_limit=8192),
            config={},
        ),
        _cached_base_prompt="level-zero-index",
        _log_metric=lambda _entry: None,
        _emit=lambda *_args, **_kwargs: None,
        calls=calls,
        operational_mode_label="READ ONLY",
        allowed_capabilities=frozenset({"read"}),
        execution_gateway=SimpleNamespace(_recover=lambda *_args: None),
        plan_builder=SimpleNamespace(
            continue_after_observation=lambda *_args: None,
            continue_after_reasoning_boundary=lambda *_args: None,
        ),
        agent_state=SimpleNamespace(continuation_attempts=0),
        tool_invocation_gateway=object(),
    )
    return owner


class _RealSeamGateway:
    provider_name = "tool-discovery-test"
    model = "tool-discovery-test"
    capabilities = ProviderCapabilities(
        structured_output_modes=(
            StructuredOutputMode.GBNF,
            StructuredOutputMode.JSON_PROMPT,
        )
    )

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError("test gateway received an unexpected request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome if isinstance(outcome, ModelResponse) else ModelResponse(content=str(outcome))


def _real_seam_owner(
    outcomes: list[Any],
) -> tuple[SimpleNamespace, _RealSeamGateway, ChatSession, list[dict[str, Any]], list[dict[str, Any]], list[tuple[Any, ...]]]:
    context = _context()
    gateway = _RealSeamGateway(outcomes)
    session = ChatSession(
        "tool-discovery-system",
        {
            "model": "tool-discovery-test",
            "max_tokens": 64,
            "max_model_calls": 8,
        },
        gateway=gateway,
    )
    model_entries: list[dict[str, Any]] = []
    session.set_model_call_callback(model_entries.append)
    manager = ContextManager(session, AgentState(), workspace_root=Path.cwd())
    metric_entries: list[dict[str, Any]] = []
    emitted: list[tuple[Any, ...]] = []
    owner = SimpleNamespace(
        planning_context=context,
        get_planning_view=lambda _kind: context.present("linear"),
        context_manager=manager,
        session=session,
        _log_metric=metric_entries.append,
        _emit=lambda *args: emitted.append(args),
    )
    return owner, gateway, session, model_entries, metric_entries, emitted


def test_known_file_reading_selects_file_reader_from_the_exact_index() -> None:
    context = _context(
        names=["file_reader", "grep", "code_analyzer", "directory_lister", "echo"]
    )
    owner = _owner(context, [{"tools": ["file_reader"]}])

    disclosure = disclose_tools(owner, planner_kind="linear", objective="read a known file")

    assert disclosure is not None
    assert disclosure.selected_names == frozenset({"file_reader"})
    assert '"name":"file_reader"' in disclosure.selected_view.render_detailed()
    assert '"name":"grep"' not in disclosure.selected_view.render_detailed()


def test_locate_then_inspect_selects_grep_and_file_reader() -> None:
    context = _context(
        names=["file_reader", "grep", "code_analyzer", "directory_lister", "echo"]
    )
    owner = _owner(context, [{"tools": ["grep", "file_reader"]}])

    disclosure = disclose_tools(owner, planner_kind="linear", objective="locate then inspect")

    assert disclosure is not None
    assert disclosure.selected_names == frozenset({"grep", "file_reader"})


def test_large_view_selects_only_exact_current_names_and_details_do_not_leak() -> None:
    context = _context()
    owner = _owner(context, [{"tools": ["tool_b", "tool_d"]}])

    disclosure = disclose_tools(owner, planner_kind="linear", objective="inspect")

    assert disclosure is not None
    assert disclosure.selected_names == frozenset({"tool_b", "tool_d"})
    assert len(owner.calls) == 1
    selection_prompt = str(owner.calls[0][0][0])
    assert '"schema"' not in selection_prompt
    assert '"usage_examples"' not in selection_prompt
    index = disclosure.index_view.render_index()
    detail = disclosure.selected_view.render_detailed()
    assert '"schema"' not in index
    assert '"usage_examples"' not in index
    assert '"name":"tool_b"' in detail
    assert '"name":"tool_d"' in detail
    assert '"name":"tool_a"' not in detail
    assert "SYNTHETIC EXAMPLE" in detail
    assert '"name":"tool_a"' in render_tool_guidance(owner, disclosure)


def test_invalid_selection_gets_one_bounded_correction_and_fails_closed() -> None:
    context = _context()
    owner = _owner(context, [{"tools": ["hidden"]}, {"tools": ["tool_c"]}])

    disclosure = disclose_tools(owner, planner_kind="linear", objective="inspect")

    assert disclosure is not None
    assert disclosure.selection_valid is True
    assert disclosure.structured_decision_valid is True
    assert disclosure.semantic_correction_requests == 1
    assert disclosure.discovery_requests == 2
    assert disclosure.selected_names == frozenset({"tool_c"})
    assert all(
        kwargs["request_contract"].value == "tool_discovery"
        for _args, kwargs in owner.calls
    )


def test_invalid_selection_after_correction_exposes_no_detail() -> None:
    context = _context()
    owner = _owner(context, [{"tools": ["hidden"]}, {"tools": ["also_hidden"]}])

    disclosure = disclose_tools(owner, planner_kind="linear", objective="inspect")

    assert disclosure is not None
    assert disclosure.selection_valid is False
    assert disclosure.selected_names == frozenset()
    assert disclosure.selected_view.tools == ()


def test_small_view_skips_model_selection_but_uses_full_detail() -> None:
    context = _context()
    small = context.present("linear", {"tool_a", "tool_b", "tool_c", "tool_d"})
    owner = _owner(context, [])
    owner.get_planning_view = lambda _kind: small

    disclosure = disclose_tools(owner, planner_kind="linear", objective="inspect")

    assert disclosure is not None
    assert disclosure.discovery_skipped is True
    assert owner.calls == []
    assert disclosure.selected_names == small.presented_names
    assert '"schema"' in disclosure.selected_view.render_detailed()


def test_selected_write_tool_does_not_change_authority_or_bypass_validator() -> None:
    context = _context(include_writer=True)
    owner = _owner(context, [{"tools": ["writer"]}])

    disclosure = disclose_tools(owner, planner_kind="linear", objective="modify")

    assert disclosure is not None
    assert disclosure.selected_names == frozenset({"writer"})
    assert context.allowed_capabilities == frozenset({"read"})
    validator = PlanValidator(
        {},
        list(context.eligible_names),
        frozenset({"read"}),
        None,
        planning_context=context,
        planning_view=disclosure.selected_view,
    )
    report = validator.validate([{"tool": "writer", "args": {}}])
    assert not report.is_valid


def test_real_resolver_exhaustion_does_not_open_semantic_correction() -> None:
    owner, gateway, session, model_entries, _metrics, emitted = _real_seam_owner(
        ["not json", "still not json"]
    )

    disclosure = disclose_tools(
        owner, planner_kind="linear", objective="malformed selector"
    )

    assert disclosure is not None
    assert disclosure.selection_valid is False
    assert disclosure.structured_decision_valid is False
    assert disclosure.discovery_requests == 1
    assert disclosure.semantic_correction_requests == 0
    assert len(gateway.requests) == 2
    assert session.budget_ledger.snapshot().model_calls == 2
    assert len(model_entries) == 2
    assert all(
        "previous selector was invalid" not in request.messages[-1].content.lower()
        for request in gateway.requests
    )
    assert emitted[-1][1]["discovery_requests"] == 1
    assert emitted[-1][1]["semantic_correction_requests"] == 0


def test_real_provider_failure_is_not_rewritten_as_invalid_selection() -> None:
    owner, gateway, session, model_entries, _metrics, emitted = _real_seam_owner(
        [RuntimeError("provider unavailable")]
    )

    with pytest.raises(ModelProviderError):
        disclose_tools(owner, planner_kind="linear", objective="provider failure")

    assert len(gateway.requests) == 1
    assert session.budget_ledger.snapshot().model_calls == 1
    assert len(model_entries) == 1
    assert model_entries[0]["provider_call_succeeded"] is False
    assert not emitted


def test_real_seam_counts_one_logical_request_and_two_provider_attempts() -> None:
    owner, gateway, session, model_entries, _metrics, _emitted = _real_seam_owner(
        ["not json", '{"tools":["tool_a"]}']
    )

    disclosure = disclose_tools(
        owner, planner_kind="linear", objective="resolver retry"
    )

    assert disclosure is not None
    assert disclosure.discovery_requests == 1
    assert disclosure.semantic_correction_requests == 0
    assert disclosure.structured_decision_valid is True
    assert disclosure.selection_valid is True
    assert len(gateway.requests) == 2
    assert len(model_entries) == session.budget_ledger.snapshot().model_calls == 2


def test_real_structured_hidden_selection_gets_one_dynamic_correction() -> None:
    owner, gateway, session, model_entries, _metrics, _emitted = _real_seam_owner(
        ['{"tools":["hidden_tool"]}', '{"tools":["also_hidden"]}']
    )

    disclosure = disclose_tools(
        owner, planner_kind="linear", objective="hidden selection"
    )

    assert valid_tool_discovery({"tools": ["hidden_tool"]}) is True
    assert disclosure is not None
    assert disclosure.structured_decision_valid is True
    assert disclosure.selection_valid is False
    assert disclosure.discovery_requests == 2
    assert disclosure.semantic_correction_requests == 1
    assert disclosure.selected_names == frozenset()
    assert len(gateway.requests) == 2
    assert len(model_entries) == session.budget_ledger.snapshot().model_calls == 2
    assert "previous selector was invalid" in gateway.requests[1].messages[-1].content.lower()


def test_static_discovery_contract_is_projected_without_duplicate_rules() -> None:
    repository = Path(__file__).resolve().parents[3]
    disclosure_source = (repository / "agent/planning/tool_disclosure.py").read_text(
        encoding="utf-8"
    )
    block7_source = (repository / "agent/evaluation/block7_tool_guidance.py").read_text(
        encoding="utf-8"
    )

    assert "from agent.llm.tool_discovery_contract import" in disclosure_source
    assert "MAX_DISCLOSED_TOOLS = " not in disclosure_source
    assert "len(raw_names)" not in disclosure_source
    assert "len(tools) <= 8" not in disclosure_source
    assert "limit: int = 8" not in block7_source
    assert MAX_DISCLOSED_TOOLS == 8
