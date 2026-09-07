"""Wave 14 C8 final-integration adversarial campaign."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.cancellation import CancellationToken
from agent.code.change_models import ChangeKind, ChangeSet, FileChange
from agent.code.change_transaction import ChangeSetTransaction
from agent.code.validation import ValidationStatus
from agent.code.workflow_application_flow_support import run_apply_changes
from agent.interaction.intent_claim import ConstraintClaim
from agent.orchestration import task_execution, task_execution_authority
from agent.orchestration.task_execution import execute_task
from agent.planning.target_grounding import GroundingError, ground_intent_claim
from agent.planning.target_grounding_discovery import discover_symbol_definitions
from agent.runtime.context import TaskExecutionContext, TaskStatus
from tests.unit.runtime.test_wave14_c6 import _claim as make_claim
from tests.unit.runtime.test_wave14_c6 import _envelope
from tests.unit.runtime.test_wave14_c7 import _resume_fixture


class _PlannerReached(AssertionError):
    pass


class _Gateway:
    provider_name = "wave14-c8"
    capabilities = SimpleNamespace()


def _planner_runner(tmp_path: Path, *, constraints: tuple[ConstraintClaim, ...] = ()):
    runner, inputs, _source = _resume_fixture(tmp_path, constraints=constraints)
    orchestrator = runner.orchestrator

    def planner(subject: str):
        assert subject == inputs.objective
        raise _PlannerReached

    orchestrator.plan_builder = SimpleNamespace(build_plan=planner)
    orchestrator._save_checkpoint = lambda: None
    runner._try_hierarchical = lambda *_args, **_kwargs: None
    runner._try_security = lambda *_args, **_kwargs: None
    runner._consume_route_result = lambda *_args, **_kwargs: None
    return runner, inputs


def _w14_service(root: Path) -> SimpleNamespace:
    source = root / "src" / "settings.py"
    source.parent.mkdir()
    source.write_text("TIMEOUT = 1\n", encoding="utf-8")
    envelope = _envelope(root)
    admitted, grounded = ground_intent_claim(
        make_claim(),
        envelope,
        current_subject="change TIMEOUT",
        workspace_root=root,
    )
    context = TaskExecutionContext(
        model_gateway=_Gateway(),
        cancellation=CancellationToken(),
        permissions=frozenset({"read", "write", "validate"}),
        metadata={
            "w14_semantic_task": True,
            "authority_envelope": envelope,
            "admitted_intent": admitted,
            "grounded_target_set": grounded,
        },
    )
    return SimpleNamespace(
        root=root,
        context=context,
        approval_policy=SimpleNamespace(
            assess=lambda *_args: SimpleNamespace(
                confidence=1.0,
                reasons=(),
                requires_confirmation=True,
            )
        ),
        validator=SimpleNamespace(),
        _diagnostic_dict=lambda _item: {},
    )


def _change_set() -> ChangeSet:
    return ChangeSet(
        "change TIMEOUT",
        (FileChange("src/settings.py", ChangeKind.MODIFY, content="TIMEOUT = 2\n"),),
    )


def _apply(service: SimpleNamespace, approver: object):
    return run_apply_changes(
        service,
        _change_set(),
        approver=approver,
        transaction_factory=ChangeSetTransaction,
        outcome_verifier_factory=object(),
        prepared_change_evidence_factory=object(),
        validate_model_code_task=lambda *_args, **_kwargs: SimpleNamespace(
            status=ValidationStatus.PASSED,
            diagnostics=(),
        ),
        requires_selective_verification=lambda *_args: False,
    )


def test_c8_a01_real_execute_task_resume_no_plan_keeps_typed_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, inputs = _planner_runner(tmp_path)
    monkeypatch.setattr(
        task_execution,
        "_admit_runtime_intent",
        lambda *_args: (_ for _ in ()).throw(AssertionError("fresh admission on resume")),
    )
    with pytest.raises(_PlannerReached):
        execute_task(runner, inputs, None)
    owner = runner.orchestrator
    assert owner.agent_state.w14_semantic_task is True
    assert owner._authority_envelope is not None
    assert owner._admitted_intent is not None
    assert owner._grounded_targets is not None


def test_c8_a02_real_resume_preserves_proposal_only_at_planner_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, inputs = _planner_runner(
        tmp_path,
        constraints=(ConstraintClaim("proposal_only", ("e1",)),),
    )

    def planner(_subject: str):
        assert runner.orchestrator._admitted_intent.proposal_only is True
        raise _PlannerReached

    runner.orchestrator.plan_builder.build_plan = planner
    monkeypatch.setattr(task_execution, "_admit_runtime_intent", lambda *_args: pytest.fail("fresh admission"))
    with pytest.raises(_PlannerReached):
        execute_task(runner, inputs, None)


def test_c8_a03_real_resume_preserves_required_validation_at_planner_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, inputs = _planner_runner(
        tmp_path,
        constraints=(ConstraintClaim("require_validation", ("e1",)),),
    )

    def planner(_subject: str):
        assert runner.orchestrator._admitted_intent.requires_validation is True
        raise _PlannerReached

    runner.orchestrator.plan_builder.build_plan = planner
    monkeypatch.setattr(task_execution, "_admit_runtime_intent", lambda *_args: pytest.fail("fresh admission"))
    with pytest.raises(_PlannerReached):
        execute_task(runner, inputs, None)


def test_c8_a04_marked_resume_with_missing_typed_state_fails_before_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, inputs = _planner_runner(tmp_path)
    runner.orchestrator._authority_envelope = None
    runner.orchestrator._admitted_intent = None
    runner.orchestrator._grounded_targets = None
    monkeypatch.setattr(task_execution, "_restore_w14_runtime_intent", lambda *_args: None)
    monkeypatch.setattr(
        task_execution_authority,
        "mark_terminal_blocked",
        lambda _owner, **kwargs: kwargs["reason_code"],
    )
    assert execute_task(runner, inputs, None) == "MUTATION_W14_AUTHORITY_MISSING"


def test_c8_a05_genuine_legacy_resume_keeps_explicit_compatibility_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, inputs = _planner_runner(tmp_path)
    runner.orchestrator.agent_state.w14_semantic_task = False
    runner.orchestrator.agent_state.w14_intent_continuation = None
    observed: list[object] = []

    def planner(_subject: str):
        observed.append(runner.orchestrator._admitted_intent)
        raise _PlannerReached

    runner.orchestrator.plan_builder.build_plan = planner
    monkeypatch.setattr(task_execution, "_admit_runtime_intent", lambda *_args: pytest.fail("legacy admission"))
    with pytest.raises(_PlannerReached):
        execute_task(runner, inputs, None)
    assert observed == [None]


def test_c8_a06_duplicate_created_during_approval_is_rejected_before_commit(
    tmp_path: Path,
) -> None:
    service = _w14_service(tmp_path)

    class DuplicateApprover:
        requires_explicit_approval = True

        def approve(self, _preview: object, _assessment: object) -> bool:
            (tmp_path / "src" / "other.py").write_text("TIMEOUT = 2\n", encoding="utf-8")
            return True

    result = _apply(service, DuplicateApprover())
    assert result.status is TaskStatus.BLOCKED
    assert result.diagnostics[0]["code"] == "MUTATION_GROUNDING_STALE"
    assert (tmp_path / "src" / "settings.py").read_text(encoding="utf-8") == "TIMEOUT = 1\n"


def test_c8_a07_unchanged_approval_passes_post_approval_revalidation(
    tmp_path: Path,
) -> None:
    service = _w14_service(tmp_path)

    class Approve:
        requires_explicit_approval = True

        def approve(self, _preview: object, _assessment: object) -> bool:
            return True

    result = _apply(service, Approve())
    assert result.status is TaskStatus.SUCCEEDED
    assert (tmp_path / "src" / "settings.py").read_text(encoding="utf-8") == "TIMEOUT = 2\n"


def test_c8_a08_selected_file_snapshot_still_blocks_after_approval(
    tmp_path: Path,
) -> None:
    target = tmp_path / "src" / "settings.py"
    target.parent.mkdir()
    target.write_text("TIMEOUT = 1\n", encoding="utf-8")
    service = SimpleNamespace(
        root=tmp_path,
        context=SimpleNamespace(
            metadata={
                "admitted_intent": SimpleNamespace(
                    mutation_targets=("src/settings.py",),
                    proposal_only=False,
                )
            }
        ),
        approval_policy=SimpleNamespace(
            assess=lambda *_args: SimpleNamespace(
                confidence=1.0,
                reasons=(),
                requires_confirmation=True,
            )
        ),
    )

    class MutatingApprover:
        requires_explicit_approval = True

        def approve(self, _preview: object, _assessment: object) -> bool:
            target.write_text("TIMEOUT = 99\n", encoding="utf-8")
            return True

    result = _apply(service, MutatingApprover())
    assert result.status is TaskStatus.FAILED
    assert "stage" in (result.error or "").casefold()
    assert target.read_text(encoding="utf-8") == "TIMEOUT = 99\n"


def test_c8_a09_approval_cannot_replace_current_w14_authority(
    tmp_path: Path,
) -> None:
    service = _w14_service(tmp_path)
    current = service.context.metadata["authority_envelope"]

    class AuthorityChangingApprover:
        requires_explicit_approval = True

        def approve(self, _preview: object, _assessment: object) -> bool:
            service.context.metadata["authority_envelope"] = _envelope(
                tmp_path,
                identity="changed-during-approval",
            )
            return True

    result = _apply(service, AuthorityChangingApprover())
    assert result.status is TaskStatus.BLOCKED
    assert result.diagnostics[0]["code"] == "MUTATION_GROUNDING_STALE"
    assert service.context.metadata["authority_envelope"] is not current
    assert (tmp_path / "src" / "settings.py").read_text(encoding="utf-8") == "TIMEOUT = 1\n"


def test_c8_a10_empty_directory_work_is_bounded(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    for index in range(8):
        (source / f"empty_{index}").mkdir()
    with pytest.raises(GroundingError, match="GROUNDING_DISCOVERY_LIMIT"):
        ground_intent_claim(
            make_claim(),
            _envelope(tmp_path),
            current_subject="change TIMEOUT",
            workspace_root=tmp_path,
            max_enumerated_paths=4,
        )


def test_c8_a11_single_directory_entry_list_is_bounded_during_scandir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    for index in range(20):
        (source / f"empty_{index:02d}").mkdir()
    import agent.planning.target_grounding_discovery as discovery

    original = discovery.os.scandir
    observed = 0

    class RecordingScan:
        def __init__(self, path: Path) -> None:
            self._path = path
            self._iterator = original(path)

        def __enter__(self):
            self._iterator.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._iterator.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal observed
            item = next(self._iterator)
            if self._path == source:
                observed += 1
            return item

    monkeypatch.setattr(discovery.os, "scandir", lambda path: RecordingScan(Path(path)))
    with pytest.raises(GroundingError, match="GROUNDING_DISCOVERY_LIMIT"):
        ground_intent_claim(
            make_claim(),
            _envelope(tmp_path),
            current_subject="change TIMEOUT",
            workspace_root=tmp_path,
            max_enumerated_paths=4,
        )
    # One extra entry is the bounded sentinel that proves the limit was
    # exceeded; the scanner never materializes the remaining directory.
    assert observed <= 5


def test_c8_a12_authorized_directory_scandir_error_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    broken = source / "broken"
    broken.mkdir(parents=True)
    (source / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    import agent.planning.target_grounding_discovery as discovery

    original = discovery.os.scandir

    def fail(path: str | Path):
        if Path(path) == broken:
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(discovery.os, "scandir", fail)
    with pytest.raises(GroundingError, match="GROUNDING_DISCOVERY_FAILED"):
        ground_intent_claim(
            make_claim(),
            _envelope(tmp_path),
            current_subject="change TIMEOUT",
            workspace_root=tmp_path,
        )


def test_c8_a13_excluded_directory_remains_outside_source_inventory(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    polluted = tmp_path / ".venv" / "lib"
    polluted.mkdir(parents=True)
    (polluted / "polluted.py").write_text("TIMEOUT = 2\n", encoding="utf-8")
    _, grounded = ground_intent_claim(
        make_claim(),
        _envelope(tmp_path, read=("*",)),
        current_subject="change TIMEOUT",
        workspace_root=tmp_path,
    )
    assert grounded.resources == ("src/settings.py",)


def test_c8_a14_actual_bytes_not_stat_estimate_control_total_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "settings.py").write_text("VALUE = 1\n", encoding="utf-8")
    raced = source / "raced.py"
    raced.write_text("x = 1\n" * 20, encoding="utf-8")
    original_stat = Path.stat

    def smaller_stat(path: Path, *args: object, **kwargs: object):
        if path == raced:
            actual = original_stat(path, *args, **kwargs)
            return SimpleNamespace(st_size=1, st_mode=actual.st_mode)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", smaller_stat)
    with pytest.raises(GroundingError, match="GROUNDING_DISCOVERY_LIMIT"):
        discover_symbol_definitions(
            tmp_path,
            _envelope(tmp_path),
            "TIMEOUT",
            max_total_source_bytes=32,
        )


def test_c8_a15_oversized_scans_share_one_aggregate_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    first = source / "a.py"
    second = source / "b.py"
    first.write_text("x = 1\n", encoding="utf-8")
    second.write_text("y = 1\n", encoding="utf-8")
    original_stat = Path.stat

    def understated_stat(path: Path, *args: object, **kwargs: object):
        if path in {first, second}:
            actual = original_stat(path, *args, **kwargs)
            return SimpleNamespace(st_size=1, st_mode=actual.st_mode)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", understated_stat)
    with pytest.raises(GroundingError, match="GROUNDING_DISCOVERY_LIMIT"):
        discover_symbol_definitions(
            tmp_path,
            _envelope(tmp_path),
            "TIMEOUT",
            max_source_bytes=4,
            max_total_source_bytes=10,
        )


def test_c8_a16_normal_repository_sized_discovery_remains_usable(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    for index in range(20):
        (source / f"unrelated_{index:02d}.py").write_text(
            f"VALUE_{index} = {index}\n", encoding="utf-8"
        )
    (source / "settings.py").write_text("TIMEOUT = 1\n", encoding="utf-8")
    _, grounded = ground_intent_claim(
        make_claim(),
        _envelope(tmp_path),
        current_subject="change TIMEOUT",
        workspace_root=tmp_path,
    )
    assert grounded.resources == ("src/settings.py",)
