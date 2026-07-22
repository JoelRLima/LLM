from __future__ import annotations

from pathlib import Path

from agent.code.contracts import AnalysisLevel, CodeAnalysis


class GenericTextAdapter:
    name = "generic_text"
    extensions: frozenset[str] = frozenset()

    def supports(self, path: Path) -> bool:
        del path
        return True

    def analyze(self, path: Path, relative_path: str, source: str, content_hash: str) -> CodeAnalysis:
        del path, source
        return CodeAnalysis(
            file_path=relative_path,
            language=self.name,
            level=AnalysisLevel.TEXTUAL,
            confidence=0.25,
            content_hash=content_hash,
            limitations=(
                "Não há adapter semântico para esta linguagem; apenas leitura e busca textual são seguras.",
            ),
        )
