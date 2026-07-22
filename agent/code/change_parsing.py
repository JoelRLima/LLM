"""Parsing and pure text-edit application for ``ChangeSet`` proposals."""

from __future__ import annotations

from typing import Any

from agent.code.change_models import (
    ChangeConflictError,
    ChangeKind,
    ChangeSet,
    ChangeSetError,
    FileChange,
    TextEdit,
    TextEditKind,
)


def _parse_text_edit(data: Any, path: str) -> TextEdit:
    if not isinstance(data, dict):
        raise ChangeSetError(f"Edit de '{path}' deve ser um objeto.")
    try:
        operation = TextEditKind(str(data.get("operation")))
    except ValueError as exc:
        raise ChangeSetError(f"Operação de edit inválida em '{path}'.") from exc
    start_line = data.get("start_line")
    end_line = data.get("end_line")
    if isinstance(start_line, bool) or not isinstance(start_line, int) or start_line < 1:
        raise ChangeSetError(f"Edit de '{path}' exige start_line inteiro positivo.")
    if end_line is not None and (isinstance(end_line, bool) or not isinstance(end_line, int) or end_line < 1):
        raise ChangeSetError(f"end_line inválido em '{path}'.")
    if operation in {TextEditKind.REPLACE, TextEditKind.DELETE}:
        end_line = start_line if end_line is None else end_line
        if end_line < start_line:
            raise ChangeSetError(f"Faixa invertida em edit de '{path}'.")
    content = data.get("content", "")
    if operation != TextEditKind.DELETE and not isinstance(content, str):
        raise ChangeSetError(f"Edit '{operation.value}' exige content em '{path}'.")
    expected = data.get("expected_text")
    if expected is not None and not isinstance(expected, str):
        raise ChangeSetError(f"expected_text deve ser string em '{path}'.")
    return TextEdit(operation, start_line, end_line, "" if operation == TextEditKind.DELETE else str(content), expected)


def _prepare_edit(lines: list[str], edit: TextEdit, path: str) -> tuple[int, int, str]:
    if edit.operation in {TextEditKind.REPLACE, TextEditKind.DELETE}:
        assert edit.end_line is not None
        if edit.end_line > len(lines):
            raise ChangeConflictError(f"Faixa fora do arquivo em '{path}'.")
        left, right = edit.start_line - 1, edit.end_line
        expected_source = "".join(lines[left:right])
    elif edit.operation == TextEditKind.INSERT_BEFORE:
        if edit.start_line > len(lines) + 1:
            raise ChangeConflictError(f"Linha de inserção fora do arquivo em '{path}'.")
        left = right = edit.start_line - 1
        expected_source = "" if edit.start_line > len(lines) else lines[left]
    else:
        if edit.start_line > len(lines):
            raise ChangeConflictError(f"Linha de inserção fora do arquivo em '{path}'.")
        left = right = edit.start_line
        expected_source = lines[edit.start_line - 1]
    if edit.expected_text is not None and expected_source != edit.expected_text:
        raise ChangeConflictError(f"expected_text divergente nas linhas de '{path}'.")
    replacement = "" if edit.operation == TextEditKind.DELETE else edit.content
    return left, right, replacement


def _ensure_non_overlapping(edits: list[tuple[int, int, str]], path: str) -> None:
    for previous, current in zip(edits, edits[1:], strict=False):
        if current[0] < previous[1] or current[0] == previous[0]:
            raise ChangeSetError(f"Edits sobrepostos ou ambíguos em '{path}'.")


def apply_text_edits(source: str, edits: tuple[TextEdit, ...], path: str = "arquivo") -> str:
    if not edits:
        raise ChangeSetError(f"Mudança edit exige ao menos uma operação em '{path}'.")
    lines = source.splitlines(keepends=True)
    prepared = sorted((_prepare_edit(lines, edit, path) for edit in edits), key=lambda item: (item[0], item[1]))
    _ensure_non_overlapping(prepared, path)
    result = list(lines)
    for left, right, replacement in reversed(prepared):
        result[left:right] = replacement.splitlines(keepends=True)
    return "".join(result)


def _file_change_from_dict(raw: Any, seen: set[str]) -> FileChange:
    if not isinstance(raw, dict):
        raise ChangeSetError("Mudança de arquivo deve ser um objeto.")
    path = raw.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ChangeSetError("Mudança sem caminho válido.")
    if path in seen:
        raise ChangeSetError(f"Arquivo repetido no ChangeSet: {path}")
    seen.add(path)
    try:
        kind = ChangeKind(str(raw.get("kind")))
    except ValueError as exc:
        raise ChangeSetError(f"Tipo de mudança inválido em '{path}'.") from exc
    content = raw.get("content")
    if kind in {ChangeKind.CREATE, ChangeKind.MODIFY} and not isinstance(content, str):
        raise ChangeSetError(f"Mudança '{kind.value}' exige conteúdo em '{path}'.")
    raw_edits = raw.get("edits", [])
    if kind == ChangeKind.EDIT and not isinstance(raw_edits, list):
        raise ChangeSetError(f"Mudança 'edit' exige lista edits em '{path}'.")
    edits = tuple(_parse_text_edit(item, path) for item in raw_edits) if kind == ChangeKind.EDIT else ()
    if kind == ChangeKind.EDIT and not edits:
        raise ChangeSetError(f"Mudança 'edit' exige ao menos um edit em '{path}'.")
    destination = raw.get("destination_path")
    if kind == ChangeKind.MOVE and not isinstance(destination, str):
        raise ChangeSetError(f"Mudança 'move' exige destination_path em '{path}'.")
    base_hash = raw.get("base_hash")
    return FileChange(
        path=path,
        kind=kind,
        content=content,
        base_hash=base_hash if isinstance(base_hash, str) else None,
        destination_path=destination,
        edits=edits,
    )


def changeset_from_dict(data: Any, objective: str = "") -> ChangeSet:
    if not isinstance(data, dict):
        raise ChangeSetError("ChangeSet deve ser um objeto.")
    raw_changes = data.get("changes")
    if not isinstance(raw_changes, list) or not raw_changes:
        raise ChangeSetError("ChangeSet deve conter uma lista não vazia de mudanças.")
    seen: set[str] = set()
    changes = tuple(_file_change_from_dict(raw, seen) for raw in raw_changes)
    base_snapshot = data.get("base_snapshot")
    return ChangeSet(
        objective=str(data.get("objective") or objective),
        changes=changes,
        base_snapshot=base_snapshot if isinstance(base_snapshot, str) else None,
        rationale=str(data.get("rationale") or ""),
    )
