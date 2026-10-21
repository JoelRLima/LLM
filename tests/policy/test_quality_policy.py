import json
import re
import subprocess
import sys
from pathlib import Path

from scripts.check_quality import (
    QUALITY_EVIDENCE_ROOT,
    ROOT,
    _is_ignored,
    _ratchet_failures,
    check_architecture,
    check_markdown_links,
    check_source_visibility,
    check_text_encoding,
)


def _ruff_complexity_probe() -> str:
    branches = "".join(f"    if value == {index}:\n        return {index}\n" for index in range(11))
    return f"def quality_input_probe(value):\n{branches}    return None\n"


def test_quality_evidence_exclusion_is_exactly_root_scoped(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("scripts.check_quality.ROOT", tmp_path)
    assert QUALITY_EVIDENCE_ROOT == ".pytest-c14-evidence-20261001-0953"
    assert _is_ignored(tmp_path / QUALITY_EVIDENCE_ROOT / "nested" / "invalid.py")
    assert not _is_ignored(tmp_path / ".pytest-c14-evidence-20261001-0953-sibling" / "invalid.py")
    assert not _is_ignored(tmp_path / ".pytest-c14-other" / "invalid.py")
    assert not _is_ignored(tmp_path / "nested" / QUALITY_EVIDENCE_ROOT / "invalid.py")


def test_ruff_exact_exclusion_preserves_all_other_quality_inputs(tmp_path) -> None:
    repository_ruff = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    exclusion = re.search(r"^extend-exclude\s*=\s*(\[[^\r\n]+\])\s*$", repository_ruff, re.MULTILINE)
    assert exclusion is not None
    config = tmp_path / "pyproject.toml"
    config.write_text(
        f"[tool.ruff]\nextend-exclude = {exclusion.group(1)}\n"
        "[tool.ruff.lint.mccabe]\nmax-complexity = 10\n",
        encoding="utf-8",
    )
    paths = (
        ".pytest-c14-evidence-20261001-0953/negative.py",
        ".pytest-c14-evidence-20261001-0953-sibling/negative.py",
        ".pytest-c14-other/negative.py",
        "src/quality_probe.py",
        "scripts/quality_probe.py",
        "tests/quality_probe.py",
        "tests/fixtures/quality_probe.py",
    )
    for relative in paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_ruff_complexity_probe(), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            ".",
            "--no-cache",
            "--select",
            "C901",
            "--output-format",
            "json",
            "--config",
            str(config),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    findings = json.loads(result.stdout)
    found = {Path(item["filename"]).relative_to(tmp_path).as_posix() for item in findings}
    assert found == set(paths[1:])


def test_quality_artifact_checks_skip_only_the_authenticated_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("scripts.check_quality.ROOT", tmp_path)
    evidence = tmp_path / QUALITY_EVIDENCE_ROOT
    sibling = tmp_path / ".pytest-c14-evidence-20261001-0953-sibling"
    evidence.mkdir()
    sibling.mkdir()
    (evidence / "broken.md").write_text("[missing](absent.md)\n", encoding="utf-8")
    (sibling / "broken.md").write_text("[missing](absent.md)\n", encoding="utf-8")
    (evidence / "broken.json").write_bytes(b"\xff")
    (sibling / "broken.json").write_bytes(b"\xff")

    link_failures, checked_links = check_markdown_links()
    encoding_failures, checked_text = check_text_encoding()

    assert checked_links == 1
    assert len(link_failures) == 1
    assert ".pytest-c14-evidence-20261001-0953-sibling/broken.md" in link_failures[0]
    assert checked_text == 2  # The sibling artifact plus this temporary pyproject.toml.
    assert len(encoding_failures) == 1
    assert ".pytest-c14-evidence-20261001-0953-sibling/broken.json" in encoding_failures[0]


def test_quality_ratchet_accepts_only_the_recorded_debt() -> None:
    assert _ratchet_failures(label="test", current={"module": 12}, allowed={"module": 12}) == []


def test_quality_ratchet_rejects_new_or_increased_debt() -> None:
    failures = _ratchet_failures(
        label="test",
        current={"existing": 13, "new": 11},
        allowed={"existing": 12},
    )

    assert failures == [
        "test: existing aumentou de 12 para 13",
        "test: nova divida em new (11)",
    ]


def test_quality_ratchet_requires_baseline_cleanup_after_improvement() -> None:
    failures = _ratchet_failures(
        label="test",
        current={"smaller": 11},
        allowed={"removed": 15, "smaller": 12},
    )

    assert failures == [
        "test: reduza a baseline de smaller de 12 para 11",
        "test: remova da baseline a entrada obsoleta removed",
    ]


def test_repository_respects_stable_architecture_boundaries() -> None:
    failures, checked_modules = check_architecture()

    assert checked_modules > 0
    assert failures == []


def test_runtime_source_is_not_treated_as_generated_runtime_data() -> None:
    assert _is_ignored(ROOT / "runtime" / "agent.log")
    assert not _is_ignored(ROOT / "src" / "agent" / "runtime" / "config.py")


def test_repository_python_sources_are_visible_to_git() -> None:
    failures, checked_files = check_source_visibility()

    assert checked_files > 0
    assert failures == []


def test_local_documentation_links_are_valid() -> None:
    failures, checked_links = check_markdown_links()

    assert checked_links > 0
    assert failures == []


def test_repository_text_files_are_bom_free_utf8() -> None:
    failures, checked_files = check_text_encoding()

    assert checked_files > 0
    assert failures == []
