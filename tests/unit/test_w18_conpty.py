from __future__ import annotations

import hashlib

import pytest

from scripts.w18_conpty import ConPtyError, _ConPtySession, normalize_terminal_text


def test_normalize_terminal_text_removes_inline_sgr_without_mutating_raw() -> None:
    raw = "Digite \x1b[95m/help \x1b[m\x1b[2mpara comandos.\x1b[22m"

    normalized = normalize_terminal_text(raw)

    assert normalized == "Digite /help para comandos."
    assert raw == "Digite \x1b[95m/help \x1b[m\x1b[2mpara comandos.\x1b[22m"
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == hashlib.sha256(
        "Digite \x1b[95m/help \x1b[m\x1b[2mpara comandos.\x1b[22m".encode("utf-8")
    ).hexdigest()


def test_normalize_terminal_text_removes_osc_and_cursor_controls() -> None:
    raw = "\x1b]0;Digite /help falso\x07Digite \x1b[2J\x1b[10;20H/help\x1b[?25l para comandos."

    normalized = normalize_terminal_text(raw)

    assert normalized == "Digite /help para comandos."
    assert "falso" not in normalized
    assert raw.startswith("\x1b]0;")


def test_normalize_terminal_text_accepts_st_terminated_osc_and_c1_csi() -> None:
    raw = "\x9d0;Digite /help falso\x1b\\Digite \x9b95m/help\x9b0m"

    assert normalize_terminal_text(raw) == "Digite /help"


def test_normalize_terminal_text_accepts_c1_string_terminator() -> None:
    raw = "\x9d0;fake title\x9cDigite /help para comandos."

    assert normalize_terminal_text(raw) == "Digite /help para comandos."


def test_marker_timeout_reports_hashes_without_raw_transcript_text() -> None:
    sentinel = "RAW_SECRET_SENTINEL_DO_NOT_LOG"
    session = object.__new__(_ConPtySession)
    session.timeout_seconds = 0
    session.chunks = [sentinel.encode("utf-8")]
    session.transcript = lambda: sentinel  # type: ignore[method-assign]

    with pytest.raises(ConPtyError) as raised:
        session.wait_for_marker("EXPECTED_MARKER")

    message = str(raised.value)
    assert "EXPECTED_MARKER" in message
    assert sentinel not in message
    assert hashlib.sha256(sentinel.encode("utf-8")).hexdigest() in message
    assert "output_bytes" in message
    assert "normalized_transcript_sha256" in message
