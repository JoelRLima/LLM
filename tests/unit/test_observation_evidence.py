import json
from types import SimpleNamespace

import pytest

from agent.final_response import FinalResponder
from agent.planning.hierarchical_executor import HierarchicalExecutor
from agent.planning.reactive_loop import ReactiveLoop
from agent.reporting.observation_evidence import (
    MAX_OBSERVATION_EVIDENCE_CHARS,
    ObservationEvidence,
    project_executed_invocation,
    project_tool_observation,
    serialize_tool_observations,
)
from agent.tools.contracts import ToolDescriptor, ToolOriginKind
from agent.tools.result_completeness import canonical_completeness


def _record(summary: str, index: int = 0) -> dict:
    return json.loads(summary.splitlines()[index])


def test_partial_artifact_does_not_become_truncation() -> None:
    result = {"artifacts": [{"metadata": {"complete": False, "truncated": False}}]}

    assert canonical_completeness(result) == (False, False)


@pytest.mark.parametrize(
    ("data", "expected_type"),
    (
        (None, "null"),
        ("", "string"),
        ("modificado", "string"),
        ([], "array"),
        ([{"name": "a.txt"}], "array"),
        ({}, "object"),
        ({"name": "a.txt"}, "object"),
        (0, "number"),
        (False, "boolean"),
    ),
)
def test_structural_presence_and_type_are_not_truthiness_based(data, expected_type) -> None:
    entry = {"tool": "probe", "result": {"status": "succeeded", "ok": True, "data": data}}

    evidence = project_tool_observation(entry)
    record = _record(serialize_tool_observations([entry]))
    observation = record["observation"]

    assert isinstance(evidence, ObservationEvidence)
    assert evidence.present is True
    assert observation["present"] is True
    assert observation["type"] == expected_type
    assert observation["complete"] is True
    assert observation["truncated"] is False
    assert "value" in observation
    assert observation["value"] == data


def test_missing_data_is_distinct_from_observed_null() -> None:
    missing = {"tool": "missing", "result": {"status": "succeeded", "ok": True}}
    observed_null = {
        "tool": "null",
        "result": {"status": "succeeded", "ok": True, "data": None},
    }

    records = [_record(serialize_tool_observations([entry])) for entry in (missing, observed_null)]

    assert records[0]["observation"] == {
        "present": False,
        "type": "missing",
        "complete": False,
        "truncated": False,
    }
    assert records[1]["observation"]["present"] is True
    assert records[1]["observation"]["type"] == "null"
    assert records[1]["observation"]["value"] is None


def test_string_length_and_exact_value_are_explicit() -> None:
    entry = {
        "tool": "file_reader",
        "invocation_id": "read-1",
        "result": {
            "status": "succeeded",
            "ok": True,
            "executed": True,
            "data": "modificado",
            "artifacts": [{"metadata": {"complete": True}}],
        },
    }

    observation = _record(serialize_tool_observations([entry]))["observation"]

    assert observation["present"] is True
    assert observation["type"] == "string"
    assert observation["value"] == "modificado"
    assert observation["chars"] == len("modificado")
    assert observation["complete"] is True
    assert observation["truncated"] is False


def test_source_partial_observation_is_preview_not_complete_value() -> None:
    entry = {
        "tool": "file_reader",
        "result": {
            "status": "succeeded",
            "ok": True,
            "data": "prefix",
            "artifacts": [{"metadata": {"complete": False, "total_chars": 100}}],
        },
    }

    observation = _record(serialize_tool_observations([entry]))["observation"]

    assert observation["present"] is True
    assert observation["complete"] is False
    assert observation["truncated"] is False
    assert observation["preview"] == "prefix"
    assert "value" not in observation
    assert observation["source_complete"] is False


def test_source_truncation_remains_distinct_from_serializer_truncation() -> None:
    entry = {
        "tool": "file_reader",
        "result": {
            "status": "succeeded",
            "ok": True,
            "data": "prefix",
            "artifacts": [{"metadata": {"complete": False, "truncated": True}}],
        },
    }

    observation = _record(serialize_tool_observations([entry]))["observation"]

    assert observation["complete"] is False
    assert observation["truncated"] is True
    assert "serialization_truncated" not in observation


def test_model_facing_truncation_is_bounded_and_truthful() -> None:
    entry = {
        "tool": "large",
        "result": {"status": "succeeded", "ok": True, "data": "x" * 5000},
    }

    assert project_tool_observation(entry).complete is True
    summary = serialize_tool_observations([entry], max_chars=300)
    observation = _record(summary)["observation"]

    assert len(summary) <= 300
    assert observation["complete"] is False
    assert observation["truncated"] is False
    assert observation["serialization_truncated"] is True
    assert "preview" in observation
    assert "value" not in observation
    assert observation["preview"].endswith("...<truncated>")


def test_value_at_serializer_boundary_is_complete_and_next_value_is_preview() -> None:
    def rendered(length: int) -> dict:
        entry = {
            "tool": "boundary",
            "result": {"status": "succeeded", "ok": True, "data": "x" * length},
        }
        return _record(serialize_tool_observations([entry], max_chars=2_000))

    last_complete = max(
        length
        for length in range(2_001)
        if "value" in rendered(length)["observation"]
    )
    exact = rendered(last_complete)["observation"]
    next_value = rendered(last_complete + 1)["observation"]

    assert exact["complete"] is True
    assert exact["truncated"] is False
    assert len(exact["value"]) == last_complete
    assert next_value["complete"] is False
    assert next_value["truncated"] is False
    assert next_value["serialization_truncated"] is True
    assert "value" not in next_value
    assert next_value["preview"].endswith("...<truncated>")


@pytest.mark.parametrize(
    "data",
    (
        [f"item-{index}" for index in range(200)],
        {f"key-{index}": f"value-{index}" for index in range(200)},
    ),
)
def test_large_structured_values_use_explicit_preview(data) -> None:
    entry = {"tool": "structured", "result": {"status": "succeeded", "ok": True, "data": data}}

    summary = serialize_tool_observations([entry], max_chars=400)
    observation = _record(summary)["observation"]

    assert len(summary) <= 400
    assert observation["type"] in {"array", "object"}
    assert observation["complete"] is False
    assert observation["truncated"] is False
    assert observation["serialization_truncated"] is True
    assert "value" not in observation
    assert observation["preview"].endswith("...<truncated>")


def test_multiple_observations_keep_order_and_provenance() -> None:
    entries = [
        {
            "tool": "file_reader",
            "invocation_id": "a",
            "result": {"status": "succeeded", "ok": True, "data": ""},
        },
        {
            "tool": "grep",
            "invocation_id": "b",
            "result": {"status": "succeeded", "ok": True, "data": [{"file": "b.txt"}]},
        },
    ]

    records = [_record(serialize_tool_observations(entries), index) for index in range(2)]

    assert [record["tool"] for record in records] == ["file_reader", "grep"]
    assert [record["invocation_id"] for record in records] == ["a", "b"]
    assert records[0]["observation"]["value"] == ""
    assert records[1]["observation"]["value"] == [{"file": "b.txt"}]


def test_failed_result_without_data_has_no_observation_value_or_raw_error() -> None:
    secret = "api_key=TOPSECRET Authorization: Bearer TOPSECRET"
    entry = {
        "tool": "failed",
        "result": {
            "status": "failed",
            "ok": False,
            "error_code": "TOOL_ERROR",
            "error": secret,
            "message": secret,
        },
    }

    summary = serialize_tool_observations([entry])
    observation = _record(summary)["observation"]

    assert observation["present"] is False
    assert "value" not in observation
    assert "preview" not in observation
    assert '"error_code":"TOOL_ERROR"' in summary
    assert "TOPSECRET" not in summary
    assert "Authorization: Bearer" not in summary


def test_global_budget_keeps_all_normal_records_and_is_deterministic() -> None:
    history = [
        {
            "tool": f"tool_{index}",
            "invocation_id": f"id-{index}",
            "result": {"status": "succeeded", "ok": True, "data": "x" * 5000},
        }
        for index in range(60)
    ]

    first = serialize_tool_observations(history)
    second = serialize_tool_observations(history)

    assert first == second
    assert len(first) <= MAX_OBSERVATION_EVIDENCE_CHARS
    assert len(first.splitlines()) == len(history)
    assert all(json.loads(line)["observation"] for line in first.splitlines())
    assert first.count('"preview"') == len(history)


def test_reactive_and_hierarchical_consumers_use_common_evidence() -> None:
    entry = {
        "tool": "file_reader",
        "invocation_id": "read-1",
        "args": {"file_path": "controle.txt"},
        "result": {"status": "succeeded", "ok": True, "data": "modificado"},
    }

    reactive_line = ReactiveLoop._history_line(entry)
    hierarchical = HierarchicalExecutor._summarize_step_results([entry])

    for rendered in (reactive_line, hierarchical):
        assert '"present":true' in rendered
        assert '"value":"modificado"' in rendered
        assert '"invocation_id":"read-1"' in rendered


def test_final_prompt_labels_non_empty_value_as_authoritative_evidence() -> None:
    responder = FinalResponder(
        SimpleNamespace(
            agent_state=SimpleNamespace(
                tool_history=[
                    {
                        "tool": "file_reader",
                        "invocation_id": "read-1",
                        "result": {
                            "status": "succeeded",
                            "ok": True,
                            "executed": True,
                            "data": "modificado",
                        },
                    }
                ]
            )
        )
    )

    prompt = responder._build_prompt("Leia controle.txt e diga exatamente o conteúdo.", "")

    assert "authoritative_tool_observation" in prompt
    assert '"present":true' in prompt
    assert '"complete":true' in prompt
    assert '"value":"modificado"' in prompt
    assert "message" in prompt and "error" in prompt


def test_executed_invocation_projects_only_descriptor_approved_fields() -> None:
    descriptor = ToolDescriptor(
        "grep", "grep", public_invocation_fields={"pattern"}
    )
    entry = {
        "tool": "grep",
        "invocation_id": "actual-1",
        "args": {"pattern": "actual pattern", "path": "secret-path"},
        "result": {
            "status": "succeeded",
            "executed": True,
            "message": "secret message",
            "error": "secret error",
            "data": [],
        },
    }

    projection = project_executed_invocation(entry, {"grep": descriptor})
    rendered = serialize_tool_observations([entry], descriptor_lookup={"grep": descriptor})

    assert projection["values"] == {"pattern": "actual pattern"}
    assert projection["invocation_id"] == "actual-1"
    assert projection["status"] == "succeeded"
    assert projection["executed"] is True
    assert projection["projection_complete"] is False
    assert projection["truncated"] is False
    assert "secret-path" not in rendered
    assert "secret message" not in rendered
    assert "secret error" not in rendered
    assert '"pattern":"actual pattern"' in rendered


def test_executed_invocation_is_bounded_and_preserves_false_execution() -> None:
    descriptor = ToolDescriptor("grep", "grep", public_invocation_fields={"pattern"})
    entry = {
        "tool": "grep",
        "invocation_id": "actual-2",
        "args": {"pattern": "x" * 2_000},
        "result": {"status": "permission_denied", "executed": False},
    }

    projection = project_executed_invocation(entry, {"grep": descriptor}, max_chars=100)
    assert projection["executed"] is False
    assert projection["projection_complete"] is False
    assert projection["truncated"] is True
    assert projection["values"]["pattern"].endswith("...<truncated>")


def test_serializer_truncation_is_distinct_from_source_projection_truncation() -> None:
    descriptor = ToolDescriptor("grep", "grep", public_invocation_fields={"pattern"})
    entry = {
        "tool": "grep",
        "args": {"pattern": "x" * 1_000},
        "result": {"status": "succeeded", "executed": True, "data": None},
    }

    rendered = serialize_tool_observations(
        [entry], descriptor_lookup={"grep": descriptor}, max_chars=300
    )
    invocation = _record(rendered)["invocation"]

    assert invocation["projection_complete"] is False
    assert invocation["truncated"] is True
    assert invocation["serialization_truncated"] is True


def test_invocation_projection_distinguishes_empty_hidden_partial_and_full_args() -> None:
    empty = {
        "tool": "no_args",
        "args": {},
        "result": {"status": "succeeded", "executed": True},
    }
    hidden = {
        "tool": "hidden",
        "args": {"secret": "value"},
        "result": {"status": "succeeded", "executed": True},
    }
    partial = {
        "tool": "partial",
        "args": {"public": "value", "secret": "value"},
        "result": {"status": "succeeded", "executed": True},
    }
    full = {
        "tool": "full",
        "args": {"public": "value"},
        "result": {"status": "succeeded", "executed": True},
    }

    assert project_executed_invocation(empty, {"no_args": ToolDescriptor("no_args", "no args")})[
        "projection_complete"
    ] is True
    assert project_executed_invocation(hidden, {"hidden": ToolDescriptor("hidden", "hidden")})[
        "projection_complete"
    ] is False
    assert project_executed_invocation(
        partial, {"partial": ToolDescriptor("partial", "partial", public_invocation_fields={"public"})}
    )["projection_complete"] is False
    assert project_executed_invocation(
        full, {"full": ToolDescriptor("full", "full", public_invocation_fields={"public"})}
    )["projection_complete"] is True


def test_extension_descriptor_cannot_publish_invocation_fields() -> None:
    with pytest.raises(ValueError):
        ToolDescriptor(
            "external",
            "external",
            origin_kind=ToolOriginKind.EXTENSION,
            extension_id="example.extension",
            adapter_id="example.extension",
            public_invocation_fields={"pattern"},
        )
