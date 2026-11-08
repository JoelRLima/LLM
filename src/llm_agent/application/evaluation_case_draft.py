"""Turn reviewed evidence into an authored, independently checked queue case."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from llm_agent.agent.observability.redaction import redact_text
from llm_agent.application.test_queue_manifest import parse_case, read_document
from llm_agent.filesystem.path_safety import assert_no_link_ancestors


def prepare_case_draft(*, case_id: str, objective: str, checks_path: Path, evidence: Path) -> dict[str, Any]:
    checks = read_document(checks_path)
    if set(checks) != {"checks"} or not checks["checks"]:
        raise ValueError("Forneça pelo menos um critério externo revisado em checks.json.")
    case = parse_case({"id": case_id, "prompt": objective, "files": {}, "checks": checks["checks"]})
    serialized = json.dumps(case.document(), ensure_ascii=False)
    if redact_text(serialized, limit=len(serialized)) != serialized:
        raise ValueError("Remova credenciais do objetivo e dos critérios antes de criar o caso.")
    assert_no_link_ancestors(evidence)
    if evidence.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Evidência excede 64 MiB.")
    content = evidence.read_bytes()
    # Store a digest, never copy prompts, environment, outputs or workspace data
    # from the observed run. The author supplies the objective and oracle.
    source = {"schema_version": 1, "evidence_sha256": hashlib.sha256(content).hexdigest(),
              "expected_result_source": "human_authored_checks", "fixtures": "add_sanitized_seed_before_import"}
    return {"case": case.document(), "source": source, "ready_for_execution": False,
            "review": "Revise objetivo, critérios e seed sanitizado. Nenhum resultado esperado foi inferido do modelo."}


def write_case_draft(directory: Path, preview: dict[str, Any]) -> None:
    assert_no_link_ancestors(directory)
    case = parse_case(preview["case"])
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "task.md").write_text(case.prompt + "\n", encoding="utf-8")
    (directory / "checks.json").write_text(json.dumps({"checks": list(case.checks)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / "source.json").write_text(json.dumps(preview["source"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / "seed").mkdir()
