"""Simulate remote CI with committed projections and no local authority tree."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


_OMIT_NAMES = {
    ".git",
    ".agent-local",
    ".audit-local",
    ".tmp",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "venv",
}


def _ignore(directory: str, names: list[str]) -> set[str]:
    del directory
    return {name for name in names if name in _OMIT_NAMES or name.endswith(".pyc")}


def _focused_tests(root: Path) -> tuple[str, ...]:
    candidates = (
        "tests/unit/runtime/test_wave21_architecture.py",
        "tests/unit/runtime/test_wave21_compatibility.py",
        "tests/unit/runtime/test_wave21_scope_checker.py",
        "tests/unit/runtime/test_wave21_checker_equivalence.py",
        "tests/unit/runtime/test_wave21_projection.py",
        "tests/unit/runtime/test_wave21_remote_ci.py",
        "tests/unit/runtime/test_wave21_authority.py",
    )
    return tuple(relative for relative in candidates if (root / relative).is_file())


def _forbidden_local_references(root: Path) -> list[str]:
    findings: list[str] = []
    for path in (root / "tests").rglob("test_wave21_*.py"):
        text = path.read_text(encoding="utf-8")
        if ".agent-local" in text or "LLM Agent Harness" in text:
            findings.append(path.relative_to(root).as_posix())
    return findings


def run_remote_simulation(python: Path, root: Path = ROOT) -> int:
    temporary_root = root / ".tmp" / "w21-remote-ci"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="candidate-", dir=temporary_root) as temporary:
        candidate = Path(temporary) / "candidate"
        shutil.copytree(root, candidate, ignore=_ignore)
        missing = [name for name in (".git", ".agent-local", ".audit-local") if (candidate / name).exists()]
        if missing:
            print(f"remote-CI simulation: FAIL; forbidden local inputs copied: {', '.join(missing)}")
            return 1
        references = _forbidden_local_references(candidate)
        if references:
            print("remote-CI simulation: FAIL; tests infer local coordinator state:")
            print("\n".join(references))
            return 1
        env = os.environ.copy()
        env["PYTHONPATH"] = str(candidate)
        for name in tuple(env):
            if name.startswith("W21_") or name in {"EPOCH_AUTHORITY", "CORRECTIVE_AUTHORITY"}:
                env.pop(name, None)
        tests = _focused_tests(candidate)
        if not tests:
            print("remote-CI simulation: FAIL; no focused W21 tests found")
            return 1
        pytest_temp = candidate / ".tmp" / "w21-pytest"
        pytest_temp.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            (str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:anyio", "--basetemp", str(pytest_temp), *tests),
            cwd=candidate,
            env=env,
            text=True,
        )
        if result.returncode:
            print("remote-CI simulation: FAIL")
            return result.returncode
        print("remote-CI simulation: PASS (.agent-local absent)")
        return 0


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run focused W21 tests without local authority inputs")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    return run_remote_simulation(arguments.python, arguments.root)


if __name__ == "__main__":
    raise SystemExit(_main())
