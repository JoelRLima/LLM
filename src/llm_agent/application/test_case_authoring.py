"""Create a small, independently checked queue case from a local form."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from llm_agent.agent.observability.redaction import redact_text
from llm_agent.application.test_queue_manifest import parse_case, parse_json, relative_path
from llm_agent.filesystem.path_safety import assert_no_link_ancestors


def _root_and_target(source_root: str | Path, case_id: str) -> tuple[Path, Path]:
    root_input = Path(source_root).expanduser()
    assert_no_link_ancestors(root_input)
    root = root_input.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Escolha uma pasta existente para guardar as tarefas.")
    if not isinstance(case_id, str) or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", case_id) is None:
        raise ValueError("Use de 1 a 64 letras sem acentos, numeros, '_' ou '-'.")
    target = root / case_id
    assert_no_link_ancestors(target)
    if target.exists():
        raise FileExistsError(f"Já existe uma tarefa chamada {case_id}.")
    return root, target


def _build_check(check_kind: str, output_path: str, expected_text: str) -> dict[str, Any]:
    kind = check_kind.strip().casefold() if isinstance(check_kind, str) else ""
    try:
        checked_output_path = relative_path(output_path)
    except ValueError as exc:
        raise ValueError(
            "Informe um caminho relativo ao projeto, sem '..', letra de unidade ou caminho absoluto."
        ) from exc
    check: dict[str, Any] = {"kind": kind, "path": checked_output_path}
    if kind == "text_equals":
        check["expected"] = expected_text
    elif kind == "json_equals":
        try:
            check["expected"] = parse_json(expected_text)
        except ValueError as exc:
            raise ValueError(f"Informe um JSON válido e sem campos duplicados: {exc}") from exc
    elif kind not in {"exists", "absent"}:
        raise ValueError("Escolha existe, ausente, texto exato ou JSON esperado.")
    return check


def _build_files(seed_path: str, seed_content: str) -> dict[str, str]:
    files: dict[str, str] = {}
    if seed_path.strip():
        try:
            checked_seed_path = relative_path(seed_path)
        except ValueError as exc:
            raise ValueError(
                "Informe um caminho inicial relativo, sem '..', letra de unidade ou caminho absoluto."
            ) from exc
        files[checked_seed_path] = seed_content
    elif seed_content:
        raise ValueError("Informe o caminho do arquivo inicial.")
    return files


def _draft_case(case_id: str, objective: str, check: dict[str, Any], files: dict[str, str]) -> Any:
    case = parse_case({"id": case_id, "prompt": objective, "files": files, "checks": [check]})
    serialized = json.dumps(case.document(), ensure_ascii=False, allow_nan=False)
    if redact_text(serialized, limit=len(serialized)) != serialized:
        raise ValueError("Remova credenciais do pedido, dos critérios e do arquivo inicial.")
    return case


def _write_case(root: Path, target: Path, case: Any, case_id: str) -> None:
    temporary = Path(tempfile.mkdtemp(prefix=".workbench-case-", dir=root))
    try:
        (temporary / "task.md").write_text(case.prompt + "\n", encoding="utf-8")
        (temporary / "checks.json").write_text(
            json.dumps({"checks": list(case.checks)}, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        for relative, content in case.files:
            destination = temporary / "seed" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        assert_no_link_ancestors(target)
        if target.exists():
            raise FileExistsError(f"Já existe uma tarefa chamada {case_id}.")
        os.rename(temporary, target)
    except BaseException:
        if temporary.exists() and temporary.parent.resolve() == root:
            shutil.rmtree(temporary)
        raise


def create_task_case(
    source_root: str | Path,
    case_id: str,
    objective: str,
    check_kind: str,
    output_path: str,
    expected_text: str = "",
    seed_path: str = "",
    seed_content: str = "",
) -> dict[str, Any]:
    """Write an authored case into a new child directory without overwriting."""
    root, target = _root_and_target(source_root, case_id)
    check = _build_check(check_kind, output_path, expected_text)
    files = _build_files(seed_path, seed_content)
    case = _draft_case(case_id, objective, check, files)
    _write_case(root, target, case, case_id)
    return {"case_id": case_id, "path": str(target), "checks": len(case.checks), "seed_files": len(case.files)}


__all__ = ["create_task_case"]
