"""Independent, bounded semantic checks for weak-model data tasks."""
from __future__ import annotations

import csv
import io
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Sequence, cast

from llm_agent.agent.evaluation.contracts import EvaluationFailure
from llm_agent.filesystem.path_safety import WorkspacePathError, assert_owned_path

_MAX_FILE_BYTES = 1_000_000


def grade_data_case(
    case_id: str,
    workspace: str | Path,
    initial_files: Mapping[str, str],
    changed_files: Sequence[str],
) -> tuple[EvaluationFailure, ...]:
    """Grade output data against the actual input fixture, without executing it."""
    if case_id not in {"WM-JSON", "WM-CSV", "WM-REPORT"}:
        return (EvaluationFailure("weak_data_case_unknown", "Unknown weak-model data case."),)
    root = Path(workspace)
    failures: list[EvaluationFailure] = []
    expected_outputs = {
        "WM-JSON": "settings.json",
        "WM-CSV": "scores.csv",
        "WM-REPORT": "summary.md",
    }
    output = expected_outputs[case_id]
    changed = set(changed_files)
    if changed - {output}:
        failures.append(EvaluationFailure("weak_data_extra_changes", "Unexpected files changed."))
    if any(path != output for path in initial_files) and any(
        path in changed for path in initial_files if path != output
    ):
        failures.append(EvaluationFailure("weak_data_source_changed", "An input source file changed."))
    try:
        if output not in changed:
            raise ValueError("expected output was not reported as changed")
        source_name = "metrics.json" if case_id == "WM-REPORT" else output
        source = _read_workspace_file(root, source_name)
        if source_name not in initial_files:
            raise ValueError("source file is absent from initial fixture")
        if case_id == "WM-REPORT":
            # The generated report is separate; source integrity is checked against
            # initial fixture bytes and all facts are derived from those bytes.
            if _read_workspace_file(root, source_name) != initial_files[source_name]:
                failures.append(EvaluationFailure("weak_data_source_changed", "The report source changed."))
            _grade_report(root, source)
        else:
            if case_id == "WM-JSON":
                _grade_json(source, initial_files[output])
            else:
                _grade_csv(source, initial_files[output])
    except (OSError, ValueError, UnicodeError, csv.Error, json.JSONDecodeError, WorkspacePathError, InvalidOperation) as exc:
        failures.append(EvaluationFailure("weak_data_output_invalid", f"Output failed semantic validation ({type(exc).__name__})."))
    return tuple(failures)


def _read_workspace_file(root: Path, relative: str) -> str:
    path = assert_owned_path(root, relative)
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise ValueError("file exceeds size bound")
    return cast(str, path.read_text(encoding="utf-8"))


def _json_object(raw: str) -> dict[str, object]:
    value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    if not isinstance(value, dict) or not all(type(key) is str for key in value):
        raise ValueError("expected JSON object")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant: {value}")


def _grade_json(actual_text: str, initial_text: str) -> None:
    actual = _json_object(actual_text)
    initial = _json_object(initial_text)
    if type(initial.get("enabled")) is not bool:
        raise ValueError("input enabled must be boolean")
    expected = dict(initial)
    expected["enabled"] = True
    if actual.get("enabled") is not True or not _same_json(actual, expected):
        raise ValueError("JSON transform changed or lost fields")


def _same_json(actual: object, expected: object) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual is expected
    if isinstance(expected, dict) and isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(_same_json(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list) and isinstance(actual, list):
        return len(actual) == len(expected) and all(_same_json(left, right) for left, right in zip(actual, expected, strict=True))
    return bool(actual == expected)


def _csv_rows(raw: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(raw, newline=""), strict=True)
    fields = reader.fieldnames
    if not fields or len(fields) != len(set(fields)) or any(not name for name in fields):
        raise ValueError("invalid CSV header")
    rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("invalid CSV row shape")
    return list(fields), cast("list[dict[str, str]]", rows)


def _grade_csv(actual_text: str, initial_text: str) -> None:
    source_fields, source_rows = _csv_rows(initial_text)
    fields, rows = _csv_rows(actual_text)
    if fields != [*source_fields, "passed"] or len(rows) != len(source_rows):
        raise ValueError("CSV schema or row count differs")
    for source, actual_row in zip(source_rows, rows, strict=True):
        for key, value in source.items():
            if actual_row.get(key) != value:
                raise ValueError("CSV source value or ordering changed")
        try:
            score = float(source["score"])
        except (KeyError, ValueError) as exc:
            raise ValueError("input score is invalid") from exc
        if actual_row.get("passed") != ("true" if score >= 70 else "false"):
            raise ValueError("CSV pass threshold result is incorrect")


def _grade_report(root: Path, source_text: str) -> None:
    source = _json_object(source_text)
    passed, failed, suite = source.get("passed"), source.get("failed"), source.get("suite")
    if type(passed) is not int or type(failed) is not int or not isinstance(suite, str):
        raise ValueError("report source has invalid types")
    if passed < 0 or failed < 0:
        raise ValueError("report source counts are invalid")
    total = passed + failed
    rate = Decimal(passed) / Decimal(total) if total else Decimal(0)
    report = _read_workspace_file(root, "summary.md")
    fields = _report_fields(report)
    if set(fields) != {"passed", "failed", "total", "rate", "suite"}:
        raise ValueError("summary omits named source facts")
    if (fields["passed"], fields["failed"], fields["total"], fields["suite"]) != (str(passed), str(failed), str(total), suite):
        raise ValueError("summary names incorrect source facts")
    if Decimal(fields["rate"]) != rate:
        raise ValueError("summary has incorrect pass rate")


def _report_fields(report: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in report.splitlines():
        match = re.fullmatch(r"\s*(?:[-*]\s*)?(passed|failed|total|rate|suite)\s*:\s*(.*?)\s*", line, flags=re.IGNORECASE)
        if match is None:
            continue
        key, value = match.group(1).casefold(), match.group(2)
        if key in fields:
            raise ValueError("duplicate report field")
        fields[key] = value
    return fields


__all__ = ["grade_data_case"]
