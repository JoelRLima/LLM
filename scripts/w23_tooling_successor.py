"""Explicit additive W23 byte verification; never grants functional acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.w23_architecture.evidence import (  # noqa: E402
    EvidenceError,
    artifact_ref,
    verify_witness,
)

WITNESS = "quality/w23/acceptance_tooling_witness_v001.json"
WITNESS_SHA256 = "89354369cf111fbc5d1b4d4d4c1d7d76899bba50d2abf77af52daa0144ca586b"
ARCHIVE = "quality/w23/historical_tooling/checkpoint_e3c1b615"
RECORD = "quality/w23/tooling_corrections_20261015_f01_f02.json"
CLEANUP_PATH = "src/llm_agent/execution/cleanup.py"
CLEANUP_BEFORE = "3e87791607dc8d0f20ddaa47f0d5c3020fcc6d7e5b02a04b377a04f7113fa973"
CLEANUP_AFTER = "1e037e92bc12bd500297da98461618a307d0dd9c98810e9cec6bd4bfcea32b42"
CLEANUP_ARCHIVE = "quality/w23/tooling_corrections_20261015/src/llm_agent/execution/cleanup.py.blob"
REVIEWED_CORRECTIONS: dict[str, tuple[str, str, str]] = {
    CLEANUP_PATH: (CLEANUP_ARCHIVE, CLEANUP_BEFORE, CLEANUP_AFTER),
    'src/llm_agent/agent/skills/shell_process.py': ('quality/w23/tooling_corrections_20261015_v002/src/llm_agent/agent/skills/shell_process.py.blob', '7a0219436da418c53da92ef5dac59a0941c9a209b535a60f5fa026f6a9ad54e4', '80aa4e7aa2e2561c84bab4100dab8e2ce171198f1576ae15a78be3050d540abf'),
    'src/llm_agent/execution/command.py': ('quality/w23/tooling_corrections_20261015_v002/src/llm_agent/execution/command.py.blob', '1c093c3c60f12991339619c25159743a8983a866fea3a0ee43df46c055ea6853', 'd8e70e784f53f077668efd22da1edabe944e0bf27aa21c7c78646a5868d655ad'),
}
BASE_COMMIT = "e3c1b6151e53082485d255a6ed80df403bf65426"
BASE_TREE = "57da004b57bf46bd161fb9f0dde7eabcef87e51e"
COMMANDS = "src/llm_agent/application/_task_commands.py"
COMMANDS_SHA256 = "3a14c2d7e6b4088663c8110be48a015c32201a98fccce2fda1efe91f7e3756e8"
PREDECESSOR_PRODUCTION = "6b2e70a908b2bb719d6b7e1c55869a3ba458815a249735326ca9b0045c7f62ff"
ATTRIBUTES_SHA256 = "9099ef32802b1628f4c598bcb98af80094d603c210ced9b902817a4d72fb0e25"
LOCK_REPORT = {"path": "quality/w23/cycle_report_after_lock_ownership.json",
               "sha256": "baba1da510914f2e8d355a4a983e97a7d3afccc66da341a479cb6783ec1e5389"}
RETIREMENT_REPORT = {"path": "quality/w23/cycle_report_after_compatibility_retirement.json",
                     "sha256": "ac8242d1d3cbbf065c7e9b041df05aa3b26cac86a0aeb86834126cb281f89ae1"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(root: Path, path: str) -> bytes:
    artifact_ref({"path": path, "sha256": "0" * 64})
    current = root
    for component in path.split("/"):
        current = current / component
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise EvidenceError(f"symlink or reparse artifact: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise EvidenceError(f"artifact is not a regular file: {path}")
    return current.read_bytes()


def _authenticated(root: Path, reference: Mapping[str, str]) -> bytes:
    ref = artifact_ref(dict(reference))
    data = _read(root, ref["path"])
    if _sha(data) != ref["sha256"]:
        raise EvidenceError(f"artifact digest mismatch: {ref['path']}")
    return data


def read_historical_tooling(root: Path) -> dict[str, bytes]:
    """Read exactly the frozen v001 toolings; missing archives never use live code."""
    witness = json.loads(_authenticated(root, {"path": WITNESS, "sha256": WITNESS_SHA256}))
    return {
        row["path"]: _authenticated(root, {"path": f"{ARCHIVE}/{row['path']}.blob", "sha256": row["sha256"]})
        for row in witness["current_tooling"]
    }


def verify_historical_files(root: Path) -> dict[str, Any]:
    """Historical PASS authenticates archived bytes, never the corrected checkout."""
    raw = _authenticated(root, {"path": WITNESS, "sha256": WITNESS_SHA256})
    witness = json.loads(raw)
    tooling = read_historical_tooling(root)
    if _sha(_read(root, "scripts/w23_architecture/evidence.py")) != _sha(tooling["scripts/w23_architecture/evidence.py"]):
        raise EvidenceError("historical verifier implementation changed")
    snapshots = {row["path"]: _authenticated(root, {"path": row["path"], "sha256": row["sha256"]})
                 for row in witness["historical_tooling_snapshots"]}
    return dict(verify_witness(raw, _authenticated(root, witness["predecessor"]),
                              _authenticated(root, witness["production_evidence"]), tooling, snapshots))


def read_historical_retired_sources(root: Path) -> dict[str, bytes]:
    """Authenticate all retired sources against the immutable lock predecessor."""
    lock = json.loads(_authenticated(root, LOCK_REPORT))
    retired = json.loads(_authenticated(root, RETIREMENT_REPORT))["unit_delta"]["removed"]
    inventory = dict(lock["predecessor_source_inventory"])
    for path, before, after in lock["unit_delta"]["changed_existing"]:
        if inventory.get(path) != before:
            raise EvidenceError("lock inventory lineage differs")
        inventory[path] = after
    inventory.update(dict(lock["unit_delta"]["added"]))
    if inventory_sha256([[path, inventory[path]] for path in sorted(inventory)]) != lock["candidate"]["source_fingerprint_sha256"]:
        raise EvidenceError("retired predecessor inventory differs")
    return {path: _authenticated(root, {"path": f"{ARCHIVE}/{path}.blob", "sha256": inventory[path]})
            for path in retired}


def read_historical_retirement_inputs(root: Path) -> dict[str, bytes]:
    """Use the explicit e3 tooling witness plus three unchanged pinned data inputs."""
    inputs = read_historical_tooling(root)
    report = json.loads(_authenticated(root, RETIREMENT_REPORT))
    for path in ("scripts/check_w21_compatibility.py", "tests/unit/runtime/test_wave21_compatibility.py",
                 "quality/w23/cycle_report_before.json"):
        inputs[path] = _authenticated(root, {"path": f"{ARCHIVE}/{path}.blob", "sha256": report["input_sha256"][path]})
    return inputs


def _production_paths(directory: Path) -> list[Path]:
    info = directory.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise EvidenceError("production directory is a symlink or reparse point")
    paths: list[Path] = []
    with os.scandir(directory) as entries:
        for child in entries:
            info = child.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise EvidenceError("production entry is a symlink or reparse point")
            if stat.S_ISDIR(info.st_mode):
                paths.extend(_production_paths(Path(child.path)))
            elif stat.S_ISREG(info.st_mode):
                if Path(child.path).suffix == ".py":
                    paths.append(Path(child.path))
            else:
                raise EvidenceError("unsupported production filesystem entry")
    return paths


def production_inventory(root: Path) -> list[list[str]]:
    return sorted([[path.relative_to(root).as_posix(), _sha(_read(root, path.relative_to(root).as_posix()))]
                   for path in _production_paths(root / "src/llm_agent")])


def inventory_sha256(inventory: list[list[str]]) -> str:
    return _sha(json.dumps(inventory, separators=(",", ":")).encode("utf-8"))


def _reviewed_predecessors(root: Path) -> dict[str, str]:
    result = {}
    for path, (archive, before, after) in REVIEWED_CORRECTIONS.items():
        _authenticated(root, {"path": archive, "sha256": before})
        if _sha(_read(root, path)) != after:
            raise EvidenceError("reviewed production correction differs: " + path)
        result[path] = before
    return result


def project_historical_production(root: Path, current: list[list[str]]) -> list[list[str]]:
    """Invert only the exact archived presentation and reviewed orphan-cleanup corrections."""
    if current != production_inventory(root):
        raise EvidenceError("supplied current inventory is stale or incomplete")
    old = _authenticated(root, {"path": f"{ARCHIVE}/{COMMANDS}.blob", "sha256": COMMANDS_SHA256})
    before = "indisponÃ­vel".encode()
    expected = old.replace(before, "indisponível".encode()).replace(b"\r\n", b"\n")
    if old.count(before) != 1 or _read(root, COMMANDS) != expected:
        raise EvidenceError("production delta is not the exact presentation correction")
    predecessors = {COMMANDS: COMMANDS_SHA256, **_reviewed_predecessors(root)}
    projected = [[path, predecessors.get(path, digest)] for path, digest in current]
    if inventory_sha256(projected) != PREDECESSOR_PRODUCTION:
        raise EvidenceError("historical production projection differs from the predecessor")
    return projected


def _rows(root: Path, rows: Any) -> dict[str, str]:
    if not isinstance(rows, list):
        raise EvidenceError("artifact rows must be a list")
    paths: list[str] = []
    result: dict[str, str] = {}
    for row in rows:
        ref = artifact_ref(row)
        _authenticated(root, ref)
        paths.append(ref["path"])
        result[ref["path"]] = ref["sha256"]
    if paths != sorted(set(paths)):
        raise EvidenceError("artifact rows must be sorted and unique")
    return result


def _selected_record(record_bytes: bytes, expected_sha256: str) -> dict[str, Any]:
    artifact_ref({"path": RECORD, "sha256": expected_sha256})
    if _sha(record_bytes) != expected_sha256:
        raise EvidenceError("successor selection digest mismatch")
    record = json.loads(record_bytes)
    canonical = (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    fields = {"schema_version", "kind", "base", "status", "predecessor", "current_tooling",
              "additional_files", "production", "observations", "remaining_blockers"}
    if not isinstance(record, dict) or set(record) != fields or canonical != record_bytes:
        raise EvidenceError("successor schema or canonical bytes differ")
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise EvidenceError("unsupported successor schema")
    if record["kind"] != "W23_ADDITIVE_TOOLING_CORRECTIONS" or record["status"] != "PENDING_USER_ACCEPTANCE":
        raise EvidenceError("successor must remain pending functional acceptance")
    if record["base"] != {"commit": BASE_COMMIT, "tree": BASE_TREE}:
        raise EvidenceError("successor base differs")
    if record["predecessor"] != {"path": WITNESS, "sha256": WITNESS_SHA256}:
        raise EvidenceError("successor predecessor differs")
    return record


def _verify_production(root: Path, record: Mapping[str, Any]) -> None:
    production = record["production"]
    if not isinstance(production, dict) or set(production) != {"files", "sha256", "historical_projection_sha256"}:
        raise EvidenceError("production identity schema differs")
    actual = production_inventory(root)
    expected = [[path, digest] for path, digest in _rows(root, production["files"]).items()]
    if actual != expected or inventory_sha256(actual) != production["sha256"]:
        raise EvidenceError("current production identity differs")
    projected = project_historical_production(root, actual)
    if inventory_sha256(projected) != production["historical_projection_sha256"]:
        raise EvidenceError("historical projection identity differs")


def _verify_observations(root: Path, record: Mapping[str, Any]) -> None:
    if not isinstance(record["observations"], list) or not isinstance(record["remaining_blockers"], list):
        raise EvidenceError("successor observations/blockers must be lists")
    for observation in record["observations"]:
        if not isinstance(observation, dict) or set(observation) != {"command", "executor", "platform", "exit_code", "scope", "output"}:
            raise EvidenceError("observation schema differs")
        if type(observation["exit_code"]) is not int:
            raise EvidenceError("observation exit_code must be an integer")
        for field in ("command", "executor", "platform", "scope"):
            value = observation[field]
            if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
                raise EvidenceError("observation attribution must be nonempty control-free text")
        _authenticated(root, observation["output"])


def _verify_git_transport(root: Path) -> None:
    old = _authenticated(root, {"path": f"{ARCHIVE}/.gitattributes.blob", "sha256": ATTRIBUTES_SHA256})
    if _read(root, ".gitattributes") != old:
        raise EvidenceError("original Git attributes changed")


def verify_successor(root: Path, record_bytes: bytes, expected_sha256: str) -> dict[str, Any]:
    """Verify independently selected successor bytes and separate historical/current identities."""
    record = _selected_record(record_bytes, expected_sha256)
    historical = verify_historical_files(root)
    read_historical_retired_sources(root)
    read_historical_retirement_inputs(root)
    live_tooling = _rows(root, record["current_tooling"])
    if set(live_tooling) != {row["path"] for row in historical["current_tooling"]}:
        raise EvidenceError("successor tooling membership differs")
    additional = _rows(root, record["additional_files"])
    expected_additional = {
        CLEANUP_ARCHIVE, "quality/w23/tooling_corrections_20261015/.gitattributes",
        "quality/w23/tooling_corrections_20261015_v002/.gitattributes",
        'quality/w23/tooling_corrections_20261015_v002/src/llm_agent/agent/skills/shell_process.py.blob',
        'quality/w23/tooling_corrections_20261015_v002/src/llm_agent/execution/command.py.blob',

        COMMANDS, "scripts/w23_tooling_successor.py", "tests/unit/test_candidate_manifest_regressions.py",
        "tests/unit/test_w23_tooling_successor.py", f"{ARCHIVE}/.gitattributes",
        "tests/unit/test_task_execution_application.py",
        ".gitattributes", f"{ARCHIVE}/.gitattributes.blob",
        "quality/w23/tooling_corrections_20261012/.gitattributes",
    }
    if set(additional) != expected_additional:
        raise EvidenceError("additional artifact membership differs")
    for attributes in (f"{ARCHIVE}/.gitattributes", "quality/w23/tooling_corrections_20261012/.gitattributes"):
        if _read(root, attributes) != b"*.blob -text\n":
            raise EvidenceError("archive raw-byte Git attributes differ")
    _verify_production(root, record)
    _verify_observations(root, record)
    _verify_git_transport(root)
    return {"historical_integrity": "PASS_ARCHIVED_BYTES", "current_identity": "PASS_CURRENT_BYTES",
            "functional_acceptance": "PENDING_USER_ACCEPTANCE", "successor_sha256": expected_sha256}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True)
    parser.add_argument("--sha256", required=True, help="Independently expected record digest; no discovery/default.")
    args = parser.parse_args()
    result = verify_successor(ROOT, _read(ROOT, args.record), args.sha256)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
