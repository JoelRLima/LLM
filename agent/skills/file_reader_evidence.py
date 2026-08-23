"""Evidence-aware reader operations kept separate from path policy."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.tools.result_completeness import EvidenceProvenance

# The mixin delegates error construction to FileReader's typed facade.
# The concrete facade provides the dict contract at the public boundary.
# mypy: disable-error-code=no-any-return


class FileReaderEvidenceMixin:
    def _read_lines(
        self: Any,
        lines: list[str],
        start_line: int | None,
        end_line: int | None,
        total_lines: int,
        total_chars: int,
        *,
        source_identity: str | None = None,
        source_hash: str | None = None,
    ) -> dict[str, Any]:
        """Leitura de um intervalo especÃ­fico de linhas."""
        if start_line is None:
            start_line = 1
        if end_line is None:
            end_line = total_lines
        if not isinstance(start_line, int) or not isinstance(end_line, int):
            return self._error("parÃ¢metros invÃ¡lidos", "start_line e end_line devem ser nÃºmeros inteiros.")
        if start_line < 1:
            start_line = 1
        if end_line > total_lines:
            end_line = total_lines
        if start_line > end_line:
            return self._error("intervalo invÃ¡lido", "start_line nÃ£o pode ser maior que end_line.")

        content = "".join(lines[start_line - 1:end_line])
        message = f"Linhas {start_line}-{end_line} de {total_lines} lidas com sucesso. Caracteres: {len(content)}/{total_chars}."
        whole_source = start_line == 1 and end_line == total_lines
        result: dict[str, Any] = {
            "evidence_provenance": (
                EvidenceProvenance.EXACT_SOURCE.value
                if whole_source
                else EvidenceProvenance.BOUNDED_SOURCE.value
            ),
            "source_extent": (
                {"kind": "whole"}
                if whole_source
                else {"kind": "lines", "start": start_line, "end": end_line}
            ),
            "ok": True,
            "done": True,
            "data": content,
            "total_lines": total_lines,
            "total_chars": total_chars,
            "complete": True,
            "truncated": False,
            "error": None,
            "message": message,
        }
        if source_identity is not None:
            result["source_identity"] = source_identity
        if source_hash is not None:
            result["source_hash"] = source_hash
        return result

    def _read_with_chunking_and_summary(
        self: Any,
        requested: Path,
        lines: list[str],
        total_lines: int,
        total_chars: int,
        *,
        source_identity: str | None = None,
        source_hash: str | None = None,
    ) -> dict[str, Any]:
        """Le o arquivo em chunks e devolve fonte exata ou resumo derivado."""
        chunk_size = 100
        start = 1
        all_content: list[str] = []
        while start <= total_lines:
            end = min(start + chunk_size - 1, total_lines)
            result = self._read_lines(
                lines,
                start,
                end,
                total_lines,
                total_chars,
                source_identity=source_identity,
                source_hash=source_hash,
            )
            if not result.get("ok"):
                if start == 1:
                    return result
                break
            all_content.append(str(result.get("data", "")))
            start += chunk_size

        full_content = "".join(all_content)
        if total_chars <= self.max_chars:
            result = {
                "ok": True,
                "done": True,
                "data": full_content,
                "total_lines": total_lines,
                "total_chars": total_chars,
                "complete": True,
                "truncated": False,
                "error": None,
                "message": f"Arquivo lido completamente. Linhas: {total_lines}, caracteres: {total_chars}.",
            }
            return _with_source_metadata(result, EvidenceProvenance.EXACT_SOURCE, {"kind": "whole"}, source_identity, source_hash)

        temp_path = self._save_temp_copy(requested, full_content)
        summary = self._generate_summary(requested, lines, total_lines, total_chars, full_content)
        result = {
            "ok": True,
            "done": True,
            "data": summary,
            "total_lines": total_lines,
            "total_chars": total_chars,
            "complete": False,
            "truncated": False,
            "error": None,
            "message": (
                f"Arquivo lido em {len(all_content)} chunk(s). "
                f"Devido ao tamanho ({total_chars} caracteres), um resumo foi gerado. "
                f"O conteÃºdo completo estÃ¡ disponÃ­vel em '{temp_path}'. "
                f"Use file_reader com start_line/end_line para ler trechos especÃ­ficos."
            ),
        }
        return _with_source_metadata(
            result,
            EvidenceProvenance.DERIVED_LOSSY,
            {"kind": "derived_summary"},
            source_identity,
            source_hash,
        )


def _with_source_metadata(
    result: dict[str, Any],
    provenance: EvidenceProvenance,
    extent: dict[str, Any],
    source_identity: str | None,
    source_hash: str | None,
) -> dict[str, Any]:
    result["evidence_provenance"] = provenance.value
    result["source_extent"] = extent
    if source_identity is not None:
        result["source_identity"] = source_identity
    if source_hash is not None:
        result["source_hash"] = source_hash
    return result


__all__ = ["FileReaderEvidenceMixin"]
