from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.cancellation import CancellationToken
from agent.code.change_models import ChangeKind, ChangeSet, FileChange
from agent.code.change_transaction import ChangeSetTransaction
from agent.code.mutation_binding import MutationBindingError, assert_changeset_admitted
from agent.code.workflow_application_flow_support import run_apply_changes
from agent.interaction.intent_claim import (
    ConstraintClaim,
    EffectClaim,
    EvidenceSpan,
    IntentClaimV1,
    TargetSelectorClaim,
)
from agent.interaction.service import InteractionService
from agent.llm.contracts import ProviderCapabilities
from agent.orchestration.task_execution import _admit_runtime_intent
from agent.planning.effect_intent import effect_intent_error
from agent.planning.graph_authority import GraphAuthorityError, preflight_graph_capabilities
from agent.planning.intent_admission import (
    AuthorityEnvelope,
    IntentAdmissionError,
    admit_intent_claim,
    authority_envelope_from_orchestrator,
)
from agent.planning.intent_continuation import W14IntentContinuation
from agent.planning.target_grounding import (
    GroundingError,
    ground_intent_claim,
    revalidate_grounded_targets,
)
from agent.planning.task_completion import review_task_completion
from agent.planning.task_graph import TaskGraph, TaskNode
from agent.planning.task_scheduler import TaskGraphScheduler
from agent.resources.contracts import ResourceAccess, ResourceMode, ResourceProvenance
from agent.runtime.budget import TaskBudgetLedger
from agent.runtime.context import TaskExecutionContext, TaskResult, TaskStatus
from agent.runtime.task_directives import DeliberationProfile, TaskDirective, TaskRunDirective
from agent.runtime.task_execution_context import _authority_metadata
from agent.runtime.task_policy_support import refresh_orchestrator_task_policy
from agent.state import AgentState
from agent.tools.invocation_semantics import resolve_invocation_semantics


class _Gateway:
    provider_name = "wave14-c6"
    capabilities = ProviderCapabilities()


class _EventSink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


def _context(
    permissions: set[str] | frozenset[str],
    *,
    metadata: dict[str, object] | None = None,
    sink: _EventSink | None = None,
) -> TaskExecutionContext:
    return TaskExecutionContext(
        model_gateway=_Gateway(),
        cancellation=CancellationToken(),
        permissions=frozenset(permissions),
        metadata=dict(metadata or {}),
        event_sink=sink,
    )


def _envelope(
    root: Path | None = None,
    *,
    permissions: tuple[str, ...] = ("read", "write", "validate"),
    read: tuple[str, ...] = ("src",),
    write: tuple[str, ...] = ("src",),
    effects: tuple[str, ...] = ("write",),
    identity: str = "c6-authority",
) -> AuthorityEnvelope:
    return AuthorityEnvelope(
        parent_permissions=frozenset(permissions),
        granted_effects=frozenset(effects),
        read_resources=tuple(
            ResourceAccess(item, ResourceMode.READ, ResourceProvenance.TRUSTED_DERIVED)
            for item in read
        ),
        write_resources=tuple(
            ResourceAccess(item, ResourceMode.WRITE, ResourceProvenance.TRUSTED_DERIVED)
            for item in write
        ),
        workspace_root=str(root) if root is not None else None,
        authority_identity=identity,
    )


def _claim(
    subject: str = "change TIMEOUT",
    *,
    operation: str = "do",
    effect: str | None = "write",
    selector_kind: str = "symbol",
    selector_value: str = "TIMEOUT",
    selector_role: str = "mutation_target",
    constraints: tuple[ConstraintClaim, ...] = (),
    polarity: str = "requested",
) -> IntentClaimV1:
    spans: tuple[EvidenceSpan, ...]
    selectors: tuple[TargetSelectorClaim, ...]
    effects: tuple[EffectClaim, ...]
    if effect is None:
        spans = ()
        selectors = ()
        effects = ()
    else:
        start = subject.index(selector_value)
        span = EvidenceSpan("e1", start, start + len(selector_value), selector_value)
        spans = (span,)
        selectors = (
            TargetSelectorClaim(
                "s1", selector_kind, selector_value, selector_role, ("e1",)
            ),
        )
        effects = (EffectClaim(effect, polarity, ("s1",), ("e1",)),)
    return IntentClaimV1(operation, "none", effects, selectors, constraints, spans)


def _memory_claim() -> IntentClaimV1:
    return _claim(
        "remember memory",
        effect="memory_write",
        selector_kind="resource",
        selector_value="memory",
        selector_role="memory",
    )


def _grounded_w14_metadata(root: Path) -> tuple[AuthorityEnvelope, object, object]:
    subject = "change src/settings.py"
    claim = _claim(
        subject,
        selector_kind="path_literal",
        selector_value="src/settings.py",
    )
    envelope = _envelope(root)
    admitted, grounded = ground_intent_claim(
        claim,
        envelope,
        current_subject=subject,
        workspace_root=root,
    )
    return envelope, admitted, grounded


def _checkpoint_state(claim: IntentClaimV1, envelope: AuthorityEnvelope, subject: str) -> AgentState:
    state = AgentState()
    state.objective = subject
    state.task_run_directive = TaskRunDirective(
        TaskDirective.DO,
        DeliberationProfile.NORMAL,
        subject,
        intent_claim=claim,
    )
    admitted = admit_intent_claim(claim, envelope, current_subject=subject)
    state.w14_semantic_task = True
    state.w14_intent_continuation = W14IntentContinuation.from_claim(
        claim,
        subject=subject,
        admitted_intent=admitted,
    )
    return state


def test_c6_a01_w14_checkpoint_missing_continuation_fails_closed() -> None:
    subject = "do nothing"
    state = _checkpoint_state(_claim(subject, effect=None), _envelope(permissions=("read",), effects=()), subject)
    checkpoint = state.to_checkpoint_dict()
    checkpoint.pop("w14_intent_continuation")

    with pytest.raises(ValueError, match="continuation"):
        AgentState().from_checkpoint_dict(checkpoint)


def test_c6_a02_w14_checkpoint_malformed_or_unknown_version_fails_closed() -> None:
    subject = "do nothing"
    state = _checkpoint_state(_claim(subject, effect=None), _envelope(permissions=("read",), effects=()), subject)
    for mutate in (
        lambda projection: projection.update(schema_version=99),
        lambda projection: projection.update(extra="unknown"),
        lambda projection: projection.pop("integrity"),
    ):
        checkpoint = state.to_checkpoint_dict()
        projection = checkpoint["w14_intent_continuation"]
        assert isinstance(projection, dict)
        mutate(projection)
        with pytest.raises(ValueError, match="continuation"):
            AgentState().from_checkpoint_dict(checkpoint)


def test_c6_a03_resume_with_narrower_write_scope_is_denied_before_mutation(tmp_path: Path) -> None:
    source = tmp_path / "src" / "settings.py"
    source.parent.mkdir()
    source.write_text("TIMEOUT = 1\n", encoding="utf-8")
    envelope, admitted, grounded = _grounded_w14_metadata(tmp_path)
    narrowed = _envelope(tmp_path, write=("src/other",), identity=envelope.authority_identity)
    context = _context(
        {"read", "write", "validate"},
        metadata={
            "w14_semantic_task": True,
            "authority_envelope": narrowed,
            "admitted_intent": admitted,
            "grounded_target_set": grounded,
            "invocation_required_capabilities": ("read", "write"),
            "invocation_durable_effects": ("write",),
        },
    )
    service = SimpleNamespace(root=tmp_path, context=context)
    change_set = ChangeSet("change", (FileChange("src/settings.py", ChangeKind.MODIFY, content="TIMEOUT = 2\n"),))

    with pytest.raises(MutationBindingError, match="MUTATION_GROUNDING_STALE"):
        assert_changeset_admitted(service, change_set, revalidate=True)
    assert source.read_text(encoding="utf-8") == "TIMEOUT = 1\n"


def test_c6_a04_resume_stale_source_fingerprint_blocks(tmp_path: Path) -> None:
    source = tmp_path / "src" / "settings.py"
    source.parent.mkdir()
    source.write_text("TIMEOUT = 1\n", encoding="utf-8")
    envelope, admitted, grounded = _grounded_w14_metadata(tmp_path)
    source.write_text("TIMEOUT = 2\n", encoding="utf-8")

    with pytest.raises(GroundingError, match="GROUNDING_STALE"):
        revalidate_grounded_targets(
            grounded,
            tmp_path,
            envelope=envelope,
            admitted_intent=admitted,
            required_capabilities=("read", "write"),
            required_effects=("write",),
        )


def test_c6_a05_resume_moved_or_ambiguous_definition_blocks(tmp_path: Path) -> None:
    source = tmp_path / "src" / "settings.py"
    source.parent.mkdir()
    source.write_text("TIMEOUT = 1\n", encoding="utf-8")
    envelope, admitted, grounded = _grounded_w14_metadata(tmp_path)
    source.write_text("OTHER = 1\n", encoding="utf-8")
    with pytest.raises(GroundingError, match="GROUNDING_STALE"):
        revalidate_grounded_targets(grounded, tmp_path, envelope=envelope, admitted_intent=admitted)

    source.write_text("TIMEOUT = 1\n", encoding="utf-8")
    (tmp_path / "src" / "other.py").write_text("TIMEOUT = 2\n", encoding="utf-8")
    with pytest.raises(GroundingError, match="GROUNDING_AMBIGUOUS"):
        ground_intent_claim(
            _claim(),
            envelope,
            current_subject="change TIMEOUT",
            workspace_root=tmp_path,
        )


def test_c6_a06_resume_symlink_escape_is_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "src" / "settings.py"
    source.parent.mkdir()
    source.write_text("TIMEOUT = 1\n", encoding="utf-8")
    envelope, admitted, grounded = _grounded_w14_metadata(tmp_path)
    import agent.planning.target_grounding_revalidation as revalidation

    def escaped(*_args, **_kwargs):
        from agent.runtime.path_safety import WorkspacePathError

        raise WorkspacePathError("link escape")

    monkeypatch.setattr(revalidation, "assert_path_safe", escaped)
    with pytest.raises(GroundingError, match="GROUNDING_PATH_ESCAPED"):
        revalidate_grounded_targets(grounded, tmp_path, envelope=envelope, admitted_intent=admitted)


def test_c6_a07_proposal_only_survives_checkpoint_and_cannot_commit() -> None:
    subject = "change TIMEOUT"
    claim = _claim(
        subject,
        constraints=(ConstraintClaim("proposal_only", ("e1",)),),
    )
    state = _checkpoint_state(claim, _envelope(), subject)
    restored = AgentState()
    restored.from_checkpoint_dict(state.to_checkpoint_dict())
    assert restored.w14_intent_continuation.proposal_only is True


def test_c6_a08_required_validation_survives_checkpoint_and_blocks_without_validation() -> None:
    subject = "change TIMEOUT"
    claim = _claim(
        subject,
        constraints=(ConstraintClaim("require_validation", ("e1",)),),
    )
    state = _checkpoint_state(claim, _envelope(), subject)
    restored = AgentState()
    restored.from_checkpoint_dict(state.to_checkpoint_dict())
    assert restored.w14_intent_continuation.requires_validation is True

    owner = SimpleNamespace(
        agent_state=SimpleNamespace(
            tool_history=[
                {
                    "result": {
                        "ok": True,
                        "status": "succeeded",
                        "data": {
                            "artifacts": [
                                {
                                    "metadata": {
                                        "affected_files": ("src/settings.py",),
                                        "mutation_occurred": True,
                                        "applied": True,
                                        "final_state": "applied",
                                    }
                                }
                            ]
                        },
                    }
                }
            ],
            last_result=None,
            terminal_disposition=None,
            pending_effects=lambda: (),
            pending_obligations=lambda: (),
            blocked_obligations=lambda: (),
            prohibited_effects_occurred=lambda: (),
            unrequested_effects=lambda: (),
            terminal_evidence_complete=lambda: True,
        ),
        _admitted_intent=SimpleNamespace(
            requires_validation=True,
            admitted_effects=("write",),
            proposal_only=False,
        ),
        _task_failed=False,
        _cancelled=False,
    )
    assert review_task_completion(owner).accepted is False


def test_c6_a09_missing_action_uses_trusted_default_requirement() -> None:
    graph = TaskGraph("analyze", (TaskNode("n", "analyze", capabilities=frozenset({"read"}), metadata={}),))
    context = _context({"read"}, metadata={"w14_semantic_task": True})
    with pytest.raises(GraphAuthorityError, match="analyze"):
        TaskGraphScheduler(SimpleNamespace(execute=lambda *_args: TaskResult(TaskStatus.SUCCEEDED))).execute(
            graph, context
        )


def test_c6_a10_unknown_action_semantics_fail_closed() -> None:
    graph = TaskGraph("unknown", (TaskNode("n", "unknown", metadata={"action": "unknown"}),))
    context = _context({"read"}, metadata={"w14_semantic_task": True})
    with pytest.raises(GraphAuthorityError, match="GRAPH_UNKNOWN_ACTION"):
        preflight_graph_capabilities(graph, context.permissions, strict_w14=True)


def test_c6_a11_underdeclared_node_capabilities_cannot_hide_requirement() -> None:
    graph = TaskGraph(
        "modify",
        (TaskNode("n", "modify", capabilities=frozenset({"read"}), metadata={"action": "modify"}),),
    )
    with pytest.raises(GraphAuthorityError, match="write"):
        preflight_graph_capabilities(graph, {"read", "analyze"}, strict_w14=True)


def test_c6_a12_graph_denial_precedes_early_mutating_node() -> None:
    called: list[str] = []

    class Executor:
        def execute(self, node, context):
            called.append(node.node_id)
            return TaskResult(TaskStatus.SUCCEEDED)

    graph = TaskGraph(
        "graph",
        (
            TaskNode("mutate", "mutate", metadata={"action": "modify"}),
            TaskNode("later", "later", metadata={}),
        ),
    )
    context = _context({"read", "write", "validate"}, metadata={"w14_semantic_task": True})
    with pytest.raises(GraphAuthorityError, match="analyze"):
        TaskGraphScheduler(Executor()).execute(graph, context)
    assert called == []


def test_c6_a13_false_or_unresolved_conditional_cannot_mutate() -> None:
    subject = "change TIMEOUT if ready"
    start = subject.index("if")
    constraint = ConstraintClaim(
        "conditional",
        ("e2",),
        value="p",
    )
    claim = IntentClaimV1(
        "do",
        "none",
        effects=(EffectClaim("write", "requested", ("s1",), ("e1",)),),
        selectors=(TargetSelectorClaim("s1", "symbol", "TIMEOUT", "mutation_target", ("e1",)),),
        constraints=(constraint,),
        evidence_spans=(
            EvidenceSpan("e1", 7, 14, "TIMEOUT"),
            EvidenceSpan("e2", start, start + 2, "if"),
        ),
    )
    with pytest.raises(IntentAdmissionError, match="INTENT_CONSTRAINT_UNSUPPORTED"):
        admit_intent_claim(claim, _envelope(), current_subject=subject, trusted_predicates={"p": False})


def test_c6_a14_unsupported_conditional_and_prohibit_constraints_do_not_evaporate() -> None:
    subject = "change TIMEOUT"
    for kind in ("conditional", "prohibit_effect", "preserve"):
        constraint = ConstraintClaim(kind, ("e1",), value="p" if kind == "conditional" else None)
        with pytest.raises(IntentAdmissionError, match="INTENT_CONSTRAINT_UNSUPPORTED"):
            admit_intent_claim(
                _claim(subject, constraints=(constraint,)),
                _envelope(),
                current_subject=subject,
            )


def test_c6_a15_narrow_read_scope_never_enumerates_out_of_scope_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "src"
    allowed.mkdir()
    (allowed / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.py").write_text("TIMEOUT = 2\n", encoding="utf-8")
    import agent.planning.target_grounding_discovery as discovery

    seen: list[str] = []
    original_scandir = discovery.os.scandir

    def recording_scandir(path, *args, **kwargs):
        seen.append(str(path))
        return original_scandir(path, *args, **kwargs)

    monkeypatch.setattr(discovery.os, "scandir", recording_scandir)
    claim = _claim()
    ground_intent_claim(
        claim,
        _envelope(tmp_path, read=("src",), write=("src",)),
        current_subject="change TIMEOUT",
        workspace_root=tmp_path,
    )
    assert seen == [str(allowed)]
    assert str(outside) not in seen


def test_c6_a16_venv_pollution_does_not_become_application_grounding(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "polluted.py").write_text("TIMEOUT = 2\n", encoding="utf-8")
    _, grounded = ground_intent_claim(
        _claim(),
        _envelope(tmp_path, read=("*",), write=("src",)),
        current_subject="change TIMEOUT",
        workspace_root=tmp_path,
    )
    assert grounded.resources == ("src/settings.py",)


def test_c6_a17_source_inventory_over_old_512_threshold_remains_bounded_and_usable(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    for index in range(513):
        (source_dir / f"unrelated_{index:04d}.py").write_text(f"VALUE_{index} = {index}\n", encoding="utf-8")
    (source_dir / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    _, grounded = ground_intent_claim(
        _claim(),
        _envelope(tmp_path),
        current_subject="change TIMEOUT",
        workspace_root=tmp_path,
    )
    assert grounded.resources == ("src/settings.py",)
    with pytest.raises(GroundingError, match="GROUNDING_DISCOVERY_LIMIT"):
        ground_intent_claim(
            _claim(),
            _envelope(tmp_path),
            current_subject="change TIMEOUT",
            workspace_root=tmp_path,
            max_files=10,
        )


def test_c6_a18_unrelated_oversized_source_does_not_poison_unique_target(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    (source_dir / "huge.py").write_text("x = 1\n" * 100, encoding="utf-8")
    _, grounded = ground_intent_claim(
        _claim(),
        _envelope(tmp_path),
        current_subject="change TIMEOUT",
        workspace_root=tmp_path,
        max_source_bytes=32,
    )
    assert grounded.resources == ("src/settings.py",)


def test_c6_a19_workspace_wildcard_does_not_authorize_logical_memory() -> None:
    envelope = _envelope(
        permissions=("read", "write", "memory"),
        read=("*",),
        write=("*",),
        effects=("memory_write",),
    )
    assert envelope.allows_write("memory") is False
    with pytest.raises(IntentAdmissionError, match="INTENT_MEMORY_WRITE_SCOPE_DENIED"):
        admit_intent_claim(_memory_claim(), envelope, current_subject="remember memory")


def test_c6_a20_memory_only_researcher_gets_trusted_session_memory_scope() -> None:
    orchestrator = SimpleNamespace(
        allowed_capabilities={"memory"},
        read_resources=None,
        write_resources=None,
        workspace_root=None,
        metadata={"memory_resource_eligible": True},
        task_authority=None,
        application_authority=None,
    )
    envelope = authority_envelope_from_orchestrator(orchestrator)
    assert envelope.parent_permissions == {"memory"}
    assert envelope.allows_write("memory") is True
    admitted = admit_intent_claim(
        _memory_claim(), envelope, current_subject="remember memory"
    )
    assert admitted.admitted_effects == ("memory_write",)


def test_c6_a21_memory_revalidation_performs_zero_filesystem_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    envelope = AuthorityEnvelope(
        parent_permissions={"memory"},
        granted_effects={"memory_write"},
        read_resources=(ResourceAccess("memory", ResourceMode.READ, ResourceProvenance.TRUSTED_DERIVED),),
        write_resources=(ResourceAccess("memory", ResourceMode.WRITE, ResourceProvenance.TRUSTED_DERIVED),),
        authority_identity="memory-c6",
    )
    admitted, grounded = ground_intent_claim(
        _memory_claim(),
        envelope,
        current_subject="remember memory",
        workspace_root=tmp_path,
    )
    import agent.planning.target_grounding_revalidation as revalidation

    monkeypatch.setattr(revalidation, "resolve_workspace_path", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("filesystem path resolution")))
    monkeypatch.setattr(Path, "resolve", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("filesystem resolve")))
    monkeypatch.setattr(Path, "stat", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("filesystem stat")))
    monkeypatch.setattr(Path, "read_bytes", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("filesystem read")))
    assert revalidate_grounded_targets(
        grounded,
        tmp_path,
        envelope=envelope,
        admitted_intent=admitted,
        required_capabilities=("memory",),
        required_effects=("memory_write",),
    ) is grounded


def test_c6_a22_minimal_do_without_durable_effect_admits() -> None:
    claim = _claim("do nothing", effect=None)
    admitted = admit_intent_claim(
        claim,
        _envelope(permissions=("read",), read=("*",), write=(), effects=()),
        current_subject="do nothing",
    )
    assert admitted.operation == "do"
    assert admitted.admitted_effects == ()


def test_c6_a23_do_process_requires_process_at_invocation_preflight() -> None:
    descriptor = SimpleNamespace(name="process_tool", capabilities=frozenset({"process"}))
    semantics = resolve_invocation_semantics(descriptor, {"action": "run"})
    assert semantics.required_capabilities == {"process"}
    registry = {"process_tool": descriptor}
    graph = TaskGraph("run", (TaskNode("n", "run", metadata={"tool": "process_tool", "action": "run"}),))
    with pytest.raises(GraphAuthorityError, match="process"):
        preflight_graph_capabilities(graph, {"read"}, registry, strict_w14=True)


def test_c6_a24_do_without_write_effect_rejects_later_filesystem_write() -> None:
    admitted = admit_intent_claim(
        _claim("do nothing", effect=None),
        _envelope(permissions=("read",), read=("*",), write=(), effects=()),
        current_subject="do nothing",
    )
    descriptor = SimpleNamespace(
        name="code_task",
        capabilities=frozenset({"read", "write", "validate", "analyze"}),
    )
    assert "UNREQUESTED_EFFECT" in str(
        effect_intent_error(
            "do nothing",
            "code_task",
            {"action": "modify", "targets": ["src/settings.py"]},
            descriptor,
            admitted_intent=admitted,
        )
    )


def test_c6_a25_vcs_write_action_requires_vcs_write() -> None:
    descriptor = SimpleNamespace(name="vcs_tool", capabilities=frozenset({"vcs_write"}))
    semantics = resolve_invocation_semantics(descriptor, {"action": "commit"})
    assert semantics.required_capabilities == {"vcs_write"}


def test_c6_a26_filesystem_write_action_requires_write() -> None:
    descriptor = SimpleNamespace(name="filesystem_tool", capabilities=frozenset({"write"}))
    semantics = resolve_invocation_semantics(descriptor, {"action": "write"})
    assert semantics.required_capabilities == {"write"}


def test_c6_a27_semantic_write_does_not_choose_exact_invocation_capability() -> None:
    subject = "change TIMEOUT"
    envelope = _envelope(
        permissions=("read", "vcs_write"),
        effects=("write",),
    )
    admitted = admit_intent_claim(_claim(subject), envelope, current_subject=subject)
    vcs = resolve_invocation_semantics(
        SimpleNamespace(name="vcs_tool", capabilities=frozenset({"vcs_write"})),
        {"action": "commit"},
    )
    filesystem = resolve_invocation_semantics(
        SimpleNamespace(name="filesystem_tool", capabilities=frozenset({"write"})),
        {"action": "write"},
    )
    assert admitted.admitted_effects == ("write",)
    assert vcs.required_capabilities == {"vcs_write"}
    assert filesystem.required_capabilities == {"write"}
    assert vcs.required_capabilities != filesystem.required_capabilities


def test_c6_a28_old_mutation_authorized_flag_cannot_override_current_denial(tmp_path: Path) -> None:
    source = tmp_path / "src" / "settings.py"
    source.parent.mkdir()
    source.write_text("TIMEOUT = 1\n", encoding="utf-8")
    envelope, admitted, grounded = _grounded_w14_metadata(tmp_path)
    current = _envelope(tmp_path, write=("src/other",), identity=envelope.authority_identity)
    context = _context(
        {"read", "write", "validate"},
        metadata={
            "w14_semantic_task": True,
            "authority_envelope": current,
            "admitted_intent": admitted,
            "grounded_target_set": grounded,
            "invocation_required_capabilities": ("read", "write"),
            "invocation_durable_effects": ("write",),
        },
    )
    service = SimpleNamespace(root=tmp_path, context=context)
    with pytest.raises(MutationBindingError, match="MUTATION_GROUNDING_STALE"):
        assert_changeset_admitted(
            service,
            ChangeSet("change", (FileChange("src/settings.py", ChangeKind.MODIFY, content="x\n"),)),
            revalidate=True,
        )


def test_c6_a29_w14_marker_with_missing_typed_metadata_fails_closed(tmp_path: Path) -> None:
    context = _context({"read", "write"}, metadata={"w14_semantic_task": True})
    service = SimpleNamespace(root=tmp_path, context=context)
    with pytest.raises(MutationBindingError, match="MUTATION_W14_AUTHORITY_MISSING"):
        assert_changeset_admitted(service, ChangeSet("x", ()), revalidate=True)


def test_c6_a30_fresh_root_and_child_preserve_typed_w14_metadata(tmp_path: Path) -> None:
    envelope, admitted, grounded = _grounded_w14_metadata(tmp_path)
    metadata = {
        "w14_semantic_task": True,
        "authority_envelope": envelope,
        "admitted_intent": admitted,
        "grounded_target_set": grounded,
    }
    root = _context({"read", "write", "validate"}, metadata=metadata)
    child = root.child("node", permissions=frozenset({"read", "write", "validate"}))
    assert child.metadata["w14_semantic_task"] is True
    assert child.metadata["authority_envelope"] is envelope
    assert child.metadata["admitted_intent"] is admitted
    assert child.metadata["grounded_target_set"] is grounded


def test_c6_a31_policy_refresh_reprojects_w14_metadata(tmp_path: Path) -> None:
    envelope, admitted, grounded = _grounded_w14_metadata(tmp_path)
    state = AgentState(budget_ledger=TaskBudgetLedger())
    state.w14_semantic_task = True
    state.w14_intent_continuation = SimpleNamespace()
    owner = SimpleNamespace(
        workspace=SimpleNamespace(),
        workspace_root=tmp_path,
        agent_state=state,
        session=SimpleNamespace(config={}, gateway=_Gateway(), model_profile=None, task_policy=None),
        task_budget=state.budget_ledger,
        cancellation_token=CancellationToken(),
        event_dispatcher=None,
        _run_correlation=None,
        _task_execution_context=_context(
            {"read", "write", "validate"},
            metadata={"old": "value"},
        ),
        run_correlation=None,
        _authority_envelope=envelope,
        _admitted_intent=admitted,
        _grounded_targets=grounded,
    )
    owner._task_execution_context.metadata.update(_authority_metadata(owner))
    refresh_orchestrator_task_policy(owner)
    assert owner._task_execution_context.metadata["w14_semantic_task"] is True
    assert owner._task_execution_context.metadata["authority_envelope"] is envelope
    assert owner._task_execution_context.metadata["admitted_intent"] is admitted
    assert owner._task_execution_context.metadata["grounded_target_set"] is grounded


def test_c6_a32_semantic_parse_audit_has_bounded_success_and_failure_facts() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    service = object.__new__(InteractionService)
    service.application = SimpleNamespace(_emit=lambda kind, data: events.append((kind, data)))
    service._semantic_audit(
        "semantic_intent_parsed",
        {"contract": "semantic-intent-v1", "success": True, "evidence_span_ids": ["e1"]},
    )
    service._semantic_audit(
        "semantic_intent_parsed",
        {"contract": "semantic-intent-v1", "success": False, "failure_reason": "invalid", "evidence_span_ids": []},
    )
    assert [event[1]["success"] for event in events] == [True, False]
    assert all("raw_prompt" not in data for _, data in events)


def test_c6_a33_grounding_audit_records_selector_resource_provenance(tmp_path: Path) -> None:
    source = tmp_path / "src" / "settings.py"
    source.parent.mkdir()
    source.write_text("TIMEOUT = 1\n", encoding="utf-8")
    events: list[tuple[str, dict[str, object]]] = []
    subject = "change TIMEOUT"
    orchestrator = SimpleNamespace(
        allowed_capabilities={"read", "write", "validate"},
        read_resources=None,
        write_resources=None,
        workspace_root=tmp_path,
        task_authority=None,
        application_authority=None,
        agent_state=AgentState(),
        _task_execution_context=None,
        _emit=lambda kind, data: events.append((kind, data)),
    )
    directive = TaskRunDirective(TaskDirective.DO, DeliberationProfile.NORMAL, subject, intent_claim=_claim())
    assert _admit_runtime_intent(orchestrator, directive) is None
    grounding = next(data for kind, data in events if kind == "semantic_grounding")
    selector = grounding["selectors"][0]
    assert selector["resource"] == "src/settings.py"
    assert selector["provenance"]
    assert selector["freshness"]


def test_c6_a34_graph_audit_records_required_and_missing_capabilities() -> None:
    sink = _EventSink()
    graph = TaskGraph("analyze", (TaskNode("n", "analyze", metadata={"action": "analyze"}),))
    context = _context({"read"}, metadata={"w14_semantic_task": True}, sink=sink)
    with pytest.raises(GraphAuthorityError):
        TaskGraphScheduler(SimpleNamespace(execute=lambda *_args: TaskResult(TaskStatus.SUCCEEDED))).execute(graph, context)
    event = next(item for item in sink.events if item.kind.value == "graph_authority_preflight")
    assert event.data["missing_capabilities"] == ("analyze",)


def test_c6_a35_mutation_subset_audit_records_pass_and_denial(tmp_path: Path) -> None:
    source = tmp_path / "src" / "settings.py"
    source.parent.mkdir()
    source.write_text("TIMEOUT = 1\n", encoding="utf-8")
    envelope, admitted, grounded = _grounded_w14_metadata(tmp_path)
    sink = _EventSink()
    context = _context(
        {"read", "write", "validate"},
        metadata={
            "w14_semantic_task": True,
            "authority_envelope": envelope,
            "admitted_intent": admitted,
            "grounded_target_set": grounded,
            "invocation_required_capabilities": ("read", "write"),
            "invocation_durable_effects": ("write",),
        },
        sink=sink,
    )
    service = SimpleNamespace(root=tmp_path, context=context)
    assert_changeset_admitted(service, ChangeSet("x", (FileChange("src/settings.py", ChangeKind.MODIFY, content="x\n"),)))
    with pytest.raises(MutationBindingError):
        assert_changeset_admitted(service, ChangeSet("x", (FileChange("other.py", ChangeKind.MODIFY, content="x\n"),)))
    results = [item.data for item in sink.events if item.kind.value == "mutation_subset_checked"]
    assert [item["subset"] for item in results] == [True, False]


def test_c6_a36_audit_payload_has_no_raw_prompt_or_absolute_path(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    subject = f"private prompt {tmp_path}"
    service = object.__new__(InteractionService)
    service.application = SimpleNamespace(_emit=lambda kind, data: events.append((kind, data)))
    service._semantic_audit(
        "semantic_intent_parsed",
        {"contract": "semantic-intent-v1", "success": True, "evidence_span_ids": ["e1"]},
    )
    payload = json.dumps(events)
    assert subject not in payload
    assert str(tmp_path) not in payload


def test_c6_a37_approval_cannot_widen_admitted_target(tmp_path: Path) -> None:
    target = tmp_path / "src" / "settings.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")
    approvals: list[bool] = []
    service = SimpleNamespace(
        root=tmp_path,
        context=SimpleNamespace(
            metadata={"admitted_intent": SimpleNamespace(mutation_targets=("src/settings.py",), proposal_only=False)}
        ),
        approval_policy=SimpleNamespace(assess=lambda *_args: SimpleNamespace(confidence=1.0, reasons=(), requires_confirmation=False)),
    )
    approver = SimpleNamespace(requires_explicit_approval=False, approve=lambda *_args: approvals.append(True) or True)
    result = run_apply_changes(
        service,
        ChangeSet("x", (FileChange("other.py", ChangeKind.MODIFY, content="bad\n"),)),
        approver=approver,
        transaction_factory=ChangeSetTransaction,
        outcome_verifier_factory=object(),
        prepared_change_evidence_factory=object(),
        validate_model_code_task=object(),
        requires_selective_verification=lambda *_args: False,
    )
    assert result.status is TaskStatus.BLOCKED
    assert approvals == []
    assert not (tmp_path / "other.py").exists()


def test_c6_a38_approval_denial_causes_zero_durable_mutation(tmp_path: Path) -> None:
    from agent.code.policy import ProposalAssessment

    target = tmp_path / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    service = SimpleNamespace(
        root=tmp_path,
        context=SimpleNamespace(
            metadata={"admitted_intent": SimpleNamespace(mutation_targets=("module.py",), proposal_only=False)}
        ),
        approval_policy=SimpleNamespace(
            assess=lambda *_args: ProposalAssessment(
                confidence=0.0,
                reasons=("denied",),
                requires_confirmation=True,
            )
        ),
    )
    approver = SimpleNamespace(requires_explicit_approval=True, approve=lambda *_args: False)
    result = run_apply_changes(
        service,
        ChangeSet("x", (FileChange("module.py", ChangeKind.MODIFY, content="value = 2\n"),)),
        approver=approver,
        transaction_factory=ChangeSetTransaction,
        outcome_verifier_factory=object(),
        prepared_change_evidence_factory=object(),
        validate_model_code_task=object(),
        requires_selective_verification=lambda *_args: False,
    )
    assert result.status is not TaskStatus.SUCCEEDED
    assert target.read_text(encoding="utf-8") == "value = 1\n"
