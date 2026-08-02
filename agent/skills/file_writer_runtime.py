from __future__ import annotations

import difflib
import os
import shutil
from pathlib import Path
from typing import Any, Callable

from agent.approval import ApprovalDecision, ApprovalPort, ApprovalRequest

Result = dict[str, Any]
AstPatcher = Callable[[Path, str, str, str | None], Result]


def prepare_workspace(requested: Path, workspace_path: str) -> tuple[Path | None, Result | None]:
    target = Path(workspace_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if requested.exists() and not target.exists():
        shutil.copy2(requested, target)
    if requested.exists() and (not target.exists() or target.stat().st_size == 0):
        return None, {"ok": False, "done": True, "error": "Falha ao criar cópia no workspace."}
    return target, None


def _approval_denied(decision: ApprovalDecision, *, message: str) -> Result | None:
    if decision is ApprovalDecision.APPROVED:
        return None
    if decision is ApprovalDecision.REQUIRED:
        return {
            "ok": False,
            "done": False,
            "status": "blocked",
            "error": "confirmation_required",
            "message": message,
        }
    return {
        "ok": False,
        "done": False,
        "status": "cancelled",
        "error": "approval_rejected",
        "message": "Modificação rejeitada pelo usuário.",
    }


def confirm_protected_edit(
    file_path: str,
    requested: Path,
    approval: ApprovalPort,
) -> Result | None:
    in_agent = "agent" in str(requested).replace("\\", "/").split("/")
    if not in_agent:
        return None
    decision = approval.request(
        ApprovalRequest(
            action="protected_edit",
            resource=file_path,
            prompt=f"Modificar o arquivo protegido '{file_path}'?",
        )
    )
    return _approval_denied(
        decision,
        message="A modificação do arquivo protegido aguarda confirmação.",
    )


def _write(target: Path, args: dict[str, Any]) -> Result | None:
    target.write_text(str(args.get("content", "")), encoding="utf-8")
    return None


def _append(target: Path, args: dict[str, Any]) -> Result | None:
    with target.open("a", encoding="utf-8") as stream:
        stream.write(str(args.get("content", "")))
    return None


def _patch(target: Path, args: dict[str, Any]) -> Result | None:
    old = args.get("old_content")
    if old is None:
        return {"ok": False, "done": True, "error": "Falta 'old_content' para a ação 'patch'."}
    current = target.read_text(encoding="utf-8")
    if old not in current:
        return {"ok": False, "done": True, "error": "'old_content' não encontrado exatamente no arquivo."}
    target.write_text(current.replace(str(old), str(args.get("new_content", "")), 1), encoding="utf-8")
    return None


def _delete_lines(target: Path, args: dict[str, Any]) -> Result | None:
    start, end = args.get("start_line"), args.get("end_line")
    valid = isinstance(start, int) and not isinstance(start, bool) and isinstance(end, int) and not isinstance(end, bool)
    if not valid:
        return {"ok": False, "done": True, "error": "'start_line' e 'end_line' devem ser inteiros."}
    assert isinstance(start, int) and isinstance(end, int)
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    if start < 1 or end < 1 or start > end:
        return {"ok": False, "done": True, "error": "Intervalo de linhas inválido."}
    if end > len(lines):
        return {"ok": False, "done": True, "error": f"Intervalo {start}-{end} fora do arquivo (total de {len(lines)} linhas)."}
    target.write_text("".join(lines[: start - 1] + lines[end:]), encoding="utf-8")
    return None


def apply_edit(action: str, target: Path, args: dict[str, Any], patcher: AstPatcher) -> Result | None:
    operations: dict[str, Callable[[Path, dict[str, Any]], Result | None]] = {
        "write": _write, "append": _append, "patch": _patch, "delete_lines": _delete_lines,
    }
    if action == "ast_patch":
        symbol, code = args.get("target", ""), args.get("new_code", "")
        if not symbol or not code:
            return {"ok": False, "done": True, "error": "'target' e 'new_code' são obrigatórios para ast_patch."}
        result = patcher(target, str(symbol), str(code), args.get("old_hash"))
        return None if result.get("ok") else result
    operation = operations.get(action)
    if operation is None:
        return {"ok": False, "done": True, "error": f"Ação desconhecida: '{action}'."}
    return operation(target, args)


def _show_diff(original: str, proposed: str, file_path: str) -> None:
    diff = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), proposed.splitlines(keepends=True),
        fromfile=file_path, tofile=f"{file_path} (proposto)",
    ))
    print(f"\n[DIFF] Mudanças propostas para '{file_path}':")
    print(diff if diff.strip() else "Nenhuma mudança detectada.")


def review_and_commit(
    requested: Path,
    workspace: Path,
    file_path: str,
    approval: ApprovalPort,
    invalidate: Callable[[str], None],
) -> Result:
    original = requested.read_text(encoding="utf-8") if requested.exists() else ""
    proposed = workspace.read_text(encoding="utf-8")
    _show_diff(original, proposed, file_path)
    decision = approval.request(
        ApprovalRequest(
            action="apply_change",
            resource=file_path,
            prompt=f"Aplicar mudanças em '{file_path}'?",
        )
    )
    denied = _approval_denied(
        decision,
        message="Mudanças preparadas, mas a escrita aguarda confirmação.",
    )
    if denied is not None:
        return denied
    temporary = Path(str(requested) + ".tmp")
    temporary.write_text(proposed, encoding="utf-8")
    os.replace(temporary, requested)
    invalidate(file_path)
    return {"ok": True, "done": True, "message": f"Mudanças aplicadas em '{file_path}'."}
