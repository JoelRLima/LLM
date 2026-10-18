"""Historical byte integrity and separately selected live tooling corrections."""

from __future__ import annotations

import copy
import hashlib
import json
import stat
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import w23_tooling_successor as successor
from scripts.w23_architecture.evidence import EvidenceError

ROOT = Path(__file__).resolve().parents[2]


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def test_explicit_current_successor_and_archived_predecessor_are_separate() -> None:
    raw = (ROOT / successor.RECORD).read_bytes()
    result = successor.verify_successor(ROOT, raw, hashlib.sha256(raw).hexdigest())
    assert result["historical_integrity"] == "PASS_ARCHIVED_BYTES"
    assert result["current_identity"] == "PASS_CURRENT_BYTES"
    assert result["functional_acceptance"] == "PENDING_USER_ACCEPTANCE"
    old = successor.read_historical_tooling(ROOT)
    assert old["distribution/candidate_snapshot.py"] != (ROOT / "distribution/candidate_snapshot.py").read_bytes()
    from scripts.w23_acceptance import AcceptanceError, verify_portable_files

    with pytest.raises(AcceptanceError):
        verify_portable_files(ROOT)


def test_successor_requires_independently_expected_digest() -> None:
    raw = (ROOT / successor.RECORD).read_bytes()
    with pytest.raises(EvidenceError, match="selection digest"):
        successor.verify_successor(ROOT, raw, "0" * 64)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "stale", "accepted", "wrong_base", "wrong_predecessor", "stale_production"])
def test_successor_mutations_fail_closed(mutation: str) -> None:
    record = copy.deepcopy(json.loads((ROOT / successor.RECORD).read_bytes()))
    if mutation == "missing":
        record["current_tooling"].pop()
    elif mutation == "extra":
        record["additional_files"].append(record["current_tooling"][0])
        record["additional_files"].sort(key=lambda row: row["path"])
    elif mutation == "duplicate":
        record["current_tooling"].append(record["current_tooling"][-1])
    elif mutation == "stale":
        record["current_tooling"][0]["sha256"] = "0" * 64
    elif mutation == "accepted":
        record["status"] = "ACCEPTED"
    elif mutation == "wrong_base":
        record["base"]["commit"] = "0" * 40
    elif mutation == "wrong_predecessor":
        record["predecessor"]["sha256"] = "0" * 64
    else:
        record["production"]["files"][0]["sha256"] = "0" * 64
    raw = _canonical(record)
    with pytest.raises(EvidenceError):
        successor.verify_successor(ROOT, raw, hashlib.sha256(raw).hexdigest())


def test_missing_archive_cannot_fall_back_to_corrected_code(monkeypatch: pytest.MonkeyPatch) -> None:
    original = successor._read
    missing = f"{successor.ARCHIVE}/distribution/candidate_manifest.py.blob"
    seen: list[str] = []

    def read(root: Path, path: str) -> bytes:
        seen.append(path)
        if path == missing:
            raise FileNotFoundError(path)
        return original(root, path)

    monkeypatch.setattr(successor, "_read", read)
    with pytest.raises(FileNotFoundError):
        successor.read_historical_tooling(ROOT)
    assert "distribution/candidate_manifest.py" not in seen


def test_historical_projection_requires_exact_live_production() -> None:
    actual = successor.production_inventory(ROOT)
    projected = successor.project_historical_production(ROOT, actual)
    assert successor.inventory_sha256(actual) != successor.PREDECESSOR_PRODUCTION
    assert successor.inventory_sha256(projected) == successor.PREDECESSOR_PRODUCTION
    with pytest.raises(EvidenceError, match="stale or incomplete"):
        successor.project_historical_production(ROOT, actual[:-1])


def test_retired_predecessor_sources_are_authenticated_without_moving_head() -> None:
    retired = successor.read_historical_retired_sources(ROOT)
    assert len(retired) == 26
    assert all(not (ROOT / path).exists() for path in retired)


@pytest.mark.parametrize("field,value", [("exit_code", True), ("exit_code", "0"), ("command", ""), ("executor", {}), ("scope", "bad\ntext")])
def test_observation_attribution_cannot_be_malformed(field: str, value: object) -> None:
    record = json.loads((ROOT / successor.RECORD).read_bytes())
    output = {"path": successor.WITNESS, "sha256": successor.WITNESS_SHA256}
    observation = {"command": "inert command", "executor": "test", "platform": "fake", "scope": "pure", "exit_code": 0, "output": output}
    observation[field] = value
    record["observations"] = [observation]
    raw = _canonical(record)
    with pytest.raises(EvidenceError, match="observation"):
        successor.verify_successor(ROOT, raw, hashlib.sha256(raw).hexdigest())


def test_production_scan_rejects_even_non_python_reparse_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "src/llm_agent"
    directory.mkdir(parents=True)
    info = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
    child = SimpleNamespace(path=str(directory / "ignored-directory"), stat=lambda **_kwargs: info)
    monkeypatch.setattr(successor.os, "scandir", lambda _path: nullcontext(iter([child])))
    with pytest.raises(EvidenceError, match="symlink or reparse"):
        successor.production_inventory(tmp_path)
