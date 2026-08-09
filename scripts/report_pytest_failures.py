"""Emit concise GitHub annotations for failures recorded by pytest JUnit XML."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path


def _escape_annotation(value: str) -> str:
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def _failure_reason(element: ET.Element) -> str:
    message = element.get("message") or ""
    text = "".join(element.itertext()).strip()
    reason = message or text or "pytest failure"
    return reason.splitlines()[0][:500]


def report_failures(path: Path) -> int:
    """Print one annotation per failed testcase and return a process status."""

    if not path.is_file():
        print(
            "::error title=Pytest diagnostics::"
            f"JUnit report not found: {_escape_annotation(str(path))}"
        )
        return 0

    root = ET.parse(path).getroot()
    failures = 0
    for testcase in root.iter("testcase"):
        failure = testcase.find("failure")
        if failure is None:
            failure = testcase.find("error")
        if failure is None:
            continue
        failures += 1
        classname = testcase.get("classname", "pytest")
        name = testcase.get("name", "unknown")
        node = f"{classname}::{name}"
        reason = _failure_reason(failure)
        print(
            "::error title=Pytest failure::"
            f"{_escape_annotation(node)} — {_escape_annotation(reason)}"
        )

    if failures == 0:
        print("::notice title=Pytest diagnostics::No failed testcases in JUnit report")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit_xml", type=Path)
    args = parser.parse_args()
    return report_failures(args.junit_xml)


if __name__ == "__main__":
    raise SystemExit(main())
