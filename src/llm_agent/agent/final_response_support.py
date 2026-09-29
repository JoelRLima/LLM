"""Supporting projections for final-response construction."""

from __future__ import annotations

import re
from typing import Any


def unread_file_warning(answer: str, history: Any) -> str:
    mentioned_files = set(
        re.findall(r"(?<!\w)[\w\-/]+\.(?:py|json|yaml|yml|md|txt|toml|cfg)(?!\w)", answer)
    )
    read_files = {
        file_path
        for entry in history
        if (
            file_path := entry.get("args", {}).get("file_path")
            or entry.get("args", {}).get("target", "")
        )
    }
    unread = mentioned_files - read_files
    had_reads = any(entry.get("tool") in ("file_reader", "code_analyzer") for entry in history)
    if not unread or not had_reads:
        return ""
    return (
        "\n\n[⚠️ Aviso: esta análise menciona arquivos que não foram lidos durante a execução: "
        + ", ".join(sorted(unread))
        + ". As sugestões relacionadas a esses arquivos podem ser imprecisas.]"
    )


__all__ = ["unread_file_warning"]
