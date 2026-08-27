from pathlib import Path

from agent.planning.planning_context import validate_planning_tool_arguments
from agent.resources.contracts import ResourceMode
from agent.skills import load_skill_registry
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.contracts import CancellationSafetyMode, ToolInvocation, ToolStatus
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.invocation_semantics import resolve_invocation_semantics


def test_builtin_adapter_descriptors(tmp_path: Path) -> None:
    skill_registry = load_skill_registry(base_dir=tmp_path)
    adapter = BuiltinToolAdapter(skill_registry)
    descriptors = adapter.descriptors()

    assert len(descriptors) > 0
    names = [d.name for d in descriptors]
    assert "echo" in names
    assert "file_reader" in names


def test_builtin_result_data_shapes_are_projected_from_the_catalog(tmp_path: Path) -> None:
    descriptors = BuiltinToolAdapter(load_skill_registry(base_dir=tmp_path)).descriptors()
    by_name = {descriptor.name: descriptor for descriptor in descriptors}

    assert by_name["file_reader"].result_data_schema == {"type": "string"}
    assert by_name["grep"].result_data_schema == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "line": {"type": "integer"},
                "content": {"type": "string"},
            },
        },
    }


def test_builtin_mutators_declare_cancellation_safety_and_readers_stay_read_only(
    tmp_path: Path,
) -> None:
    descriptors = BuiltinToolAdapter(load_skill_registry(base_dir=tmp_path)).descriptors()
    by_name = {descriptor.name: descriptor for descriptor in descriptors}

    assert by_name["code_task"].cancellation_safety is CancellationSafetyMode.UNSUPPORTED
    assert by_name["session_memory"].cancellation_safety is CancellationSafetyMode.BOUNDED_COOPERATIVE
    assert by_name["file_writer"].cancellation_safety is CancellationSafetyMode.UNSUPPORTED
    assert "write" not in by_name["shell"].capabilities
    assert by_name["git_reader"].cancellation_safety is CancellationSafetyMode.PROCESS_KILLABLE
    assert by_name["python_executor"].cancellation_safety is CancellationSafetyMode.PROCESS_KILLABLE
    assert ToolInvocationGateway._descriptor_may_mutate(
        by_name["code_task"], {"action": "analyze"}
    ) is False
    assert ToolInvocationGateway._descriptor_may_mutate(
        by_name["code_task"], {"action": "modify"}
    ) is True
    assert ToolInvocationGateway._descriptor_may_mutate(
        by_name["session_memory"], {"action": "get"}
    ) is False
    assert ToolInvocationGateway._descriptor_may_mutate(
        by_name["session_memory"], {"action": "set"}
    ) is True


def test_invocation_semantics_separate_external_and_workspace_domains(
    tmp_path: Path,
) -> None:
    descriptors = BuiltinToolAdapter(load_skill_registry(base_dir=tmp_path)).descriptors()
    by_name = {descriptor.name: descriptor for descriptor in descriptors}

    web = resolve_invocation_semantics(by_name["web_search"], {"query": "status"})
    assert web.required_capabilities == frozenset({"network"})
    assert web.external_side_effects == ("network",)
    assert web.workspace_mutation is False
    assert web.may_mutate is False
    assert not any(access.mode is ResourceMode.WRITE for access in web.resource_access)

    python = resolve_invocation_semantics(
        by_name["python_executor"], {"code": "print(1)"}
    )
    assert python.required_capabilities == frozenset({"process"})
    assert python.external_side_effects == ("process",)
    assert python.workspace_mutation is False
    assert python.resource_access == ()

    shell = resolve_invocation_semantics(
        by_name["shell"], {"command": "git log -1"}
    )
    git = resolve_invocation_semantics(by_name["git_reader"], {"action": "log"})
    assert all(access.mode is ResourceMode.READ for access in shell.resource_access)
    assert all(access.mode is ResourceMode.READ for access in git.resource_access)

    memory = resolve_invocation_semantics(
        by_name["session_memory"], {"action": "set", "key": "k", "value": "v"}
    )
    assert memory.task_state_mutation is True
    assert memory.workspace_mutation is False
    assert {access.name for access in memory.resource_access} == {"memory"}
    assert all(access.mode is ResourceMode.WRITE for access in memory.resource_access)

    writer = resolve_invocation_semantics(
        by_name["file_writer"], {"file_path": "foo.py", "content": "x"}
    )
    reader = resolve_invocation_semantics(
        by_name["file_reader"], {"file_path": "foo.py"}
    )
    assert writer.workspace_mutation is True
    assert [(access.name, access.mode) for access in writer.resource_access] == [
        ("foo.py", ResourceMode.WRITE)
    ]
    assert [(access.name, access.mode) for access in reader.resource_access] == [
        ("foo.py", ResourceMode.READ)
    ]

    analyze = resolve_invocation_semantics(
        by_name["code_task"], {"action": "analyze", "targets": ["foo.py"]}
    )
    modify = resolve_invocation_semantics(
        by_name["code_task"], {"action": "modify", "targets": ["foo.py"]}
    )
    assert analyze.workspace_mutation is False
    assert modify.workspace_mutation is True


def test_code_task_descriptor_is_canonical_and_rejects_writer_arguments(
    tmp_path: Path,
) -> None:
    descriptor = next(
        item
        for item in BuiltinToolAdapter(
            load_skill_registry(base_dir=tmp_path)
        ).descriptors()
        if item.name == "code_task"
    )

    assert descriptor.schema["type"] == "object"
    assert descriptor.schema["required"] == ["action"]
    assert descriptor.schema["additionalProperties"] is False
    validate_planning_tool_arguments(
        descriptor,
        {
            "action": "modify",
            "objective": "alterar",
            "targets": ["controle.txt"],
        },
    )
    try:
        validate_planning_tool_arguments(
            descriptor,
            {"action": "modify", "target": "controle.txt", "content": "novo"},
        )
    except ValueError as exc:
        assert "unknown argument" in str(exc)
    else:
        raise AssertionError("writer arguments must not validate as code_task")


def test_builtin_adapter_invoke_success(tmp_path: Path) -> None:
    skill_registry = load_skill_registry(base_dir=tmp_path)
    adapter = BuiltinToolAdapter(skill_registry)

    invocation = ToolInvocation(tool_name="echo", args={"message": "hello world"})
    result = adapter.invoke(invocation)

    assert result.ok is True
    assert result.status == ToolStatus.SUCCEEDED
    assert result.data == "hello world"


def test_file_reader_adapter_marks_only_integral_text_as_complete(
    tmp_path: Path,
) -> None:
    (tmp_path / "small.txt").write_text("original", encoding="utf-8")
    (tmp_path / "large.txt").write_text("x" * 25_000, encoding="utf-8")
    adapter = BuiltinToolAdapter(load_skill_registry(base_dir=tmp_path))

    complete = adapter.invoke(
        ToolInvocation(tool_name="file_reader", args={"file_path": "small.txt"})
    )
    summarized = adapter.invoke(
        ToolInvocation(tool_name="file_reader", args={"file_path": "large.txt"})
    )

    assert complete.artifacts[0]["metadata"]["complete"] is True
    assert summarized.artifacts[0]["metadata"]["complete"] is False


def test_builtin_adapter_preserves_partial_search_metadata(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("needle", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle", encoding="utf-8")
    result = BuiltinToolAdapter(load_skill_registry(base_dir=tmp_path)).invoke(
        ToolInvocation(
            tool_name="grep",
            args={"path": ".", "pattern": "needle", "max_results": 1},
        )
    )

    assert result.ok is True
    assert result.artifacts[0]["metadata"]["truncated"] is True
    assert result.artifacts[0]["metadata"]["total_matches"] == 1


def test_adapter_does_not_misclassify_normalized_output_as_incomplete() -> None:
    complete = BuiltinToolAdapter._observation_artifacts(
        "shell",
        {"total_chars": 6, "truncated": False},
        "hello",
    )
    truncated = BuiltinToolAdapter._observation_artifacts(
        "shell",
        {"total_chars": 6, "truncated": True},
        "hello",
    )

    assert complete[0]["metadata"]["complete"] is True
    assert truncated[0]["metadata"]["complete"] is False



def test_builtin_adapter_invoke_unavailable(tmp_path: Path) -> None:
    skill_registry = load_skill_registry(base_dir=tmp_path)
    adapter = BuiltinToolAdapter(skill_registry)

    invocation = ToolInvocation(tool_name="non_existent_tool", args={})
    result = adapter.invoke(invocation)

    assert result.ok is False
    assert result.status == ToolStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "TOOL_NOT_FOUND"
