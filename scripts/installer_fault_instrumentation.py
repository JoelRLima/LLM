"""Verifier-only helpers for instrumenting real installer transactions.

This module is intentionally not imported by the installer or release
builder.  It creates disposable copies of installer scripts and refuses to
edit them unless the requested source seam is present exactly once.
"""

from __future__ import annotations

from pathlib import Path


class InstrumentationError(ValueError):
    """Raised when a test seam is missing or ambiguous."""


_ANCHORS = {
    "A47": (
        "        # W18_TEST_SEAM_A47_AFTER_LAUNCHER_PROMOTION\n",
        "        # W18_TEST_SEAM_A47_AFTER_LAUNCHER_PROMOTION\n"
        "        # W18_TEST_FAULT_A47\n",
    ),
    "A48": (
        "            # W18_TEST_SEAM_A48_AFTER_PATH_MUTATION\n",
        "            # W18_TEST_SEAM_A48_AFTER_PATH_MUTATION\n"
        "            # W18_TEST_FAULT_A48\n",
    ),
    "RECOVERY": (
        "    Recover-IncompleteJournal $pathsForRecovery\n",
        "    Recover-IncompleteJournal $pathsForRecovery\n"
        "    # W18_TEST_RECOVERY_OBSERVATION\n",
    ),
}

_SEAM_MARKERS = {
    "A47": "W18_TEST_FAULT_A47_REACHED",
    "A48": "W18_TEST_FAULT_A48_REACHED",
}


def _read_utf8_bom(path: Path) -> tuple[str, bool]:
    raw = path.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    return raw[3:].decode("utf-8") if has_bom else raw.decode("utf-8"), has_bom


def instrument_installer(source: Path, destination: Path, seam: str, *, hard_stop: bool = True) -> Path:
    """Copy *source* and inject one deterministic test-only fault.

    ``A47`` stops after launcher promotion, ``A48`` stops after the real PATH
    registry write, and ``RECOVERY`` stops after canonical recovery.  A
    ``throw`` is used for in-process rollback; ``exit`` is used for durable
    crash-recovery tests.
    """

    try:
        anchor_lf, replacement_lf = _ANCHORS[seam.upper()]
    except KeyError as exc:
        raise InstrumentationError(f"unknown W18 instrumentation seam: {seam}") from exc
    text, has_bom = _read_utf8_bom(source)
    newline = "\r\n" if "\r\n" in text else "\n"
    anchor = anchor_lf.replace("\n", newline)
    replacement = replacement_lf.replace("\n", newline)
    count = text.count(anchor)
    if count != 1:
        raise InstrumentationError(f"W18 instrumentation anchor {seam} expected once, found {count}")
    normalized_seam = seam.upper()
    indentation = anchor_lf[: len(anchor_lf) - len(anchor_lf.lstrip())]
    reached_marker = _SEAM_MARKERS.get(normalized_seam)
    marker_line = (
        indentation + f'Write-Output "{reached_marker}"' + "\n"
        if reached_marker is not None
        else ""
    )
    fault = "exit 191" if hard_stop else 'throw "W18_TEST_FAULT_' + normalized_seam + '"'
    destination.parent.mkdir(parents=True, exist_ok=True)
    updated = text.replace(
        anchor,
        replacement + marker_line.replace("\n", newline) + indentation.replace("\n", newline) + fault + newline,
        1,
    )
    destination.write_bytes((b"\xef\xbb\xbf" if has_bom else b"") + updated.encode("utf-8"))
    return destination


def instrumentation_anchor_count(source: Path, seam: str) -> int:
    """Return the exact number of occurrences of a supported source seam."""

    try:
        anchor_lf = _ANCHORS[seam.upper()][0]
    except KeyError as exc:
        raise InstrumentationError(f"unknown W18 instrumentation seam: {seam}") from exc
    text, _ = _read_utf8_bom(source)
    anchor = anchor_lf.replace("\n", "\r\n" if "\r\n" in text else "\n")
    return text.count(anchor)


__all__ = ["InstrumentationError", "instrument_installer", "instrumentation_anchor_count"]
