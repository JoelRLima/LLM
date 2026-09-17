from __future__ import annotations

import json
from pathlib import Path

import pytest

from distribution.payload import make_inventory
from scripts.verify_installed_product import (
    ProductVerificationError,
    _v3_assert_candidate,
)

CANDIDATE_ID = "w18-" + "a" * 32


def _candidate(tmp_path: Path, extra: tuple[str, ...] = ()) -> tuple[Path, dict[str, str]]:
    candidate = tmp_path / CANDIDATE_ID
    (candidate / "runtime").mkdir(parents=True)
    (candidate / "app").mkdir()
    (candidate / "bin").mkdir()
    (candidate / "runtime" / "python.exe").write_bytes(b"embedded-python")
    (candidate / "app" / "launcher.py").write_text("launcher\n", encoding="utf-8")
    (candidate / "bin" / "llm-agent.cmd").write_text("@echo off\r\n", encoding="utf-8")
    for relative in extra:
        path = candidate / Path(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"payload-member")
    inventory = make_inventory(candidate)
    (candidate / "payload-files.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return candidate, {"candidate_id": CANDIDATE_ID}


@pytest.mark.parametrize(
    "relative",
    (
        "runtime/Lib/venv/__init__.py",
        "runtime/Lib/venv/scripts/nt/python.exe",
    ),
)
def test_canonical_stdlib_venv_members_pass_when_inventoried(tmp_path: Path, relative: str) -> None:
    candidate, receipt = _candidate(tmp_path, (relative,))

    _v3_assert_candidate(candidate, receipt)


@pytest.mark.parametrize(
    "relative",
    (
        "pyvenv.cfg",
        ".venv/pyvenv.cfg",
        "venv/Scripts/python.exe",
    ),
)
def test_created_virtual_environment_paths_fail_even_when_inventoried(tmp_path: Path, relative: str) -> None:
    candidate, receipt = _candidate(tmp_path, (relative,))

    with pytest.raises(ProductVerificationError, match="build/install artifact|forbidden candidate payload path"):
        _v3_assert_candidate(candidate, receipt)


def test_uninventoried_member_inside_canonical_stdlib_venv_fails(tmp_path: Path) -> None:
    candidate, receipt = _candidate(tmp_path, ("runtime/Lib/venv/__init__.py",))
    extra = candidate / "runtime" / "Lib" / "venv" / "created.py"
    extra.write_text("created\n", encoding="utf-8")

    with pytest.raises(ProductVerificationError, match="not present in the payload inventory|unexpected member"):
        _v3_assert_candidate(candidate, receipt)


def test_unexpected_file_fails_closed(tmp_path: Path) -> None:
    candidate, receipt = _candidate(tmp_path)
    (candidate / "runtime" / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")

    with pytest.raises(ProductVerificationError, match="unexpected member not in payload inventory"):
        _v3_assert_candidate(candidate, receipt)


def test_inventoried_hash_mismatch_still_fails_closed(tmp_path: Path) -> None:
    candidate, receipt = _candidate(tmp_path)
    target = candidate / "runtime" / "python.exe"
    target.write_bytes(b"embedded-pythOn")

    with pytest.raises(ProductVerificationError, match="hash/size mismatch"):
        _v3_assert_candidate(candidate, receipt)


def test_inventoried_size_mismatch_still_fails_closed(tmp_path: Path) -> None:
    candidate, receipt = _candidate(tmp_path)
    target = candidate / "runtime" / "python.exe"
    target.write_bytes(target.read_bytes() + b"x")

    with pytest.raises(ProductVerificationError, match="hash/size mismatch"):
        _v3_assert_candidate(candidate, receipt)


@pytest.mark.parametrize(
    "relative",
    (
        "Scripts/python.exe",
        "runtime/Scripts/python.exe",
        "runtime/bin/python.exe",
    ),
)
def test_created_scripts_and_bin_layouts_fail_closed(tmp_path: Path, relative: str) -> None:
    candidate, receipt = _candidate(tmp_path, (relative,))

    with pytest.raises(ProductVerificationError, match="build/install artifact|forbidden candidate payload path"):
        _v3_assert_candidate(candidate, receipt)
