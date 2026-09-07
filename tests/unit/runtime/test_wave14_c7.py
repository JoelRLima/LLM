"""Wave 14 C7 residual-closure adversarial campaign."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.cancellation import CancellationToken
from agent.interaction.intent_claim import ConstraintClaim
from agent.interaction.semantic_observability import (
    emit_semantic_admission_denied_event,
    emit_semantic_parse_event,
)
from agent.orchestration.task_execution import _resume_task
from agent.planning.effect_intent import effect_intent_error
from agent.planning.effect_intent_constraints import semantic_effect_error
from agent.planning.graph_authority import GraphAuthorityError, preflight_graph_capabilities
from agent.planning.intent_admission import AuthorityEnvelope, admit_intent_claim
from agent.planning.target_grounding import GroundingError, ground_intent_claim, revalidate_grounded_targets
from agent.planning.task_graph import TaskGraph, TaskNode
from agent.planning.task_scheduler import TaskGraphScheduler
from agent.resources.contracts import ResourceAccess, ResourceMode, ResourceProvenance
from agent.runtime.context import TaskExecutionContext, TaskResult, TaskStatus
from agent.runtime.task_directives import TaskDirective, TaskRunDirective
from agent.state import AgentState
from agent.tools.invocation_semantics import resolve_invocation_semantics
from tests.unit.runtime.test_wave14_c6 import _checkpoint_state
from tests.unit.runtime.test_wave14_c6 import _claim as make_claim


class _Gateway:
    provider_name = "wave14-c7"
    capabilities = SimpleNamespace()


def _envelope(
    root: Path | None = None,
    *,
    permissions: tuple[str, ...] = ("read", "write", "validate"),
    read: tuple[str, ...] = ("src",),
    write: tuple[str, ...] = ("src",),
    effects: tuple[str, ...] = ("write",),
) -> AuthorityEnvelope:
    return AuthorityEnvelope(
        parent_permissions=permissions,
        granted_effects=effects,
        read_resources=tuple(
            ResourceAccess(item, ResourceMode.READ, ResourceProvenance.TRUSTED_DERIVED)
            for item in read
        ),
        write_resources=tuple(
            ResourceAccess(item, ResourceMode.WRITE, ResourceProvenance.TRUSTED_DERIVED)
            for item in write
        ),
        workspace_root=str(root) if root is not None else None,
    )


def _context(permissions: set[str], *, strict: bool = True) -> TaskExecutionContext:
    return TaskExecutionContext(
        model_gateway=_Gateway(),
        cancellation=CancellationToken(),
        permissions=frozenset(permissions),
        metadata={"w14_semantic_task": True} if strict else {},
    )


def _resume_fixture(
    tmp_path: Path,
    *,
    constraints: tuple[ConstraintClaim, ...] = (),
) -> tuple[SimpleNamespace, SimpleNamespace, Path]:
    source = tmp_path / "src" / "settings.py"
    source.parent.mkdir()
    source.write_text("TIMEOUT = 1\n", encoding="utf-8")
    subject = "change TIMEOUT"
    claim = make_claim(subject, constraints=constraints)
    seeded = _checkpoint_state(claim, _envelope(tmp_path), subject)
    restored = AgentState()
    restored.from_checkpoint_dict(seeded.to_checkpoint_dict())
    events: list[tuple[str, dict[str, object]]] = []
    orchestrator = SimpleNamespace(
        agent_state=restored,
        allowed_capabilities={"read", "write", "validate"},
        read_resources=None,
        write_resources=None,
        workspace_root=tmp_path,
        task_authority=None,
        application_authority=None,
        _task_execution_context=None,
        _authority_envelope=None,
        _admitted_intent=None,
        _grounded_targets=None,
        _emit=lambda kind, data: events.append((kind, data)),
        _restore_persona_from_state=lambda: None,
    )
    runner = SimpleNamespace(
        orchestrator=orchestrator,
        _execute_plan=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("planner/executor was reached")
        ),
    )
    inputs = SimpleNamespace(resumed=True, objective=subject)
    return runner, inputs, source


def _symbol_grounding(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    claim = make_claim()
    envelope = _envelope(tmp_path)
    admitted, grounded = ground_intent_claim(
        claim,
        envelope,
        current_subject="change TIMEOUT",
        workspace_root=tmp_path,
    )
    return envelope, admitted, grounded


def _runtime_orchestrator(root: Path, permissions: set[str]) -> tuple[SimpleNamespace, list[tuple[str, dict[str, object]]]]:
    state = AgentState()
    events: list[tuple[str, dict[str, object]]] = []
    return (
        SimpleNamespace(
            agent_state=state,
            allowed_capabilities=permissions,
            read_resources=None,
            write_resources=None,
            workspace_root=root,
            task_authority=None,
            application_authority=None,
            _task_execution_context=None,
            _emit=lambda kind, data: events.append((kind, data)),
        ),
        events,
    )


def test_c7_a01_resume_no_plan_restores_before_planner(tmp_path: Path) -> None:
    runner, inputs, _ = _resume_fixture(tmp_path)
    result = _resume_task(runner, inputs, None, runner.orchestrator.agent_state.task_run_directive, {})
    assert result is None
    assert runner.orchestrator._admitted_intent is not None
    assert runner.orchestrator._grounded_targets is not None


def test_c7_a02_resume_no_plan_preserves_proposal_only(tmp_path: Path) -> None:
    runner, inputs, _ = _resume_fixture(
        tmp_path,
        constraints=(ConstraintClaim("proposal_only", ("e1",)),),
    )
    assert _resume_task(runner, inputs, None, runner.orchestrator.agent_state.task_run_directive, {}) is None
    assert runner.orchestrator._admitted_intent.proposal_only is True


def test_c7_a03_resume_no_plan_preserves_required_validation(tmp_path: Path) -> None:
    runner, inputs, _ = _resume_fixture(
        tmp_path,
        constraints=(ConstraintClaim("require_validation", ("e1",)),),
    )
    assert _resume_task(runner, inputs, None, runner.orchestrator.agent_state.task_run_directive, {}) is None
    assert runner.orchestrator._admitted_intent.requires_validation is True


def test_c7_a04_resume_no_plan_narrower_authority_blocks_before_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, inputs, _ = _resume_fixture(tmp_path)
    runner.orchestrator.allowed_capabilities = {"read", "validate"}
    import agent.orchestration.task_execution_authority as authority

    monkeypatch.setattr(authority, "mark_terminal_blocked", lambda _owner, **kwargs: kwargs["reason_code"])
    result = _resume_task(runner, inputs, None, runner.orchestrator.agent_state.task_run_directive, {})
    assert str(result) in {"INTENT_CAPABILITY_DENIED", "INTENT_EFFECT_NOT_GRANTED"}


def test_c7_a05_resume_no_plan_ambiguous_symbol_blocks_before_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, inputs, source = _resume_fixture(tmp_path)
    (source.parent / "other.py").write_text("TIMEOUT = 2\n", encoding="utf-8")
    import agent.orchestration.task_execution_authority as authority

    monkeypatch.setattr(authority, "mark_terminal_blocked", lambda _owner, **kwargs: kwargs["reason_code"])
    result = _resume_task(runner, inputs, None, runner.orchestrator.agent_state.task_run_directive, {})
    assert "GROUNDING_AMBIGUOUS" in str(result)


def test_c7_a06_w14_marker_missing_continuation_denies_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, inputs, _ = _resume_fixture(tmp_path)
    runner.orchestrator.agent_state.w14_intent_continuation = None
    import agent.orchestration.task_execution_authority as authority

    monkeypatch.setattr(authority, "mark_terminal_blocked", lambda _owner, **kwargs: kwargs["reason_code"])
    result = _resume_task(runner, inputs, None, runner.orchestrator.agent_state.task_run_directive, {})
    assert result == "W14_CONTINUATION_MISSING"


def test_c7_a07_pre_w14_resume_uses_legacy_compatibility_path(tmp_path: Path) -> None:
    runner, inputs, _ = _resume_fixture(tmp_path)
    runner.orchestrator.agent_state.w14_semantic_task = False
    runner.orchestrator.agent_state.w14_intent_continuation = None
    assert _resume_task(runner, inputs, None, runner.orchestrator.agent_state.task_run_directive, {}) is None
    assert runner.orchestrator._admitted_intent is None


def test_c7_a08_do_network_only_admits_without_read() -> None:
    claim = make_claim("do nothing", operation="do", effect=None)
    admitted = admit_intent_claim(
        claim,
        _envelope(permissions=("network",), read=(), write=(), effects=()),
        current_subject="do nothing",
    )
    assert admitted.capabilities == set()


def test_c7_a09_network_invocation_is_checked_at_graph_semantics() -> None:
    descriptor = SimpleNamespace(name="network_tool", capabilities=frozenset({"network"}))
    graph = TaskGraph("network", (TaskNode("n", "network", metadata={"tool": "network_tool", "action": "call"}),))
    assert preflight_graph_capabilities(graph, {"network"}, {"network_tool": descriptor}, strict_w14=True).required_capabilities == {"network"}
    with pytest.raises(GraphAuthorityError, match="network"):
        preflight_graph_capabilities(graph, {"read"}, {"network_tool": descriptor}, strict_w14=True)


def test_c7_a10_do_process_only_admits_and_process_is_invocation_requirement() -> None:
    claim = make_claim("do nothing", operation="do", effect=None)
    admitted = admit_intent_claim(
        claim,
        _envelope(permissions=("process",), read=(), write=(), effects=()),
        current_subject="do nothing",
    )
    assert admitted.capabilities == set()
    descriptor = SimpleNamespace(name="process_tool", capabilities=frozenset({"process"}))
    semantics = resolve_invocation_semantics(descriptor, {"action": "run"})
    assert semantics.required_capabilities == {"process"}


def test_c7_a11_package_only_do_does_not_invent_read() -> None:
    admitted = admit_intent_claim(
        make_claim("do nothing", operation="do", effect=None),
        _envelope(permissions=("package_install",), read=(), write=(), effects=()),
        current_subject="do nothing",
    )
    assert "read" not in admitted.capabilities


def test_c7_a12_do_without_write_rejects_later_filesystem_write() -> None:
    admitted = admit_intent_claim(
        make_claim("do nothing", effect=None),
        _envelope(permissions=("read",), effects=()),
        current_subject="do nothing",
    )
    descriptor = SimpleNamespace(name="code_task", capabilities={"read", "write", "analyze"})
    assert "UNREQUESTED_EFFECT" in str(
        effect_intent_error("do nothing", "code_task", {"action": "modify"}, descriptor, admitted_intent=admitted)
    )


def test_c7_a13_do_without_memory_write_rejects_later_memory_write() -> None:
    admitted = admit_intent_claim(
        make_claim("do nothing", effect=None),
        _envelope(permissions=("memory",), read=(), write=(), effects=()),
        current_subject="do nothing",
    )
    error = semantic_effect_error(
        "memory_tool",
        "memory_write",
        (ResourceAccess("memory", ResourceMode.WRITE),),
        admitted,
        None,
        SimpleNamespace(action="memory_write", requires_validation=False),
    )
    assert "UNREQUESTED_EFFECT" in str(error)


def test_c7_a14_successful_admission_capabilities_are_parent_subset() -> None:
    cases = (
        (make_claim("read this", operation="read", effect=None), _envelope(permissions=("read",), read=(), write=(), effects=())),
        (make_claim("do nothing", operation="do", effect=None), _envelope(permissions=("network",), read=(), write=(), effects=())),
        (make_claim(), _envelope(permissions=("read", "vcs_write"), effects=("write",))),
    )
    for claim, envelope in cases:
        admitted = admit_intent_claim(claim, envelope, current_subject=claim.subject if hasattr(claim, "subject") else ("read this" if claim.operation == "read" else "change TIMEOUT"))
        assert admitted.capabilities.issubset(envelope.parent_permissions)


def test_c7_a15_vcs_write_ceiling_does_not_add_write() -> None:
    admitted = admit_intent_claim(
        make_claim(),
        _envelope(permissions=("vcs_write",), read=(), write=(), effects=("write",)),
        current_subject="change TIMEOUT",
    )
    assert admitted.capabilities == set()


def test_c7_a16_filesystem_write_action_requires_exact_write() -> None:
    descriptor = SimpleNamespace(name="filesystem", capabilities=frozenset({"write"}))
    assert resolve_invocation_semantics(descriptor, {"action": "write"}).required_capabilities == {"write"}


def test_c7_a17_vcs_action_requires_exact_vcs_write() -> None:
    descriptor = SimpleNamespace(name="vcs", capabilities=frozenset({"vcs_write"}))
    assert resolve_invocation_semantics(descriptor, {"action": "commit"}).required_capabilities == {"vcs_write"}


def test_c7_a18_semantic_write_grants_neither_exact_write_capability() -> None:
    admitted = admit_intent_claim(
        make_claim(),
        _envelope(permissions=("read", "write", "vcs_write"), effects=("write",)),
        current_subject="change TIMEOUT",
    )
    assert not ({"write", "vcs_write"} & set(admitted.capabilities))


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[TaskExecutionContext] = []

    def execute(self, _node: TaskNode, context: TaskExecutionContext) -> TaskResult:
        self.calls.append(context)
        return TaskResult(TaskStatus.SUCCEEDED, summary="ok")


def test_c7_a19_strict_default_analyze_ignores_empty_declaration() -> None:
    executor = _RecordingExecutor()
    graph = TaskGraph("analyze", (TaskNode("n", "analyze", capabilities=frozenset()),))
    result = TaskGraphScheduler(executor).execute(graph, _context({"read", "analyze"}))
    assert result.succeeded
    assert executor.calls[0].permissions == {"read", "analyze"}


def test_c7_a20_strict_default_analyze_denies_missing_analyze_preflight() -> None:
    executor = _RecordingExecutor()
    graph = TaskGraph("analyze", (TaskNode("n", "analyze", capabilities=frozenset()),))
    with pytest.raises(GraphAuthorityError, match="analyze"):
        TaskGraphScheduler(executor).execute(graph, _context({"read"}))
    assert executor.calls == []


def test_c7_a21_false_write_declaration_does_not_widen_analyze_child() -> None:
    executor = _RecordingExecutor()
    graph = TaskGraph("analyze", (TaskNode("n", "analyze", capabilities=frozenset({"write"})),))
    result = TaskGraphScheduler(executor).execute(graph, _context({"read", "analyze"}))
    assert result.succeeded
    assert "write" not in executor.calls[0].permissions


def test_c7_a22_omitted_trusted_requirement_does_not_block_granted_parent() -> None:
    executor = _RecordingExecutor()
    graph = TaskGraph("analyze", (TaskNode("n", "analyze"),))
    result = TaskGraphScheduler(executor).execute(graph, _context({"read", "analyze"}))
    assert result.succeeded


def test_c7_a23_legacy_graph_requirement_path_remains_explicit() -> None:
    graph = TaskGraph("legacy", (TaskNode("n", "legacy", capabilities=frozenset({"read"})),))
    result = preflight_graph_capabilities(graph, {"read"}, strict_w14=False)
    assert result.required_capabilities == {"read"}
    assert result.node_requirements[0].source == "legacy-explicit-request"


def test_c7_a24_non_python_enumeration_limit_is_enforced_during_walk(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    for index in range(5):
        (source / f"file_{index}.txt").write_text("no symbol", encoding="utf-8")
    (source / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    with pytest.raises(GroundingError, match="GROUNDING_DISCOVERY_LIMIT"):
        ground_intent_claim(
            make_claim(),
            _envelope(tmp_path),
            current_subject="change TIMEOUT",
            workspace_root=tmp_path,
            max_enumerated_paths=2,
        )


def test_c7_a25_excluded_directories_do_not_enter_source_inventory(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    for index in range(20):
        (tmp_path / ".venv" / "lib" / f"polluted_{index}.py").write_text("TIMEOUT = 2\n", encoding="utf-8")
    _, grounded = ground_intent_claim(
        make_claim(),
        _envelope(tmp_path, read=("*",)),
        current_subject="change TIMEOUT",
        workspace_root=tmp_path,
    )
    assert grounded.resources == ("src/settings.py",)


def test_c7_a26_overlapping_authorized_roots_are_walked_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "src" / "nested").mkdir(parents=True)
    (tmp_path / "src" / "nested" / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    envelope = _envelope(tmp_path, read=("src", "src/nested"), write=("src",))
    import agent.planning.target_grounding_discovery as discovery

    calls: list[str] = []
    original = discovery.os.scandir

    def record(path, *args, **kwargs):
        calls.append(str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(discovery.os, "scandir", record)
    ground_intent_claim(make_claim(), envelope, current_subject="change TIMEOUT", workspace_root=tmp_path)
    assert calls.count(str(tmp_path / "src")) == 1
    assert calls.count(str(tmp_path / "src" / "nested")) == 1


def test_c7_a27_repo_sized_inventory_remains_bounded_and_usable(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    for index in range(513):
        (source / f"unrelated_{index:04d}.py").write_text(f"VALUE_{index} = {index}\n", encoding="utf-8")
    (source / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    _, grounded = ground_intent_claim(make_claim(), _envelope(tmp_path), current_subject="change TIMEOUT", workspace_root=tmp_path)
    assert grounded.resources == ("src/settings.py",)


def test_c7_a28_oversized_source_with_symbol_is_unclassifiable(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    (source / "large.py").write_text("x = 1\n" * 100 + "TIMEOUT = 2\n", encoding="utf-8")
    with pytest.raises(GroundingError, match="GROUNDING_SOURCE_UNCLASSIFIABLE"):
        ground_intent_claim(make_claim(), _envelope(tmp_path), current_subject="change TIMEOUT", workspace_root=tmp_path, max_source_bytes=32)


def test_c7_a29_oversized_source_without_symbol_is_safe_negative_evidence(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    (source / "large.py").write_text("x = 1\n" * 100, encoding="utf-8")
    _, grounded = ground_intent_claim(make_claim(), _envelope(tmp_path), current_subject="change TIMEOUT", workspace_root=tmp_path, max_source_bytes=32)
    assert grounded.resources == ("src/settings.py",)


def test_c7_a30_syntax_invalid_source_with_symbol_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    (source / "broken.py").write_text("TIMEOUT = 2\nif (\n", encoding="utf-8")
    with pytest.raises(GroundingError, match="GROUNDING_SOURCE_UNCLASSIFIABLE"):
        ground_intent_claim(make_claim(), _envelope(tmp_path), current_subject="change TIMEOUT", workspace_root=tmp_path)


def test_c7_a31_syntax_invalid_source_without_symbol_is_skippable(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    (source / "broken.py").write_text("OTHER = 2\nif (\n", encoding="utf-8")
    _, grounded = ground_intent_claim(make_claim(), _envelope(tmp_path), current_subject="change TIMEOUT", workspace_root=tmp_path)
    assert grounded.resources == ("src/settings.py",)


def test_c7_a32_unreadable_authorized_source_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    unreadable = source / "broken.py"
    unreadable.write_text("OTHER = 2\n", encoding="utf-8")
    original = Path.open

    def fail(path: Path, *args, **kwargs):
        if path == unreadable:
            raise OSError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(GroundingError, match="GROUNDING_SOURCE_UNREADABLE"):
        ground_intent_claim(make_claim(), _envelope(tmp_path), current_subject="change TIMEOUT", workspace_root=tmp_path)


def test_c7_a33_first_chained_assignment_target_is_classified(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").write_text("DEFAULT_TIMEOUT = OTHER = 1\n", encoding="utf-8")
    _, grounded = ground_intent_claim(make_claim("change DEFAULT_TIMEOUT", selector_value="DEFAULT_TIMEOUT"), _envelope(tmp_path), current_subject="change DEFAULT_TIMEOUT", workspace_root=tmp_path)
    assert grounded.resources == ("src/settings.py",)


def test_c7_a34_second_chained_assignment_target_is_classified(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").write_text("OTHER = DEFAULT_TIMEOUT = 1\n", encoding="utf-8")
    _, grounded = ground_intent_claim(make_claim("change DEFAULT_TIMEOUT", selector_value="DEFAULT_TIMEOUT"), _envelope(tmp_path), current_subject="change DEFAULT_TIMEOUT", workspace_root=tmp_path)
    assert grounded.resources == ("src/settings.py",)


def test_c7_a35_destructuring_assignment_target_is_classified(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").write_text("DEFAULT_TIMEOUT, OTHER = (1, 2)\n", encoding="utf-8")
    _, grounded = ground_intent_claim(make_claim("change DEFAULT_TIMEOUT", selector_value="DEFAULT_TIMEOUT"), _envelope(tmp_path), current_subject="change DEFAULT_TIMEOUT", workspace_root=tmp_path)
    assert grounded.resources == ("src/settings.py",)


def test_c7_a36_chained_duplicate_is_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").write_text("DEFAULT_TIMEOUT = 1\n", encoding="utf-8")
    (tmp_path / "src" / "other.py").write_text("OTHER = DEFAULT_TIMEOUT = 2\n", encoding="utf-8")
    with pytest.raises(GroundingError, match="GROUNDING_AMBIGUOUS"):
        ground_intent_claim(make_claim("change DEFAULT_TIMEOUT", selector_value="DEFAULT_TIMEOUT"), _envelope(tmp_path), current_subject="change DEFAULT_TIMEOUT", workspace_root=tmp_path)


def test_c7_a37_nested_qualified_symbol_preserves_scope(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").write_text("class Settings:\n    DEFAULT_TIMEOUT = 1\n", encoding="utf-8")
    _, grounded = ground_intent_claim(make_claim("change Settings.DEFAULT_TIMEOUT", selector_value="Settings.DEFAULT_TIMEOUT"), _envelope(tmp_path), current_subject="change Settings.DEFAULT_TIMEOUT", workspace_root=tmp_path)
    assert grounded.targets[0].definition_line == 2


def test_c7_a38_new_global_duplicate_fails_revalidation(tmp_path: Path) -> None:
    envelope, admitted, grounded = _symbol_grounding(tmp_path)
    (tmp_path / "src" / "other.py").write_text("TIMEOUT = 2\n", encoding="utf-8")
    with pytest.raises(GroundingError, match="GROUNDING_AMBIGUOUS"):
        revalidate_grounded_targets(grounded, tmp_path, envelope=envelope, admitted_intent=admitted)


def test_c7_a39_unrelated_changed_file_keeps_unique_grounding_valid(tmp_path: Path) -> None:
    envelope, admitted, grounded = _symbol_grounding(tmp_path)
    other = tmp_path / "src" / "other.py"
    other.write_text("VALUE = 1\n", encoding="utf-8")
    other.write_text("VALUE = 2\n", encoding="utf-8")
    assert revalidate_grounded_targets(grounded, tmp_path, envelope=envelope, admitted_intent=admitted) is grounded


def test_c7_a40_new_chained_duplicate_fails_revalidation(tmp_path: Path) -> None:
    envelope, admitted, grounded = _symbol_grounding(tmp_path)
    (tmp_path / "src" / "other.py").write_text("OTHER = TIMEOUT = 2\n", encoding="utf-8")
    with pytest.raises(GroundingError, match="GROUNDING_AMBIGUOUS"):
        revalidate_grounded_targets(grounded, tmp_path, envelope=envelope, admitted_intent=admitted)


def test_c7_a41_new_uninspectable_source_fails_revalidation(tmp_path: Path) -> None:
    envelope, admitted, grounded = _symbol_grounding(tmp_path)
    (tmp_path / "src" / "broken.py").write_text("TIMEOUT = 2\nif (\n", encoding="utf-8")
    with pytest.raises(GroundingError, match="GROUNDING_SOURCE_UNCLASSIFIABLE"):
        revalidate_grounded_targets(grounded, tmp_path, envelope=envelope, admitted_intent=admitted)


def test_c7_a42_moved_sole_definition_is_stale(tmp_path: Path) -> None:
    envelope, admitted, grounded = _symbol_grounding(tmp_path)
    old = tmp_path / "src" / "settings.py"
    old.write_text("OTHER = 1\n", encoding="utf-8")
    (tmp_path / "src" / "moved.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    with pytest.raises(GroundingError, match="GROUNDING_STALE"):
        revalidate_grounded_targets(grounded, tmp_path, envelope=envelope, admitted_intent=admitted)


def test_c7_a43_parse_success_precedes_admission_success(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    orchestrator, events = _runtime_orchestrator(tmp_path, {"read", "write", "validate"})
    claim = make_claim()
    emit_semantic_parse_event(orchestrator, claim)
    from agent.orchestration.task_execution_authority import _admit_runtime_intent

    assert _admit_runtime_intent(
        orchestrator,
        TaskRunDirective(TaskDirective.DO, "normal", "change TIMEOUT", intent_claim=claim),
    ) is None
    kinds = [kind for kind, _data in events]
    assert kinds.index("semantic_intent_parsed") < kinds.index("semantic_intent_admitted")


def test_c7_a44_parse_success_and_admission_denial_are_both_observable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orchestrator, events = _runtime_orchestrator(tmp_path, {"read"})
    claim = make_claim()
    emit_semantic_parse_event(orchestrator, claim)
    import agent.orchestration.task_execution_authority as authority

    monkeypatch.setattr(authority, "mark_terminal_blocked", lambda _owner, **kwargs: kwargs["reason_code"])
    result = authority._admit_runtime_intent(
        orchestrator,
        TaskRunDirective(TaskDirective.DO, "normal", "change TIMEOUT", intent_claim=claim),
    )
    assert str(result) in {"INTENT_CAPABILITY_DENIED", "INTENT_EFFECT_NOT_GRANTED"}
    assert [kind for kind, _data in events] == [
        "semantic_intent_parsed",
        "semantic_intent_admission_denied",
    ]


def test_c7_a45_malformed_semantic_output_emits_parse_failure_only() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    application = SimpleNamespace(_emit=lambda kind, data: events.append((kind, data)))
    emit_semantic_parse_event(application, failure_reason="INTERACTION_RESOLVER_INVALID")
    assert len(events) == 1
    assert events[0][0] == "semantic_intent_parsed"
    assert events[0][1]["success"] is False


def test_c7_a46_semantic_audit_never_contains_raw_prompt_or_secret() -> None:
    secret = "SECRET_PROMPT_VALUE"
    subject = f"change {secret}"
    claim = make_claim(subject, selector_value=secret)
    events: list[tuple[str, dict[str, object]]] = []
    application = SimpleNamespace(_emit=lambda kind, data: events.append((kind, data)))
    emit_semantic_admission_denied_event(application, claim, "INTENT_CAPABILITY_DENIED")
    payload = json.dumps(events, ensure_ascii=False)
    assert secret not in payload
    assert "raw_prompt" not in payload
