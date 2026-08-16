"""Small plan policy checks kept separate from schema/provenance validation."""

from __future__ import annotations

from typing import Any


def _step_args(step: dict[str, Any]) -> dict[str, Any]:
    args = step.get("args")
    return args if isinstance(args, dict) else {}


def check_analysis_notes(plan: list[dict[str, Any]], blocked: list[Any]) -> None:
    """Block destructive writes to the protected analysis notes file."""

    for index, step in enumerate(plan):
        if not isinstance(step, dict) or step.get("tool") != "file_writer":
            continue
        args = _step_args(step)
        if "analysis_notes.md" not in str(args.get("file_path", "")):
            continue
        action = args.get("action", "write")
        if action == "delete_lines":
            blocked.append(_blocked(index, "Passo apagaria linhas de 'analysis_notes.md'."))
        elif action == "write" and not str(args.get("content") or "").strip():
            blocked.append(_blocked(index, "Passo esvaziaria 'analysis_notes.md'."))


def _blocked(index: int, reason: str) -> Any:
    # Late import avoids the validator/check helper cycle.
    from agent.planning.plan_validator import BlockedStep

    return BlockedStep(index, reason)


def check_patch_without_read(plan: list[dict[str, Any]], warnings: list[str]) -> None:
    """Warn when a patch lacks a preceding read of the same file."""

    read_files: set[Any] = set()
    for index, step in enumerate(plan):
        if not isinstance(step, dict):
            continue
        tool = step.get("tool")
        args = _step_args(step)
        if tool == "file_reader":
            file_path = args.get("file_path")
            if file_path:
                read_files.add(file_path)
        elif tool == "file_writer" and args.get("action") == "patch":
            file_path = args.get("file_path")
            if file_path and file_path not in read_files:
                warnings.append(
                    f"Passo {index + 1}: patch em '{file_path}' sem um file_reader previo desse arquivo no plano."
                )


def check_consecutive_writes(plan: list[dict[str, Any]], warnings: list[str]) -> None:
    """Warn about consecutive writes to the same file."""

    last_write_file = None
    for index, step in enumerate(plan):
        if not isinstance(step, dict) or step.get("tool") != "file_writer":
            last_write_file = None
            continue
        file_path = _step_args(step).get("file_path")
        if file_path and file_path == last_write_file:
            warnings.append(
                f"Passo {index + 1}: escrita consecutiva em '{file_path}' (mesmo arquivo do passo imediatamente anterior)."
            )
        last_write_file = file_path


def check_inverted_dependencies(plan: list[dict[str, Any]], blocked: list[Any]) -> None:
    """Block reads that precede the writer which creates their target."""

    producers: dict[str, int] = {}
    for index, step in enumerate(plan):
        if not isinstance(step, dict) or step.get("tool") != "file_writer":
            continue
        file_path = _step_args(step).get("file_path")
        if file_path and file_path not in producers:
            producers[file_path] = index
    for index, step in enumerate(plan):
        if not isinstance(step, dict) or step.get("tool") not in {"file_reader", "code_analyzer"}:
            continue
        args = _step_args(step)
        file_path = args.get("file_path") or args.get("target")
        if file_path in producers and producers[file_path] > index:
            blocked.append(
                _blocked(
                    index,
                    f"Dependencia invertida: passo le/analisa '{file_path}' antes do passo {producers[file_path] + 1}, que e quem o cria.",
                )
            )


__all__ = [
    "check_analysis_notes",
    "check_consecutive_writes",
    "check_inverted_dependencies",
    "check_patch_without_read",
]
