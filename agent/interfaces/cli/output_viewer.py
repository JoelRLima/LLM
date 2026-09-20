"""Terminal-only adapter for bounded retained-output reads."""

from __future__ import annotations

import re
from typing import Any

from agent.outputs.models import OUTPUT_ID_PATTERN, OutputError

_USAGE = "Uso: /inspect output <output-id> [offset]"
_OFFSET = re.compile(r"[0-9]+")


def _emit(ctx: Any, value: object) -> None:
    shell = getattr(ctx, "shell", None)
    if shell is not None:
        shell.print_background(value)
        return
    from agent.interfaces.cli.ui import console

    console.print(value, markup=False)


def _parse(text: str) -> tuple[str, int] | None:
    tokens = text.strip().split()
    if len(tokens) < 2 or tokens[0].casefold() != "/inspect" or tokens[1].casefold() != "output":
        return None
    if len(tokens) not in {3, 4}:
        raise ValueError(_USAGE)
    output_id = tokens[2]
    if OUTPUT_ID_PATTERN.fullmatch(output_id) is None:
        raise ValueError(_USAGE)
    offset = 0
    if len(tokens) == 4:
        if _OFFSET.fullmatch(tokens[3]) is None:
            raise ValueError(_USAGE)
        try:
            offset = int(tokens[3], 10)
        except (TypeError, ValueError) as exc:
            raise ValueError(_USAGE) from exc
    return output_id, offset


def render_output_viewer(text: str, ctx: Any) -> bool:
    """Handle the output subgrammar; return False for historical inspect syntax."""

    try:
        parsed = _parse(text)
    except ValueError as exc:
        _emit(ctx, str(exc))
        return True
    if parsed is None:
        return False
    output_id, offset = parsed
    application = getattr(ctx, "application", None)
    accessor = getattr(application, "output_service", None)
    if not callable(accessor):
        _emit(ctx, "OutputService indisponível.")
        return True
    try:
        service = accessor()
        artifact = service.metadata(output_id)
        chunk = service.read_chunk(output_id, offset=offset, limit=16_384)
    except OutputError as exc:
        _emit(ctx, f"Saída indisponível [{exc.reason_code}].")
        return True
    _emit(ctx, f"Output {artifact.output_id}: {artifact.title}")
    _emit(ctx, chunk.text)
    if chunk.next_offset is not None:
        _emit(ctx, f"Próximo trecho: /inspect output {artifact.output_id} {chunk.next_offset}")
    return True


__all__ = ["render_output_viewer"]
