from agent.reporting.task_report import TaskReportBuilder
from agent.runtime.paths import REPORTS_DIR


def test_task_report_default_stays_under_the_canonical_runtime_directory() -> None:
    assert TaskReportBuilder({}).output_dir == REPORTS_DIR


def test_task_report_projects_invocation_and_output_bounds() -> None:
    report = TaskReportBuilder({}).build_report(
        type(
            "State",
            (),
            {
                "objective": "read",
                "tool_history": [
                    {
                        "tool": "file_reader",
                        "args": {"file_path": "notes.txt"},
                        "invocation_id": "inv-1",
                        "result": {
                            "invocation_id": "inv-1",
                            "ok": True,
                            "status": "succeeded",
                            "data": "hello",
                            "metadata": {"total_chars": 5, "truncated": False},
                        },
                    }
                ],
                "events": [],
                "last_result": {"ok": True},
            },
        )(),
        [],
        "hello",
    )

    step = report["steps"][0]
    assert step["invocation_id"] == "inv-1"
    assert step["result"]["status"] == "succeeded"
    assert step["result"]["output_chars"] == 5
    assert step["result"]["truncated"] is False
