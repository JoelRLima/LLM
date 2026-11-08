from __future__ import annotations

import json
from types import SimpleNamespace

from llm_agent.agent.llm.context_model_call import _prepare_model_state
from llm_agent.agent.llm.context_projection import (
    REQUIRED_EVIDENCE,
    UNTRUSTED_PROJECT_GUIDANCE,
    UNTRUSTED_SESSION,
    ContextSourceRecord,
    fixed_untrusted_data_policy,
    render_untrusted_context_envelope,
)
from llm_agent.agent.llm.decision_contract import ModelRequestContract
from llm_agent.agent.llm.stage_context import project_stage_records


def _record(
    source_id: str,
    source_kind: str,
    *,
    necessity: str = "OPTIONAL_AUXILIARY",
    trust_class: str = UNTRUSTED_SESSION,
) -> ContextSourceRecord:
    return ContextSourceRecord(
        source_id=source_id,
        source_kind=source_kind,
        necessity=necessity,
        trust_class=trust_class,
        reason="test data",
        data={"content": f"payload:{source_id}"},
    )


def _manager(config: dict, records: tuple[ContextSourceRecord, ...]):
    class Session:
        def __init__(self) -> None:
            self.config = config
            self.messages = [{"role": "system", "content": "ORIGINAL SYSTEM"}]

        def add_user_message(self, content: str) -> None:
            self.messages.append({"role": "user", "content": content})

    manager = SimpleNamespace(
        session=Session(),
        hardware_profile=SimpleNamespace(default_output_tokens=2048),
        _grammar_supports_grammar=False,
        build_base_system_prompt=lambda _persona, _tools: "BASE SYSTEM",
        build_trusted_task_context=lambda: "TRUSTED TASK DEFINITION",
        build_auxiliary_records=lambda _prompt: records,
        _emit_runtime_projection=lambda event_type, payload: events.append(
            (event_type, payload)
        ),
    )
    events: list[tuple[str, dict]] = []
    return manager, events


def _prepare(manager, *, contract):
    return _prepare_model_state(
        manager,
        "CURRENT USER REQUEST",
        None,
        True,
        None,
        contract,
        4096,
    )


def test_standard_profile_keeps_auxiliary_records_and_mandatory_messages_unchanged():
    records = (
        _record("runtime:frontier", "execution_frontier", necessity=REQUIRED_EVIDENCE),
        _record("workspace:hints", "workspace_hints"),
        _record("session:history", "derived_session"),
        _record(
            "guidance:AGENTS.md",
            "project_guidance",
            trust_class=UNTRUSTED_PROJECT_GUIDANCE,
        ),
    )
    manager, events = _manager({}, records)

    _budget, _grammar, base_messages, selected = _prepare(
        manager,
        contract=ModelRequestContract.INITIAL_PLAN,
    )

    assert selected == records
    assert manager.session.messages[0]["content"] == (
        "BASE SYSTEM\n\n"
        + fixed_untrusted_data_policy()
        + "\n\nTRUSTED TASK DEFINITION"
    )
    assert base_messages[-1] == {
        "role": "user",
        "content": "CURRENT USER REQUEST",
    }
    assert render_untrusted_context_envelope(selected) == render_untrusted_context_envelope(records)
    assert events == []


def test_compact_initial_plan_keeps_required_frontier_and_guidance_trust():
    frontier = _record(
        "runtime:frontier",
        "execution_frontier",
        necessity=REQUIRED_EVIDENCE,
    )
    hint = _record("workspace:hints", "workspace_hints")
    history = _record("session:history", "derived_session")
    guidance = _record(
        "guidance:AGENTS.md",
        "project_guidance",
        trust_class=UNTRUSTED_PROJECT_GUIDANCE,
    )
    memory = _record("memory:current", "memory")
    manager, events = _manager(
        {"agent_execution_profile": "compact"},
        (frontier, hint, history, guidance, memory),
    )

    _budget, _grammar, _base_messages, selected = _prepare(
        manager,
        contract=ModelRequestContract.INITIAL_PLAN,
    )

    assert selected == (frontier, guidance, memory)
    assert selected[0] is frontier
    envelope = json.loads(render_untrusted_context_envelope(selected))
    projected = {item["source_id"]: item for item in envelope["records"]}
    assert projected["runtime:frontier"]["necessity"] == REQUIRED_EVIDENCE
    assert projected["runtime:frontier"]["trust_class"] == UNTRUSTED_SESSION
    assert projected["guidance:AGENTS.md"]["trust_class"] == UNTRUSTED_PROJECT_GUIDANCE
    assert events == [
        (
            "stage_context_selected",
            {
                "profile": "compact",
                "stage": "initial_plan",
                "selected_record_count": 3,
                "excluded_record_count": 2,
                "excluded_source_kind_counts": {
                    "derived_session": 1,
                    "workspace_hints": 1,
                },
            },
        )
    ]


def test_unknown_contract_keeps_compact_profile_records_unchanged():
    records = (
        _record("workspace:hints", "workspace_hints"),
        _record("session:history", "derived_session"),
    )

    selection = project_stage_records(
        {"agent_execution_profile": "compact"},
        "unrecognized_contract",  # type: ignore[arg-type]
        records,
    )

    assert selection.records == records
    assert selection.excluded_source_kind_counts == ()


def test_compact_continuation_does_not_drop_required_or_guidance_records():
    frontier = _record(
        "runtime:frontier",
        "execution_frontier",
        necessity=REQUIRED_EVIDENCE,
    )
    guidance = _record(
        "guidance:AGENTS.md",
        "project_guidance",
        trust_class=UNTRUSTED_PROJECT_GUIDANCE,
    )
    records = (
        frontier,
        _record("workspace:hints", "workspace_hints"),
        _record("session:history", "derived_session"),
        guidance,
        _record("repository-state:latest", "repository_state"),
        _record("memory:current", "memory"),
    )

    selection = project_stage_records(
        {"agent_execution_profile": "compact"},
        ModelRequestContract.REASONING_BOUNDARY_CONTINUATION,
        records,
    )

    assert selection.records == (frontier, guidance, records[-2], records[-1])
    assert len(selection.records) <= len(records)
