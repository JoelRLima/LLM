"""Run the repository's local validation gates with a stable repository cwd."""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Gate:
    label: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class Group:
    name: str
    gates: tuple[Gate, ...]


STATIC = Group(
    "static",
    (
        Gate("Ruff", (sys.executable, "-m", "ruff", "check", ".")),
        Gate("Repository quality policy", (sys.executable, "scripts/check_quality.py")),
        Gate("Mypy Linux", (sys.executable, "-m", "mypy", "--platform", "linux")),
        Gate("Mypy Windows", (sys.executable, "-m", "mypy", "--platform", "win32")),
        Gate("Git diff check", ("git", "-c", "core.whitespace=cr-at-eol", "diff", "--check")),
    ),
)
TESTS = Group("tests", (Gate("Pytest", (sys.executable, "-m", "pytest", "-q")),))
INSTALLED = Group("installed", (Gate("Installed package", (sys.executable, "scripts/verify_installed_package.py")),))
PROFILES: dict[str, tuple[Group, ...]] = {group.name: (group,) for group in (STATIC, TESTS, INSTALLED)}
PROFILES["all"] = (STATIC, TESTS, INSTALLED)


def selected_groups(profile: str) -> tuple[Group, ...]:
    """Return the groups in execution order for a named profile."""
    try:
        return PROFILES[profile]
    except KeyError as exc:
        raise ValueError(f"unknown validation profile: {profile}") from exc


def _display(command: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(command))


def _emit(message: str) -> None:
    print(message, flush=True)


def run_profile(
    profile: str,
    *,
    repo: Path = ROOT,
    execute: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    emit: Callable[[str], object] = _emit,
    plan: bool = False,
) -> int:
    """Run a profile, reporting every gate and continuing after failures."""
    outcomes: list[tuple[str, int]] = []
    for group in selected_groups(profile):
        for gate in group.gates:
            emit(f"[{group.name}] {gate.label}: {_display(gate.command)}")
            if plan:
                continue
            try:
                result = execute(gate.command, cwd=repo, check=False)
                code = int(result.returncode)
            except OSError as exc:
                code = 127
                emit(f"  execution error (code {code}): {exc}")
            outcomes.append((gate.label, code))
    if plan:
        return 0
    for label, code in outcomes:
        emit(f"{label}: {'PASS' if code == 0 else 'FAIL'} (exit {code})")
    emit(f"Summary: {sum(code == 0 for _, code in outcomes)}/{len(outcomes)} gates passed")
    return 0 if all(code == 0 for _, code in outcomes) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=tuple(PROFILES))
    parser.add_argument("--plan", action="store_true", help="show commands without running them")
    args = parser.parse_args(argv)
    return run_profile(args.profile, plan=args.plan)


if __name__ == "__main__":
    raise SystemExit(main())
