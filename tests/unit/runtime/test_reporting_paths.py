from agent.reporting.task_report import TaskReportBuilder
from agent.runtime.paths import REPORTS_DIR


def test_task_report_default_stays_under_the_canonical_runtime_directory() -> None:
    assert TaskReportBuilder({}).output_dir == REPORTS_DIR
