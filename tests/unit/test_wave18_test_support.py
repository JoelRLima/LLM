from __future__ import annotations

from pathlib import Path

import pytest

from scripts.installer_fault_instrumentation import (
    InstrumentationError,
    instrument_installer,
    instrumentation_anchor_count,
)
from scripts.verify_installed_product import CommandResult, ProductVerificationError, _v3_validate_fault_result


def test_instrumentation_preserves_bom_and_requires_unique_anchor(tmp_path: Path) -> None:
    source = tmp_path / "install.ps1"
    source.write_bytes(b"\xef\xbb\xbfanchor\n")
    with pytest.raises(InstrumentationError, match="expected once"):
        instrument_installer(source, tmp_path / "copy.ps1", "A47")


def test_instrumentation_inserts_deterministic_hard_stop(tmp_path: Path) -> None:
    source = tmp_path / "install.ps1"
    source.write_bytes(b"\xef\xbb\xbf" + b"        # W18_TEST_SEAM_A47_AFTER_LAUNCHER_PROMOTION\n")
    destination = instrument_installer(source, tmp_path / "copy.ps1", "A47")
    raw = destination.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"W18_TEST_FAULT_A47" in raw
    assert b'W18_TEST_FAULT_A47_REACHED' in raw
    assert b"exit 191" in raw


def test_real_installer_has_one_asserted_anchor_per_required_seam() -> None:
    source = Path(__file__).resolve().parents[2] / "installer" / "install.ps1"
    assert instrumentation_anchor_count(source, "A47") == 1
    assert instrumentation_anchor_count(source, "A48") == 1
    assert instrumentation_anchor_count(source, "RECOVERY") == 1


def _fault_result(returncode: int, output: str) -> CommandResult:
    return CommandResult(("powershell",), returncode, output, "")


def test_fault_validator_rejects_generic_exit_before_seam() -> None:
    with pytest.raises(ProductVerificationError, match="code 191"):
        _v3_validate_fault_result(_fault_result(1, "early failure"), "A47", "A47")


def test_fault_validator_rejects_wrong_seam_marker() -> None:
    with pytest.raises(ProductVerificationError, match="exact seam marker"):
        _v3_validate_fault_result(_fault_result(191, "W18_TEST_FAULT_A48_REACHED"), "A47", "A47")


def test_fault_validator_accepts_correct_marker_and_code() -> None:
    evidence = _v3_validate_fault_result(
        _fault_result(191, "W18_TEST_FAULT_A47_REACHED"),
        "A47",
        "A47",
    )

    assert evidence == {
        "fault_exit_code": 191,
        "fault_reached_marker": "W18_TEST_FAULT_A47_REACHED",
    }
