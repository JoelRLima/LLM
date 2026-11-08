"""Durability and independent verification of user-authored task batches."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from llm_agent.application.test_queue import TestQueue
from llm_agent.application.test_queue_manifest import local_path, parse_case
from llm_agent.application.test_queue_verification import verify
from llm_agent.interfaces.test_queue import main


def make_queue(tmp_path: Path, *, checks: bool = True) -> TestQueue:
    tasks = tmp_path / "tasks"
    for name in ("01-first", "02-second", "03-third"):
        case = tasks / name
        case.mkdir(parents=True)
        (case / "task.md").write_text("Create result.txt containing done", encoding="utf-8")
        if checks:
            (case / "checks.json").write_text(json.dumps({"checks": [
                {"kind": "text_equals", "path": "result.txt", "expected": "done"}]}), encoding="utf-8")
    queue = TestQueue(tmp_path / "queue")
    queue.create(tasks)
    return queue


def successful(case: Any, workspace: Path, cancel: Any, seconds: int) -> dict[str, Any]:
    (workspace / "result.txt").write_text("done", encoding="utf-8")
    return {"status": "completed", "success": True, "answer": "done"}


def test_runs_all_and_resumes_without_repeating(tmp_path: Path) -> None:
    queue = make_queue(tmp_path)
    report = queue.run(successful, identity="runtime-A")
    assert report["status"] == "completed"
    assert all(row["status"] == "verified" for row in report["results"].values())
    assert all(row["verification"]["checks"][0]["actual_sha256"] for row in report["results"].values())
    assert "checks" not in queue.snapshot()["results"]["01-first"]["verification"]
    def never(*args: Any) -> Any:
        pytest.fail("completed tests must not be repeated")
    resumed = TestQueue(queue.root).run(never, identity="runtime-A")
    assert resumed["status"] == "completed"
    assert resumed["results"]["01-first"]["verification"]["checks"][0]["actual_sha256"]


def test_false_completion_and_missing_oracle_are_not_success(tmp_path: Path) -> None:
    queue = make_queue(tmp_path)
    report = queue.run(lambda *args: {"status": "completed", "success": True, "answer": "I did it"}, identity="A")
    assert all(row["status"] == "failed" for row in report["results"].values())
    other = make_queue(tmp_path / "other", checks=False)
    report = other.run(successful, identity="A")
    assert all(row["status"] == "unverified" for row in report["results"].values())


def test_checks_do_not_override_failed_runtime(tmp_path: Path) -> None:
    queue = make_queue(tmp_path)
    def execute(*args: Any) -> Any:
        return {**successful(*args), "success": False}
    report = queue.run(execute, identity="A")
    assert all(row["status"] == "failed" and row["verification"]["status"] == "passed" for row in report["results"].values())


def test_pause_survives_new_queue_object_and_can_prioritize(tmp_path: Path) -> None:
    queue = make_queue(tmp_path)
    def execute(*args: Any) -> Any:
        queue.control("pause")
        return successful(*args)
    report = queue.run(execute, identity="A")
    assert report["status"] == "paused"
    assert [row["status"] for row in report["results"].values()] == ["verified", "pending", "pending"]
    resumed = TestQueue(queue.root)
    resumed.prioritize("03-third")
    order = []
    def collect(case: Any, *args: Any) -> Any:
        order.append(case.case_id)
        return successful(case, *args)
    assert resumed.run(collect, identity="A")["status"] == "completed"
    assert order == ["03-third", "02-second"]


def test_stop_cannot_be_undone_by_pause_and_requires_review(tmp_path: Path) -> None:
    queue = make_queue(tmp_path)
    def execute(case: Any, workspace: Path, cancel: Any, seconds: int) -> Any:
        queue.control("stop")
        queue.control("pause")
        assert cancel.is_set()
        return {"status": "interrupted", "success": False}
    report = queue.run(execute, identity="A")
    assert report["status"] == "stopped"
    with pytest.raises(ValueError, match="requires review"):
        queue.run(successful, identity="A")
    report = queue.run(successful, identity="A", skip_interrupted=True)
    assert report["results"]["01-first"]["status"] == "interrupted"
    assert report["results"]["02-second"]["status"] == "verified"


def test_crash_claim_is_not_replayed(tmp_path: Path) -> None:
    queue = make_queue(tmp_path)
    state = queue.snapshot()
    state["results"]["01-first"]["status"] = "running"
    state["status"] = "running"
    queue.save(state)
    assert queue.status()["status"] == "interrupted"
    assert queue.status()["runner_active"] is False
    with pytest.raises(ValueError, match="requires review"):
        queue.run(successful, identity="A")
    assert queue.snapshot()["results"]["01-first"]["status"] == "interrupted"


def test_checkpoint_exists_before_effect_and_lock_excludes_second_runner(tmp_path: Path) -> None:
    queue = make_queue(tmp_path)
    def execute(case: Any, *args: Any) -> Any:
        assert TestQueue(queue.root).snapshot()["results"][case.case_id]["status"] == "running"
        assert TestQueue(queue.root).status()["runner_active"] is True
        with pytest.raises(RuntimeError, match="already owns"):
            TestQueue(queue.root).run(successful, identity="A")
        with pytest.raises(RuntimeError, match="already owns"):
            queue.prioritize("02-second")
        return successful(case, *args)
    assert queue.run(execute, identity="A")["status"] == "completed"


def test_changed_manifest_or_runtime_refuses_resume(tmp_path: Path) -> None:
    queue = make_queue(tmp_path)
    queue.run(successful, identity="A")
    with pytest.raises(ValueError, match="changed"):
        queue.run(successful, identity="B")
    manifest = queue.root / "manifest.json"
    value = json.loads(manifest.read_text())
    value["cases"][0]["prompt"] = "changed"
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="manifest"):
        queue.run(successful, identity="A")


def test_runtime_cannot_change_frozen_checks(tmp_path: Path) -> None:
    queue = make_queue(tmp_path)
    def execute(case: Any, workspace: Path, *args: Any) -> Any:
        (workspace / "checks.json").write_text('{"checks": []}')
        return {"success": True}
    report = queue.run(execute, identity="A")
    assert all(row["status"] == "failed" for row in report["results"].values())


@pytest.mark.parametrize("value", ["../outside", "C:/outside", "/outside", "a/../../outside", ""])
def test_rejects_escaping_paths(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError):
        local_path(tmp_path, value)


@pytest.mark.parametrize("check", [{}, {"kind": "command", "path": "x"}, {"kind": "exists", "path": "x", "expected": True}])
def test_rejects_ambiguous_check_contract(check: Any) -> None:
    with pytest.raises(ValueError):
        parse_case({"id": "one", "prompt": "task", "files": {}, "checks": [check]})


def test_json_check_distinguishes_boolean_from_integer(tmp_path: Path) -> None:
    (tmp_path / "data.json").write_text('{"value": true}')
    assert verify(tmp_path, ({"kind": "json_equals", "path": "data.json", "expected": {"value": 1}},))["status"] == "failed"


def test_no_live_calls_without_explicit_run_flag(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["run", str(tmp_path), "--config", "missing.json"])
    assert exc.value.code == 2


def test_keyboard_interrupt_is_durably_recorded(tmp_path: Path) -> None:
    queue = make_queue(tmp_path)
    def execute(*args: Any) -> Any:
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        queue.run(execute, identity="A")
    assert queue.snapshot()["status"] == "stopped"
    assert queue.snapshot()["results"]["01-first"]["status"] == "interrupted"


def test_author_script_is_frozen_and_executed_outside_workspace(tmp_path: Path) -> None:
    make_queue(tmp_path)
    # Reimport with an external behavior checker, before the agent ever runs.
    task = tmp_path / "tasks" / "01-first"
    (task / "verify.py").write_text("import pathlib,sys\nassert (pathlib.Path(sys.argv[1])/'result.txt').read_text() == 'done'\nprint('checked')")
    other = TestQueue(tmp_path / "with-script")
    other.create(tmp_path / "tasks")
    def execute(*args: Any) -> Any:
        # Editing the author's file now cannot alter the imported verifier.
        (task / "verify.py").write_text("raise SystemExit(99)")
        return successful(*args)
    state = other.run(execute, identity="A")
    row = state["results"]["01-first"]
    assert row["status"] == "verified"
    assert row["verification"]["checks"][1]["stdout"].strip() == "checked"
    assert not (Path(row["workspace"]) / "verify.py").exists()


def test_python_verifier_has_bounded_time_and_cannot_pass_with_output_only(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    failed = verify(workspace, ({"kind": "python", "path": "verify.py", "expected": "print('passed'); raise SystemExit(1)"},))
    assert failed["status"] == "failed"
    timed = verify(workspace, ({"kind": "python", "path": "verify.py", "expected": "import time; time.sleep(30)"},), timeout_seconds=0.3)
    assert timed["status"] == "failed"
    assert timed["checks"][0]["timed_out"] is True


def test_budget_stops_before_pending_case(tmp_path: Path, monkeypatch: Any) -> None:
    import llm_agent.application.test_queue_scheduler as module
    queue = make_queue(tmp_path)
    ticks = iter([0, 0, 2, 2])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks, 2))
    state = queue.run(successful, identity="A", total_seconds=1)
    assert state["status"] == "budget_exhausted"
    assert state["results"]["02-second"]["status"] == "pending"


def test_infrastructure_error_pauses_without_burning_remaining_queue(tmp_path: Path) -> None:
    queue = make_queue(tmp_path)
    state = queue.run(lambda *args: {"status": "error", "success": False}, identity="A")
    assert state["status"] == "paused"
    assert state["results"]["01-first"]["status"] == "error"
    assert state["results"]["02-second"]["status"] == "pending"


def test_large_evidence_is_retained_outside_compact_progress(tmp_path: Path) -> None:
    queue = make_queue(tmp_path)
    def execute(*args: Any) -> Any:
        return {**successful(*args), "answer": "evidence" * 10000}
    state = queue.run(execute, identity="A")
    row = state["results"]["01-first"]
    assert row["status"] == "verified"
    assert len(json.dumps(row)) < 1000
    full = json.loads(Path(row["evidence_path"]).read_text())
    assert full["execution"]["answer"] == "evidence" * 10000
    assert TestQueue(queue.root).snapshot()["status"] == "completed"


def test_json_with_duplicate_fields_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "data.json").write_text('{"value": 1, "value": 2}')
    assert verify(tmp_path, ({"kind": "json_equals", "path": "data.json", "expected": {"value": 2}},))["status"] == "failed"


def test_scheduler_accepts_an_independent_verification_adapter(tmp_path: Path) -> None:
    queue = make_queue(tmp_path)
    seen = []
    def external(workspace: Path, checks: Any, **kwargs: Any) -> Any:
        seen.append(workspace)
        return {"status": "failed", "checks": [{"passed": False, "source": "external"}]}
    state = queue.run(successful, identity="A", verifier=external)
    assert len(seen) == 3
    assert all(row["status"] == "failed" for row in state["results"].values())
