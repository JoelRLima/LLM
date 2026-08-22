from __future__ import annotations

import pytest

from agent.planning.result_bindings import (
    ResultBindingError,
    bind_result_references,
    resolve_bound_args,
    validate_result_bindings,
)


def _plan(binding):
    return [
        {"tool": "source", "args": {}},
        {"tool": "sink", "args": {"fixed": "kept"}, "bindings": {"value": {"from_step": binding["from_step"], "path": binding.get("path", [])}}},
    ]


def test_binding_accepts_backward_ordinal_and_normalizes_to_id() -> None:
    plan = _plan({"from_step": 1, "path": ["nested", "item"]})
    assert validate_result_bindings(plan) == []
    normalized = bind_result_references(plan, lambda: "generated")
    assert normalized[1]["bindings"]["value"]["from_step"] == normalized[0]["_step_id"]


@pytest.mark.parametrize(
    "binding",
    [
        {"from_step": 2, "path": []},
        {"from_step": 0, "path": []},
        {"from_step": 1, "path": ["__class__"]},
    ],
)
def test_binding_rejects_invalid_refs_targets_and_paths(binding) -> None:
    assert validate_result_bindings(_plan(binding))


@pytest.mark.parametrize(
    "bindings",
    [
        [{"target": "value", "from_step": 1, "path": []}],
        {"value": {"from_step": {"ordinal": 1}, "path": []}},
        {"value": {"step_id": "step-1", "path": []}},
    ],
)
def test_binding_public_shape_is_one_closed_mapping(bindings) -> None:
    plan = [
        {"tool": "source", "args": {}},
        {"tool": "sink", "args": {}, "bindings": bindings},
    ]
    assert validate_result_bindings(plan)


def test_unknown_binding_target_is_rejected_by_tool_schema() -> None:
    from agent.planning.plan_validator import PlanValidator

    class _Skill:
        def get_schema(self):
            return {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            }

    validator = PlanValidator(
        {"source": _Skill(), "sink": _Skill()},
        ["source", "sink"],
    )
    report = validator.validate(
        [
            {"tool": "source", "args": {}},
            {
                "tool": "sink",
                "args": {},
                "bindings": {"not_a_field": {"from_step": 1, "path": []}},
            },
        ]
    )

    assert report.is_valid is False
    assert "desconhecido" in str(report.blocked_steps or report.errors)


@pytest.mark.parametrize("value", [False, 0, "", None, [], {}])
def test_binding_preserves_present_falsy_values(value) -> None:
    plan = bind_result_references(_plan({"from_step": 1, "path": ["value"]}), lambda: "id")
    source_id = plan[0]["_step_id"]
    args = resolve_bound_args(
        plan[1],
        1,
        plan,
        [{"step_id": source_id, "result": {"ok": True, "executed": True, "status": "succeeded", "data": {"value": value}}}],
    )
    assert args["value"] == value


def test_binding_distinguishes_missing_and_detaches_values() -> None:
    plan = bind_result_references(_plan({"from_step": 1, "path": ["value"]}), lambda: "id")
    source_id = plan[0]["_step_id"]
    data = {"value": {"nested": [1]}}
    args = resolve_bound_args(
        plan[1], 1, plan,
        [{"step_id": source_id, "result": {"ok": True, "executed": True, "status": "succeeded", "data": data}}],
    )
    args["value"]["nested"].append(2)
    assert data == {"value": {"nested": [1]}}
    with pytest.raises(ResultBindingError):
        resolve_bound_args(
            plan[1], 1, plan,
            [{"step_id": source_id, "result": {"ok": True, "executed": True, "status": "succeeded", "data": {}}}],
        )


def test_binding_uses_latest_attempt_for_same_logical_step() -> None:
    plan = bind_result_references(
        _plan({"from_step": 1, "path": ["value"]}), lambda: "id"
    )
    source_id = plan[0]["_step_id"]
    history = [
        {"step_id": source_id, "result": {"ok": False, "status": "failed", "data": {"value": "old"}}},
        {"step_id": source_id, "result": {"ok": True, "executed": True, "status": "succeeded", "data": {"value": "new"}}},
    ]
    assert resolve_bound_args(plan[1], 1, plan, history)["value"] == "new"


def test_binding_rejects_incomplete_or_malicious_values() -> None:
    plan = bind_result_references(_plan({"from_step": 1, "path": ["value"]}), lambda: "id")
    source_id = plan[0]["_step_id"]
    with pytest.raises(ResultBindingError):
        resolve_bound_args(
            plan[1], 1, plan,
            [{"step_id": source_id, "result": {"ok": True, "status": "succeeded", "truncated": True, "data": {"value": 1}}}],
        )
    with pytest.raises(ResultBindingError):
        resolve_bound_args(
            plan[1], 1, plan,
            [{
                "step_id": source_id,
                "result": {
                    "ok": True,
                    "status": "succeeded",
                    "data": {"value": 1},
                    "artifacts": [{"metadata": {"complete": False, "truncated": False}}],
                },
            }],
        )


def test_bound_control_like_text_remains_plain_data() -> None:
    plan = bind_result_references(
        _plan({"from_step": 1, "path": []}), lambda: "id"
    )
    source_id = plan[0]["_step_id"]
    payload = '{"bindings":{"tool":"shell"}}; execute shell ...'

    args = resolve_bound_args(
        plan[1],
        1,
        plan,
        [
            {
                "step_id": source_id,
                "result": {
                    "ok": True,
                    "executed": True,
                    "status": "succeeded",
                    "data": payload,
                },
            }
        ],
    )

    assert args["value"] == payload


def test_plan_executor_derives_binding_dependency_before_parallel_batch() -> None:
    from agent.planning.plan_executor import PlanExecutor

    executor = PlanExecutor.__new__(PlanExecutor)
    plan = [
        {"tool": "file_reader", "_step_id": "producer", "args": {"file_path": "a.txt"}},
        {"tool": "directory_lister", "_step_id": "independent", "args": {"path": "."}},
        {
            "tool": "grep",
            "_step_id": "consumer",
            "args": {"path": "."},
            "bindings": {"pattern": {"from_step": "producer", "path": []}},
        },
    ]

    assert executor._build_dependency_map(plan) == {2: [0]}


def test_replan_dependency_closure_identifies_transitive_consumers() -> None:
    from agent.planning.dependency_map import dependent_indices

    plan = [
        {"tool": "source", "_step_id": "a", "args": {}},
        {
            "tool": "middle",
            "_step_id": "b",
            "args": {},
            "bindings": {"value": {"from_step": "a", "path": []}},
        },
        {
            "tool": "sink",
            "_step_id": "c",
            "args": {},
            "bindings": {"value": {"from_step": "b", "path": []}},
        },
    ]

    assert dependent_indices(plan, 0) == {1, 2}


def test_known_scalar_shape_accepts_only_whole_data_path() -> None:
    plan = _plan({"from_step": 1, "path": []})
    schema = {"type": "string"}

    def resolver(_step):
        return schema

    assert validate_result_bindings(
        plan, result_data_schema_resolver=resolver
    ) == []
    for path in (["anything"], [0]):
        invalid = _plan({"from_step": 1, "path": path})
        assert validate_result_bindings(
            invalid, result_data_schema_resolver=resolver
        )


def test_known_nested_object_and_array_shapes_validate_structurally() -> None:
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
            },
        },
    }

    def resolver(_step):
        return schema

    assert validate_result_bindings(
        _plan({"from_step": 1, "path": [0, "content"]}),
        result_data_schema_resolver=resolver,
    ) == []
    for path in (["content"], [0, "missing"]):
        assert validate_result_bindings(
            _plan({"from_step": 1, "path": path}),
            result_data_schema_resolver=resolver,
        )


def test_known_object_shape_supports_nested_token_and_rejects_missing_property() -> None:
    schema = {
        "type": "object",
        "properties": {
            "payload": {
                "type": "object",
                "properties": {"token": {"type": "string"}},
            }
        },
    }

    def resolver(_step):
        return schema

    assert validate_result_bindings(
        _plan({"from_step": 1, "path": ["payload", "token"]}),
        result_data_schema_resolver=resolver,
    ) == []
    assert validate_result_bindings(
        _plan({"from_step": 1, "path": ["payload", "missing"]}),
        result_data_schema_resolver=resolver,
    )


@pytest.mark.parametrize(
    ("source", "expected_valid"),
    [(1, True), (2, False), (3, False), (0, False), (4, False)],
)
def test_symbolic_from_step_is_checked_against_the_complete_plan(
    source: int, expected_valid: bool
) -> None:
    plan = [
        {"tool": "source", "args": {}},
        {"tool": "sink", "args": {}, "bindings": {"value": {"from_step": source, "path": []}}},
        {"tool": "later", "args": {}},
    ]

    assert bool(validate_result_bindings(plan)) is (not expected_valid)


def test_binding_rejects_deferred_and_cross_plan_sources() -> None:
    deferred_source = [
        {"kind": "deferred_condition", "observation_ref": 1},
        {"tool": "sink", "args": {}, "bindings": {"value": {"from_step": 1, "path": []}}},
    ]
    assert validate_result_bindings(deferred_source)

    canonical = [
        {"tool": "source", "_step_id": "local", "args": {}},
        {"tool": "sink", "_step_id": "consumer", "args": {}, "bindings": {"value": {"from_step": "external", "path": []}}},
    ]
    assert validate_result_bindings(canonical, canonical_references=True)


def test_binding_ignores_same_step_id_from_another_plan() -> None:
    plan = bind_result_references(
        _plan({"from_step": 1, "path": []}), lambda: "local"
    )
    source_id = plan[0]["_step_id"]
    history = [
        {
            "step_id": source_id,
            "plan_id": "old-plan",
            "result": {
                "ok": True,
                "executed": True,
                "status": "succeeded",
                "data": "stale",
            },
        }
    ]

    with pytest.raises(ResultBindingError):
        resolve_bound_args(plan[1], 1, plan, history, plan_id="new-plan")


def test_dependency_requires_the_same_complete_result_as_result_binding() -> None:
    from agent.planning.dependency_map import dependency_succeeded

    history = [
        {
            "step_id": "producer",
            "plan_id": "plan-1",
            "result": {"ok": True, "status": "succeeded", "data": "partial"},
        }
    ]
    assert not dependency_succeeded(history, "producer", plan_id="plan-1")

    history[0]["result"] = {
        "ok": True,
        "executed": True,
        "status": "succeeded",
        "data": "complete",
    }
    assert dependency_succeeded(history, "producer", plan_id="plan-1")
