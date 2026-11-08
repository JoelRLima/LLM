from __future__ import annotations

from pathlib import Path

import pytest

from llm_agent.agent.evaluation.weak_model_data_oracles import grade_data_case


def _workspace(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def test_json_oracle_preserves_all_existing_values_while_enabling(tmp_path: Path) -> None:
    original = '{"enabled":false,"retries":2,"label":"keep"}\n'
    changed = '{ "label": "keep", "enabled": true, "retries": 2 }\n'
    workspace = _workspace(tmp_path, {"settings.json": changed})
    assert grade_data_case("WM-JSON", workspace, {"settings.json": original}, ("settings.json",)) == ()


@pytest.mark.parametrize("actual", [
    '{"enabled":1,"retries":1}',
    '{"enabled":true,"retries":true}',
    '{"enabled":false,"enabled":true,"retries":1}',
    '{"enabled":true,"retries":NaN}',
])
def test_json_oracle_rejects_type_confusion_and_ambiguous_json(tmp_path, actual):
    original = '{"enabled":false,"retries":1}'
    workspace = _workspace(tmp_path, {"settings.json": actual})
    assert grade_data_case("WM-JSON", workspace, {"settings.json": original}, ("settings.json",))


def test_report_oracle_rejects_swapped_labels_even_when_all_numbers_are_present(tmp_path):
    source = '{"passed":3,"failed":1,"suite":"unit"}'
    workspace = _workspace(tmp_path, {"metrics.json": source, "summary.md": "passed: 1\nfailed: 3\ntotal: 4\nrate: 0.75\nsuite: unit\n"})
    assert grade_data_case("WM-REPORT", workspace, {"metrics.json": source}, ("summary.md",))


@pytest.mark.parametrize("rate", ["unknown", "NaN", "Infinity", "0.9"])
def test_report_oracle_returns_failure_for_invalid_rate_without_raising(tmp_path, rate):
    source = '{"passed":3,"failed":1,"suite":"unit"}'
    report = f"passed: 3\nfailed: 1\ntotal: 4\nrate: {rate}\nsuite: unit\n"
    workspace = _workspace(tmp_path, {"metrics.json": source, "summary.md": report})
    assert grade_data_case("WM-REPORT", workspace, {"metrics.json": source}, ("summary.md",))


def test_json_oracle_rejects_lost_fields_extra_keys_and_extra_file_changes(tmp_path: Path) -> None:
    original = '{"enabled":false,"retries":2}\n'
    workspace = _workspace(tmp_path, {"settings.json": '{"enabled":true}\n'})
    failures = grade_data_case("WM-JSON", workspace, {"settings.json": original}, ("settings.json", "other.txt"))
    codes = {failure.code for failure in failures}
    assert "weak_data_output_invalid" in codes
    assert "weak_data_extra_changes" in codes


def test_csv_oracle_checks_threshold_order_and_original_cells(tmp_path: Path) -> None:
    original = "name,score\nAda,90\nLin,62\n"
    valid = "name,score,passed\nAda,90,true\nLin,62,false\n"
    workspace = _workspace(tmp_path, {"scores.csv": valid})
    assert grade_data_case("WM-CSV", workspace, {"scores.csv": original}, ("scores.csv",)) == ()

    for invalid in (
        "name,score,passed\nAda,90,true\nLin,62,true\n",
        "name,score,passed\nLin,62,false\nAda,90,true\n",
        "name,score,passed\nAda,90,true\nLin,99,false\n",
    ):
        bad_workspace = tmp_path / f"case-{len(list(tmp_path.iterdir()))}"
        _workspace(bad_workspace, {"scores.csv": invalid})
        failures = grade_data_case("WM-CSV", bad_workspace, {"scores.csv": original}, ("scores.csv",))
        assert "weak_data_output_invalid" in {failure.code for failure in failures}


def test_report_oracle_derives_facts_from_source_and_requires_source_unchanged(tmp_path: Path) -> None:
    source = '{"passed":3,"failed":1,"suite":"unit"}\n'
    good = "# Summary\nPassed: 3\nFailed: 1\nTotal: 4\nRate: 0.75\nSuite: unit\n"
    workspace = _workspace(tmp_path, {"metrics.json": source, "summary.md": good})
    assert grade_data_case("WM-REPORT", workspace, {"metrics.json": source}, ("summary.md",)) == ()

    wrong = _workspace(tmp_path / "wrong", {"metrics.json": source, "summary.md": "Passed 9, failed 0"})
    failures = grade_data_case("WM-REPORT", wrong, {"metrics.json": source}, ("summary.md",))
    assert "weak_data_output_invalid" in {failure.code for failure in failures}

    mutated = _workspace(tmp_path / "mutated", {
        "metrics.json": '{"passed":99,"failed":0,"suite":"unit"}\n',
        "summary.md": "Passed 99 failed 0 total 99 rate 1 unit",
    })
    failures = grade_data_case("WM-REPORT", mutated, {"metrics.json": source}, ("summary.md", "metrics.json"))
    assert "weak_data_source_changed" in {failure.code for failure in failures}
    assert "weak_data_extra_changes" in {failure.code for failure in failures}
