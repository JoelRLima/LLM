"""Pure helpers for compact conversation views and repository file hints."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List


def build_compact_view(
    messages: Sequence[Mapping[str, Any]],
    tool_history: Sequence[Mapping[str, Any]],
    memory_state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    compact: List[Dict[str, Any]] = []
    summaries = memory_state.get("file_summaries", {})
    summarized_files = {
        entry.get("args", {}).get("file_path", "")
        for entry in tool_history
        if entry.get("tool") == "file_reader" and entry.get("result", {}).get("ok")
    }
    for message in messages:
        file_path = next(
            (path for path in summarized_files if path and len(message.get("content", "")) > 500 and summaries.get(path)),
            None,
        )
        if message.get("role") == "system" or file_path is None:
            compact.append(dict(message))
            continue
        replacement = dict(message)
        replacement["content"] = f"[Resumo de '{file_path}']: {summaries[file_path]}"
        compact.append(replacement)
    return compact


def discover_project_context(root: str) -> str:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--cached", "--exclude-standard"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=root,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and result.returncode == 0 and result.stdout.strip():
        files = result.stdout.strip().splitlines()[:50]
        file_list = "\n".join(f"  {filename}" for filename in files)
        return f"\n\n--- CONTEXTO DO PROJETO ---\nArquivos rastreados pelo Git ({len(files)} arquivos):\n{file_list}\n"
    try:
        entries = [
            f"  {item}{'/' if os.path.isdir(os.path.join(root, item)) else ''}"
            for item in sorted(os.listdir(root))
            if not item.startswith(".") and item != "__pycache__"
        ]
    except OSError:
        return ""
    return "\n\n--- CONTEXTO DO PROJETO ---\nEstrutura raiz:\n" + "\n".join(entries[:40]) + "\n"


def compress_conversation(session: Any, context_limit: int, verbose: bool) -> None:
    estimated = sum(len(str(message.get("content", ""))) for message in session.messages) // 4
    threshold = int(context_limit * 0.8)
    if estimated <= threshold:
        return
    prompt = (
        "Resuma a conversa abaixo mantendo objetivo, progresso, descobertas e próximas ações.\n\n"
        + "\n".join(
            f"[{message['role']}] {message['content']}" for message in session.messages[-20:]
        )
    )
    original_system = session.messages[0]["content"] if session.messages else ""
    temporary = type(session)("", session.config)
    temporary.set_system_prompt("Resuma o histórico de forma concisa e técnica.")
    temporary.add_user_message(prompt)
    payload = temporary.build_payload()
    payload.update({"max_tokens": 1024, "stream": False})
    try:
        response = session.send_non_streaming_request(payload)
    except Exception:
        return
    if not isinstance(response, str) or not response.strip():
        return
    summary = response.strip()
    session.messages = [{"role": "system", "content": original_system}]
    session.add_message("system", f"[RESUMO DO CONTEXTO]: {summary}")
    if verbose:
        print(f"✅ [COMPRESS] Contexto comprimido para ~{len(summary) // 4} tokens.")


def _line_hint(filename: str, semantic: bool = False) -> str | None:
    path = os.path.join(os.getcwd(), filename)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            line_count = sum(1 for _ in handle)
    except OSError:
        return None
    suffix = " [semântico]" if semantic else ""
    return f"{filename} ({line_count} linhas){suffix}"


def get_file_hints(objective: str, semantic_memory: Any) -> str:
    candidates = re.findall(r"\b[\w\-.]+\.(?:py|md|txt|json|yaml|yml|toml|cfg)\b", objective)
    hints: list[str] = []
    seen: set[str] = set()
    for filename in candidates:
        if filename not in seen and (hint := _line_hint(filename)):
            seen.add(filename)
            hints.append(hint)
    try:
        semantic_files = semantic_memory.find_similar_files(objective, top_k=5)
    except Exception:
        semantic_files = []
    for filename in semantic_files:
        if filename not in seen and (hint := _line_hint(filename, semantic=True)):
            seen.add(filename)
            hints.append(hint)
    return "\n".join(f"- {hint}" for hint in hints)
