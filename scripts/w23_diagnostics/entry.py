"""Initialize/run/finalize entry points. No work occurs on import."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from scripts.w23_diagnostics import phases
from scripts.w23_diagnostics.evidence import Redactor, digest, inventory, write_json
from scripts.w23_diagnostics.inputs import TOOL_ROOT, authenticate, contract
from scripts.w23_diagnostics.process import Commands, StageError, environment, require

_WORKFLOW_DIRECTORY = ".github/workflows/"
_INSTALLED_WORKFLOW_SUFFIX = "-installed-product.yml"


@dataclass(frozen=True)
class _ExecutionIdentity:
    commit: str
    tree: str
    parents: tuple[str, ...]
    inventory: tuple[tuple[str, str], ...]
    selection_sha256: str
    before_evidence_sha256: str


def _repository_state(
    commands: Commands, env: dict[str, str], label: str, *, verify_objects: bool = True,
) -> tuple[str, str, tuple[str, ...]]:
    identity = commands.run(label + "-identity", ["git", "rev-parse", "HEAD", "HEAD^{tree}"],
                            TOOL_ROOT, env, 30)
    require(identity)
    commit, tree, *extra = identity.stdout.split()
    if (extra or re.fullmatch(r"[a-f0-9]{40}", commit) is None
            or re.fullmatch(r"[a-f0-9]{40}", tree) is None):
        raise StageError("FAIL")

    lineage = commands.run(label + "-lineage", ["git", "rev-list", "--parents", "-n", "1", "HEAD"],
                           TOOL_ROOT, env, 30)
    require(lineage)
    lineage_ids = lineage.stdout.split()
    if (not lineage_ids or lineage_ids[0] != commit
            or any(re.fullmatch(r"[a-f0-9]{40}", value) is None for value in lineage_ids)):
        raise StageError("FAIL")

    status = commands.run(label + "-worktree", ["git", "status", "--porcelain=v1",
                            "--untracked-files=all", "--ignored=matching", "--ignore-submodules=none"],
                          TOOL_ROOT, env, 30)
    require(status)
    if status.stdout:
        raise StageError("FAIL")

    if verify_objects:
        objects = commands.run(label + "-objects", ["git", "fsck", "--full", "--no-reflogs", "--no-progress"],
                               TOOL_ROOT, env, 60)
        require(objects)
    return commit, tree, tuple(lineage_ids[1:])


def _evidence_digest(work: Path, relative: str) -> str:
    path = work / relative
    root_metadata = work.lstat()
    if (work.is_symlink() or getattr(root_metadata, "st_file_attributes", 0) & 0x400
            or not stat.S_ISDIR(root_metadata.st_mode)):
        raise ValueError("invalid evidence root")
    current = work
    for part in Path(relative).parts:
        current = current / part
        metadata = current.lstat()
        attributes = getattr(metadata, "st_file_attributes", 0)
        if current.is_symlink() or attributes & 0x400:
            raise ValueError("reparse evidence path")
        if current != path and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("invalid evidence parent")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("invalid evidence file")
    return digest(path)


def _installed_workflow_path(files: dict[str, str]) -> str:
    candidates = [
        name
        for name in files
        if name.startswith(_WORKFLOW_DIRECTORY)
        and "/" not in name[len(_WORKFLOW_DIRECTORY):]
        and name.endswith(_INSTALLED_WORKFLOW_SUFFIX)
    ]
    if len(candidates) != 1:
        raise StageError("FAIL")
    return candidates[0]


def _require_only_installed_workflow_change(expected: dict[str, str], actual: dict[str, str]) -> None:
    allowed = _installed_workflow_path(expected)
    changed = sorted(
        [name for name, value in expected.items() if actual.get(name) != value]
        + [name for name in actual if name not in expected]
    )
    if changed != [allowed]:
        raise StageError("FAIL")


def guard(work: Path) -> None:
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("disposable GitHub-hosted Windows required")
    expected = Path(os.environ["LOCALAPPDATA"]) / ("w23-manual-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"])
    if work != expected.resolve() or work.is_symlink() or getattr(work.lstat(), "st_file_attributes", 0) & 0x400:
        raise RuntimeError("workspace must be this run's private profile-local root")


def initialize(work: Path, mode: str) -> None:
    guard(work)
    marker = work / ".w23-new-root"
    expected_marker = os.environ["GITHUB_RUN_ID"] + "/" + os.environ["GITHUB_RUN_ATTEMPT"]
    if marker.read_text(encoding="utf-8") != expected_marker or len(list(work.iterdir())) != 1:
        raise RuntimeError("fresh protected workspace marker required")
    (work / "private").mkdir()
    (work / "publish").mkdir()
    runner_identity = {"run_id": os.environ["GITHUB_RUN_ID"], "attempt": os.environ["GITHUB_RUN_ATTEMPT"], "mode": mode}
    write_json(work / "owner.json", runner_identity)
    write_json(work / "publish/summary.json", {"status": "UNKNOWN", "mode": mode, "completed": False,
                                              "restoration": "UNKNOWN", "w23": "BLOCKED"})
    expected = (3, 10, 11) if mode == "r02" else (3, 12, 10)
    write_json(work / "publish/environment.json", {"python": sys.version, "actual": list(sys.version_info[:3]),
               "requested": list(expected), "exact": sys.version_info[:3] == expected, "platform": platform.platform(),
               "architecture_bits": 64 if sys.maxsize > 2**32 else 32, "logical_cpus": os.cpu_count(),
               "runner_image": {key: os.environ.get(key) for key in ("ImageOS", "ImageVersion", "RUNNER_ARCH")},
               "run_id": runner_identity["run_id"],
               "attempt": runner_identity["attempt"], "workflow_sha": os.environ.get("GITHUB_SHA")})


def _capture_tool_identity(work: Path, commands: Commands) -> tuple[dict[str, str], _ExecutionIdentity]:
    historical = contract()
    env = environment(work / "private/temp-git", TOOL_ROOT)
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    selected = os.environ["W23_EXPECTED_TOOLING_SHA"]
    if re.fullmatch(r"[a-f0-9]{40}", selected) is None:
        raise StageError("FAIL")
    commit, tree, parents = _repository_state(commands, env, "implementation")
    if commit != selected:
        raise StageError("FAIL")
    actual = inventory(TOOL_ROOT)
    final_state = _repository_state(commands, env, "implementation-after", verify_objects=False)
    if final_state != (commit, tree, parents):
        raise StageError("FAIL")
    inventory_bytes = json.dumps(actual, sort_keys=True, separators=(",", ":")).encode("utf-8")
    write_json(work / "publish/tooling-before.json", actual)
    write_json(work / "publish/selection.json", {"implementation_sha": commit, "implementation_tree": tree,
               "implementation_parents": list(parents),
               "identity_scope": "EXECUTION_PIN_ONLY_NO_AUTHORITY", "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
               "inventory_file_count": len(actual), "base_commit": historical["commit"],
               "base_tree": historical["tree"], "input_contract_sha256": digest(TOOL_ROOT / "quality/w23/diagnostics/inputs.json"),
               "patch_sha256": historical["patch_sha256"]})
    expected = _ExecutionIdentity(
        commit=commit,
        tree=tree,
        parents=parents,
        inventory=tuple(sorted(actual.items())),
        selection_sha256=_evidence_digest(work, "publish/selection.json"),
        before_evidence_sha256=_evidence_digest(work, "publish/tooling-before.json"),
    )
    return actual, expected


def tool_identity(work: Path, commands: Commands) -> dict[str, str]:
    actual, _expected = _capture_tool_identity(work, commands)
    return actual


def _check_git_and_evidence_integrity(
    work: Path, expected: _ExecutionIdentity | None, commands: Commands,
    result: dict[str, Any], record: Callable[[str], None],
) -> None:
    if expected is None:
        record("UNKNOWN")
    else:
        try:
            env = environment(work / "private/temp-git-postflight", TOOL_ROOT)
            env["GIT_NO_REPLACE_OBJECTS"] = "1"
            current = _repository_state(commands, env, "postflight")
            result["git_identity_unchanged"] = current == (expected.commit, expected.tree, expected.parents)
            if not result["git_identity_unchanged"]:
                record("FAIL")
        except StageError as exc:
            record(exc.status)
        except BaseException as exc:
            record("UNKNOWN")
            result["git_exception_type"] = type(exc).__name__

        for field, relative in (
            ("selection_unchanged", "publish/selection.json"),
            ("before_evidence_unchanged", "publish/tooling-before.json"),
        ):
            try:
                expected_digest = (expected.selection_sha256 if field == "selection_unchanged"
                                   else expected.before_evidence_sha256)
                unchanged = _evidence_digest(work, relative) == expected_digest
                result[field] = unchanged
                if not unchanged:
                    record("FAIL")
            except FileNotFoundError:
                record("UNKNOWN")
                result[field + "_exception_type"] = "FileNotFoundError"
            except BaseException as exc:
                record("UNKNOWN")
                result[field + "_exception_type"] = type(exc).__name__


def final_integrity(work: Path, expected: _ExecutionIdentity | None, commands: Commands) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "PASS",
        "tooling_unchanged": False,
        "git_identity_unchanged": False,
        "selection_unchanged": False,
        "before_evidence_unchanged": False,
    }

    def record(state: str) -> None:
        result["status"] = phases.aggregate([result["status"], state])

    try:
        after = inventory(TOOL_ROOT)
        write_json(work / "publish/tooling-after.json", after)
        result["tooling_unchanged"] = expected is not None and tuple(sorted(after.items())) == expected.inventory
        if expected is None:
            record("UNKNOWN")
        elif not result["tooling_unchanged"]:
            record("FAIL")
    except BaseException as exc:
        record("UNKNOWN")
        result["inventory_exception_type"] = type(exc).__name__

    _check_git_and_evidence_integrity(work, expected, commands, result, record)

    for name in ("source-base", "original", "patched"):
        root = work / "private" / name
        try:
            root.lstat()
        except FileNotFoundError:
            continue
        except BaseException as exc:
            record("UNKNOWN")
            result[f"terminal_{name}_exception_type"] = type(exc).__name__
            continue
        try:
            authenticate(root, work / "publish" / f"terminal-{name}.json", patched=name == "patched")
        except StageError as exc:
            record(exc.status)
        except BaseException as exc:
            record("UNKNOWN")
            result[f"terminal_{name}_exception_type"] = type(exc).__name__
    return result


def execute(work: Path, mode: str) -> int:
    guard(work)
    owner = json.loads((work / "owner.json").read_text(encoding="utf-8"))
    if owner != {"run_id": os.environ["GITHUB_RUN_ID"], "attempt": os.environ["GITHUB_RUN_ATTEMPT"], "mode": mode}:
        raise RuntimeError("workspace owner mismatch")
    redactor = Redactor([work, TOOL_ROOT])
    commands = Commands(work / "publish/commands", redactor)
    result: dict[str, Any] = {"status": "UNKNOWN", "mode": mode, "completed": False, "w23": "BLOCKED"}
    expected_identity: _ExecutionIdentity | None = None
    try:
        expected = (3, 10, 11) if mode == "r02" else (3, 12, 10)
        if sys.version_info[:3] != expected or sys.maxsize <= 2**32:
            raise StageError("UNKNOWN")  # Never silently substitute a microversion.
        _before, expected_identity = _capture_tool_identity(work, commands)
        result.update(phases.r01(work, commands) if mode == "r01" else phases.r02(work, commands))
        result["completed"] = True
    except StageError as exc:
        result.update(status=exc.status, completed=True)
    except BaseException as exc:
        result.update(status="UNKNOWN", exception_type=type(exc).__name__)
    finally:
        integrity = final_integrity(work, expected_identity, commands)
        result["integrity"] = integrity
        result["status"] = phases.aggregate([result["status"], integrity["status"]])
        result = redactor.value(result)
        write_json(work / "publish/summary.json", result)
    print(result["status"], flush=True)
    return 0 if result["status"] == "PASS" else 124 if result["status"] == "TIMEOUT" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("initialize", "run", "export"))
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--mode", choices=("r01", "r02"), required=True)
    parser.add_argument("--run-outcome", default="failure")
    args = parser.parse_args()
    work = args.work.resolve()
    guard(work)
    if args.action == "initialize":
        initialize(work, args.mode)
        return 0
    if args.action == "export":
        from scripts.w23_diagnostics.export import export
        return export(work, args.run_outcome)
    return execute(work, args.mode)
