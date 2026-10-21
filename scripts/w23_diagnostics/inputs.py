"""Authenticate the fixed 5acccd7 source and reconstruct independent trees."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.w23_diagnostics.evidence import digest, inventory, write_json
from scripts.w23_diagnostics.process import Commands, StageError, environment, require

TOOL_ROOT = Path(__file__).resolve().parents[2]
ASSETS = TOOL_ROOT / "quality/w23/diagnostics"
INPUTS_SHA256 = "7b8299e3bec5c7180411008320d83a104f5331cfc9eea959abca25f46de0c497"


def contract() -> dict[str, Any]:
    path = ASSETS / "inputs.json"
    if digest(path) != INPUTS_SHA256:
        raise StageError("FAIL")
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if digest(ASSETS / "r02.patch") != value["patch_sha256"]:
        raise StageError("FAIL")
    return value


def authenticate(root: Path, output: Path, patched: bool = False) -> dict[str, str]:
    expected = dict(contract()["files"])
    if patched:
        expected.update(contract()["patched_files"])
    actual = inventory(root)
    write_json(output, actual)
    if actual != expected:
        raise StageError("FAIL")
    return actual


def reconstruct(work: Path, name: str, commands: Commands, patched: bool = False) -> Path:
    spec = contract()
    root = work / "private" / name
    env = environment(work / "private" / "temp", TOOL_ROOT)
    actions = [
        ["-c", "core.hooksPath=NUL", "clone", "--no-local", "--no-hardlinks", "--no-checkout", str(TOOL_ROOT), str(root)],
        ["-C", str(root), "config", "core.hooksPath", "NUL"],
        ["-C", str(root), "checkout", "--detach", spec["commit"]],
        ["-C", str(root), "remote", "remove", "origin"],
    ]
    for arguments in actions:
        require(commands.run("git-prepare", ["git", *arguments], TOOL_ROOT, env, 60))
    if (root / ".git/objects/info/alternates").exists():
        raise StageError("FAIL")
    identity = commands.run("git-identity", ["git", "rev-parse", "HEAD", "HEAD^{tree}"], root, env, 30)
    require(identity)
    if identity.stdout.split() != [spec["commit"], spec["tree"]]:
        raise StageError("FAIL")
    authenticate(root, work / "publish" / f"{name}-base.json")
    if patched:
        apply_patch(root, commands, env)
    authenticate(root, work / "publish" / f"{name}-before.json", patched)
    return root


def apply_patch(root: Path, commands: Commands, env: dict[str, str]) -> None:
    patch = str(ASSETS / "r02.patch")
    for arguments in (["apply", "--check", patch], ["apply", patch]):
        require(commands.run("git-patch", ["git", *arguments], root, env, 30))
