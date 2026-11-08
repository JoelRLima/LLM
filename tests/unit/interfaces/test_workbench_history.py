from __future__ import annotations

from types import SimpleNamespace

from llm_agent.agent.observability import TraceStore
from llm_agent.application.context import AppPaths
from llm_agent.application.inspection import InspectionListRequest, InspectionReadContext, list_inspection_runs
from llm_agent.interfaces.workbench.webview import WorkbenchBridge


def _bridge(home, workspace_id: str = "history-workspace") -> WorkbenchBridge:
    app_paths = AppPaths.discover(home, env={})
    session = SimpleNamespace(
        workspace=SimpleNamespace(workspace_id=workspace_id),
        start=SimpleNamespace(app_paths=app_paths),
        snapshot=lambda: {"state": "idle"},
    )
    return WorkbenchBridge(session=session)


def test_workbench_tasks_pages_more_than_fifty_runs(monkeypatch, tmp_path):
    from llm_agent import application

    requests = []
    runs = [{"run_id": f"run-{index:03}", "status": "succeeded"} for index in range(51)]

    def list_runs(_context, request):
        requests.append((request.limit, request.offset))
        return tuple(runs[request.offset:request.offset + request.limit])

    monkeypatch.setattr(application.inspection, "list_inspection_runs", list_runs)
    bridge = _bridge(tmp_path / "home")
    first = bridge.tasks()
    second = bridge.tasks(50)

    assert requests == [(51, 0), (51, 50)]
    assert len(first["runs"]) == 50
    assert first["runs"][0]["run_id"] == "run-000"
    assert first["runs"][-1]["run_id"] == "run-049"
    assert first["next_offset"] == 50
    assert [item["run_id"] for item in second["runs"]] == ["run-050"]
    assert second["next_offset"] is None


def test_workbench_tasks_rejects_invalid_offsets(tmp_path):
    bridge = _bridge(tmp_path / "home")
    for offset in (True, -1, 1_000_001, "50"):
        result = bridge.tasks(offset)  # type: ignore[arg-type]
        assert result["runs"] == []
        assert result["next_offset"] is None
        assert result["history_state"].startswith("invalid_offset:")


def test_inspection_service_offsets_preserve_canonical_order(tmp_path):
    paths = AppPaths.discover(tmp_path / "home", env={}).for_workspace("history-workspace")
    for index in range(3):
        TraceStore(paths, f"ordered-{index}").close()
    context = InspectionReadContext(paths)

    full = list_inspection_runs(context, InspectionListRequest(limit=10))
    first = list_inspection_runs(context, InspectionListRequest(limit=2, offset=0))
    second = list_inspection_runs(context, InspectionListRequest(limit=2, offset=2))

    assert [item["run_id"] for item in first + second] == [item["run_id"] for item in full]
    for offset in (True, -1, 1_000_001):
        try:
            list_inspection_runs(context, InspectionListRequest(offset=offset))
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid offset accepted: {offset!r}")


def test_task_details_use_real_canonical_lookup_for_full_retention(tmp_path):
    home = tmp_path / "home"
    app_paths = AppPaths.discover(home, env={})
    workspace = app_paths.for_workspace("history-workspace")
    for index in range(51):
        TraceStore(workspace, f"retained-{index:03}").close()

    bridge = _bridge(home)
    first_page = bridge.tasks()
    second_page = bridge.tasks(first_page["next_offset"])
    page_ids = [run["run_id"] for run in first_page["runs"] + second_page["runs"]]
    assert len(first_page["runs"]) == 50
    assert len(second_page["runs"]) == 1
    assert first_page["next_offset"] == 50
    assert second_page["next_offset"] is None
    assert len(set(page_ids)) == 51
    assert set(page_ids) == {f"retained-{index:03}" for index in range(51)}

    detail = bridge.task_details("retained-000")
    stale = _bridge(home).task_details("no-longer-retained")
    traversal = _bridge(home).task_details("../../outside-workspace")
    other_workspace = _bridge(home, "other-workspace").task_details("retained-000")

    assert detail["state"] == "available"
    assert detail["detail"]["run"]["run_id"] == "retained-000"
    assert stale == {"state": "unavailable", "reason": "RUN_NOT_RETAINED"}
    assert traversal == {"state": "unavailable", "reason": "RUN_NOT_RETAINED"}
    assert other_workspace == {"state": "unavailable", "reason": "RUN_NOT_RETAINED"}


def test_task_details_report_missing_inspection_context():
    bridge = WorkbenchBridge(session=SimpleNamespace(snapshot=lambda: {}))
    assert bridge.task_details("run-1") == {
        "state": "not_implemented", "reason": "INSPECTION_CONTEXT_UNAVAILABLE"
    }
