"""Incremental UTF-8 decoding for bounded workspace query reads."""

from __future__ import annotations

import codecs


def decode_utf8_prefix(payload: bytes, *, truncated: bool) -> str:
    decoder = codecs.getincrementaldecoder("utf-8")()
    return decoder.decode(payload, final=not truncated)
