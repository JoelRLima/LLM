"""Initialize/run/finalize entry points. No work occurs on import."""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from scripts.w23_diagnostics import phases
from scripts.w23_diagnostics.evidence import Redactor, digest, inventory, write_json
from scripts.w23_diagnostics.inputs import TOOL_ROOT, authenticate, contract
from scripts.w23_diagnostics.process import Commands, StageError, environment, require


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


def tool_identity(work: Path, commands: Commands) -> dict[str, str]:
    expected = contract()["files"]
    actual = inventory(TOOL_ROOT)
    changed = [name for name, value in expected.items() if actual.get(name) != value]
    if changed != [".github/workflows/wave18-installed-product.yml"]:
        raise StageError("FAIL")  # Other base bytes must stay frozen in the tooling checkout.
    env = environment(work / "private/temp-git", TOOL_ROOT)
    result = commands.run("implementation-identity", ["git", "rev-parse", "HEAD"], TOOL_ROOT, env, 30)
    require(result)
    selected = os.environ["W23_EXPECTED_TOOLING_SHA"]
    if result.stdout.strip() != selected:
        raise StageError("FAIL")
    write_json(work / "publish/tooling-before.json", actual)
    write_json(work / "publish/selection.json", {"implementation_sha": selected, "base_commit": contract()["commit"],
               "base_tree": contract()["tree"], "input_contract_sha256": digest(TOOL_ROOT / "quality/w23/diagnostics/inputs.json"),
               "patch_sha256": contract()["patch_sha256"]})
    return actual


def final_integrity(work: Path, before: dict[str, str] | None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "PASS"}
    try:
        after = inventory(TOOL_ROOT)
        write_json(work / "publish/tooling-after.json", after)
        result["tooling_unchanged"] = before is not None and before == after
        if before is None or before != after:
            result["status"] = "UNKNOWN" if before is None else "FAIL"
        for name in ("source-base", "original", "patched"):
            root = work / "private" / name
            if root.exists():
                authenticate(root, work / "publish" / f"terminal-{name}.json", patched=name == "patched")
    except StageError:
        result["status"] = "FAIL"
    except BaseException as exc:
        result.update(status="UNKNOWN", integrity_exception_type=type(exc).__name__)
    return result


def execute(work: Path, mode: str) -> int:
    guard(work)
    owner = json.loads((work / "owner.json").read_text(encoding="utf-8"))
    if owner != {"run_id": os.environ["GITHUB_RUN_ID"], "attempt": os.environ["GITHUB_RUN_ATTEMPT"], "mode": mode}:
        raise RuntimeError("workspace owner mismatch")
    redactor = Redactor([work, TOOL_ROOT])
    commands = Commands(work / "publish/commands", redactor)
    result: dict[str, Any] = {"status": "UNKNOWN", "mode": mode, "completed": False, "w23": "BLOCKED"}
    before: dict[str, str] | None = None
    try:
        expected = (3, 10, 11) if mode == "r02" else (3, 12, 10)
        if sys.version_info[:3] != expected or sys.maxsize <= 2**32:
            raise StageError("UNKNOWN")  # Never silently substitute a microversion.
        before = tool_identity(work, commands)
        result.update(phases.r01(work, commands) if mode == "r01" else phases.r02(work, commands))
        result["completed"] = True
    except StageError as exc:
        result.update(status=exc.status, completed=True)
    except BaseException as exc:
        result.update(status="UNKNOWN", exception_type=type(exc).__name__)
    finally:
        integrity = final_integrity(work, before)
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
