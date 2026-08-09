from pathlib import Path

from scripts.report_pytest_failures import report_failures


def test_report_failures_emits_node_and_short_reason(tmp_path: Path, capsys) -> None:
    report = tmp_path / "results.xml"
    report.write_text(
        '<testsuite><testcase classname="tests.unit.test_demo" name="test_bad">'
        '<failure message="expected % value">trace\nsecond line</failure>'
        "</testcase></testsuite>",
        encoding="utf-8",
    )

    assert report_failures(report) == 0

    output = capsys.readouterr().out
    assert "tests.unit.test_demo::test_bad" in output
    assert "expected %25 value" in output
    assert "second line" not in output


def test_report_failures_handles_missing_report_without_masking_failure(
    tmp_path: Path, capsys
) -> None:
    assert report_failures(tmp_path / "missing.xml") == 0
    assert "JUnit report not found" in capsys.readouterr().out
