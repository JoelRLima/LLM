"""Fail-closed checks for roadmap provenance in production surfaces."""

from __future__ import annotations

import io
import re
import sys
import tokenize
from pathlib import Path
from typing import Iterable

GOVERNANCE_EXCEPTIONS = frozenset(
    {
        "scripts/check_wave1_architecture.py",
        "scripts/check_wave2_architecture.py",
        "scripts/check_wave3_architecture.py",
        "scripts/check_wave4_architecture.py",
    }
)
SELF_PATH = "scripts/check_production_naming_hygiene.py"
SCAN_EXTENSIONS = frozenset({".py", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"})

# These forms identify development/roadmap provenance while leaving ordinary
# domain terms such as ``epoch``, ``phase_angle`` and ``block_size`` alone.
PROVENANCE_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:block|wave|gate|phase)[ _-]?\d+[A-Za-z0-9_-]*"
    r"|\bmarco[ _-]?\d+[A-Za-z0-9_-]*"
    r"|\bcen[-_]\d+[A-Za-z0-9_-]*"
    r"|\bB7[-_][A-Za-z0-9_-]*"
    r"|\bM3B[-_][A-Za-z0-9_-]*"
    r")"
)
IMPORT_RE = re.compile(
    r"(?i)(?:"
    r"\bfrom\s+[A-Za-z_][\w.]*\s+import\s+[^\n#]*\bblock7\w*"
    r"|\bimport\s+[A-Za-z_][\w.]*block7\w*"
    r"|\bagent[./\\]evaluation[./\\]block7\w*"
    r"|\bscripts[./\\]run_block7\.py"
    r")"
)


def _normalise_path(path: str | Path) -> str:
    return Path(path).as_posix().lstrip("./")


def _is_excluded(relative_path: str) -> bool:
    return relative_path in GOVERNANCE_EXCEPTIONS or relative_path == SELF_PATH


def _token_lines(text: str) -> tuple[set[int], set[int]]:
    """Return Python lines belonging to docstrings/comments and strings."""

    doc_lines: set[int] = set()
    string_lines: set[int] = set()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                doc_lines.update(range(token.start[0], token.end[0] + 1))
            elif token.type == tokenize.STRING:
                string_lines.update(range(token.start[0], token.end[0] + 1))
                if token.string.startswith(('"""', "'''") ):
                    doc_lines.update(range(token.start[0], token.end[0] + 1))
    except (IndentationError, SyntaxError, tokenize.TokenError):
        return set(), set()
    return doc_lines, string_lines


def _reason_for_line(
    line: str,
    match: re.Match[str],
    *,
    doc_lines: set[int],
    string_lines: set[int],
    line_number: int,
    python_source: bool,
) -> str:
    if IMPORT_RE.search(line):
        return "PNH-IMPORT"
    if python_source and line_number in doc_lines:
        return "PNH-DOC"
    if python_source and line_number in string_lines:
        return "PNH-STRING"
    if line.find('"', 0, match.start()) >= 0 or line.find("'", 0, match.start()) >= 0:
        return "PNH-STRING"
    if "#" in line[: match.start()]:
        return "PNH-DOC"
    return "PNH-ID"


def check_text(text: str, relative_path: str | Path) -> list[str]:
    """Check one production path and its source, returning stable findings."""

    relative = _normalise_path(relative_path)
    if _is_excluded(relative):
        return []

    findings: list[str] = []
    path_matches = list(PROVENANCE_RE.finditer(relative))
    for match in path_matches:
        findings.append(
            f"{relative}:path: PNH-PATH: roadmap provenance in production path ({match.group(0)})"
        )

    python_source = Path(relative).suffix.lower() == ".py"
    doc_lines, string_lines = _token_lines(text) if python_source else (set(), set())
    for line_number, line in enumerate(text.splitlines(), start=1):
        import_match = IMPORT_RE.search(line)
        if import_match:
            findings.append(
                f"{relative}:{line_number}: PNH-IMPORT: prohibited module/import identity ({import_match.group(0)})"
            )
        for match in PROVENANCE_RE.finditer(line):
            reason = _reason_for_line(
                line,
                match,
                doc_lines=doc_lines,
                string_lines=string_lines,
                line_number=line_number,
                python_source=python_source,
            )
            findings.append(
                f"{relative}:{line_number}: {reason}: roadmap provenance ({match.group(0)})"
            )
    return sorted(set(findings))


def _iter_production_files(root: Path) -> Iterable[Path]:
    candidates: set[Path] = set()
    for directory in (root / "agent", root / "scripts"):
        if directory.is_dir():
            candidates.update(path for path in directory.rglob("*") if path.is_file())
    for name in (
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "requirements-dev.txt",
    ):
        candidate = root / name
        if candidate.is_file():
            candidates.add(candidate)
    return sorted(
        (
            path
            for path in candidates
            if path.suffix.lower() in SCAN_EXTENSIONS
            and not _is_excluded(path.relative_to(root).as_posix())
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def check_repository(root: str | Path = ".") -> list[str]:
    """Scan production paths, source, exports and configuration deterministically."""

    repository = Path(root).resolve()
    findings: list[str] = []
    for path in _iter_production_files(repository):
        relative = path.relative_to(repository).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(f"{relative}:file: PNH-READ: unable to inspect production source ({exc})")
            continue
        findings.extend(check_text(text, relative))
    return sorted(set(findings))


def main() -> int:
    findings = check_repository(Path(__file__).resolve().parents[1])
    if findings:
        print("Production naming hygiene failed:")
        print("\n".join(findings))
        return 1
    print("Production naming hygiene passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
