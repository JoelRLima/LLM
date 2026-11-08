"""Read-only execution facts for explicit review of trusted local plugins."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from llm_agent.agent.observability.redaction import redact_text
from llm_agent.extensions.extension_manifest_parser import load_extension_manifest_bytes


def execution_preview(manifest_path: str | None, fingerprint: str | None, workspace: Path) -> dict[str, Any]:
    preview: dict[str, Any] = {
        "trust_model": "trusted_local_process", "sandboxed": False,
        "identity": "current_os_user", "cwd": str(workspace), "command": None,
        "fingerprint_scope": "manifest_only", "review_status": "unavailable",
        "environment": "filtered_allowlist",
        "warning": "O processo terá o acesso do seu usuário a arquivos e rede. O ambiente inicial é filtrado. Grants controlam chamadas do Agent; não confinam o processo.",
    }
    if not manifest_path or not fingerprint:
        return preview
    try:
        path = Path(manifest_path)
        if path.stat().st_size > 1024 * 1024:
            return preview
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != fingerprint:
            preview["review_status"] = "changed"
            return preview
        manifest = load_extension_manifest_bytes(content, mode="strict_catalog")
        # Render one JSON argv rather than a shell command. Redact before UI/export.
        preview.update(command=redact_text(json.dumps(list(manifest.entrypoint), ensure_ascii=False), limit=16000),
                       transport=manifest.transport, review_status="matched")
    except (OSError, ValueError, TypeError):
        pass
    return preview
