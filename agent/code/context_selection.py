"""Seleção determinística de contexto por arquivos, símbolos e imports."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Sequence

from agent.code.contracts import CodeAnalysis, RepositoryIndex
from agent.code.intelligence import CodeIntelligenceService

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STOPWORDS = frozenset(
    {
        "adicionar",
        "alterar",
        "arquivo",
        "codigo",
        "com",
        "corrigir",
        "criar",
        "este",
        "para",
        "preservar",
        "que",
        "sem",
        "the",
        "this",
        "with",
    }
)


@dataclass(frozen=True)
class SelectedFile:
    path: str
    score: int
    reasons: tuple[str, ...]
    content_hash: str


@dataclass(frozen=True)
class SelectedContext:
    text: str
    files: tuple[SelectedFile, ...]
    truncated: bool = False


class ContextSelector:
    def __init__(
        self,
        root: str | Path,
        intelligence: CodeIntelligenceService,
        max_files: int = 6,
    ) -> None:
        self.root = Path(root).resolve()
        self.intelligence = intelligence
        self.max_files = max(1, max_files)

    @staticmethod
    def _terms(objective: str) -> frozenset[str]:
        return frozenset(
            term.casefold()
            for term in _IDENTIFIER.findall(objective)
            if term.casefold() not in _STOPWORDS
        )

    @staticmethod
    def _module_candidates(target: str) -> tuple[str, ...]:
        normalized = target.replace(".", "/")
        return (f"{normalized}.py", f"{normalized}/__init__.py")

    @staticmethod
    def _normalize_target(target: str) -> str:
        normalized = target.replace("\\", "/").rstrip("/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized or "."

    def _score(
        self,
        objective: str,
        explicit_targets: Sequence[str],
        index: RepositoryIndex,
    ) -> tuple[Dict[str, int], Dict[str, set[str]]]:
        terms = self._terms(objective)
        scores: Dict[str, int] = {}
        reasons: Dict[str, set[str]] = {}

        for target in explicit_targets:
            normalized_target = self._normalize_target(target)
            self._add_score(scores, reasons, normalized_target, 100, "target explícito")

        by_path = {analysis.file_path: analysis for analysis in index.analyses}
        self._score_containment(explicit_targets, by_path, scores, reasons)
        self._score_mentions(terms, by_path, scores, reasons)
        self._score_imports(explicit_targets, by_path, scores, reasons)
        return scores, reasons

    @staticmethod
    def _add_score(
        scores: Dict[str, int],
        reasons: Dict[str, set[str]],
        path: str,
        score: int,
        reason: str,
    ) -> None:
        scores[path] = scores.get(path, 0) + score
        reasons.setdefault(path, set()).add(reason)

    def _score_containment(
        self,
        explicit_targets: Sequence[str],
        by_path: Dict[str, CodeAnalysis],
        scores: Dict[str, int],
        reasons: Dict[str, set[str]],
    ) -> None:
        for target in explicit_targets:
            normalized_target = self._normalize_target(target)
            for path in by_path:
                if normalized_target == "." or path.startswith(normalized_target + "/"):
                    self._add_score(scores, reasons, path, 80, f"contido no target {normalized_target}")

    def _score_mentions(
        self,
        terms: frozenset[str],
        by_path: Dict[str, CodeAnalysis],
        scores: Dict[str, int],
        reasons: Dict[str, set[str]],
    ) -> None:
        for path, analysis in by_path.items():
            file_terms = {
                Path(path).name.casefold(),
                Path(path).stem.casefold(),
                path.casefold(),
            }
            matched_file_terms = sorted(
                term for term in terms if any(term in value for value in file_terms)
            )
            if matched_file_terms:
                self._add_score(scores, reasons, path, 40 + 5 * len(matched_file_terms), "nome mencionado no objetivo")
            symbol_matches = sorted(
                symbol.name
                for symbol in analysis.symbols
                if symbol.name.casefold() in terms
                or symbol.qualified_name.casefold() in terms
            )
            if symbol_matches:
                self._add_score(scores, reasons, path, 50 + 5 * len(symbol_matches), "símbolo mencionado")

    def _score_imports(
        self,
        explicit_targets: Sequence[str],
        by_path: Dict[str, CodeAnalysis],
        scores: Dict[str, int],
        reasons: Dict[str, set[str]],
    ) -> None:
        for target in explicit_targets:
            target_analysis = by_path.get(self._normalize_target(target))
            if target_analysis is None:
                continue
            for edge in target_analysis.imports:
                for candidate in self._module_candidates(edge.target):
                    matched = next(
                        (path for path in by_path if path.endswith(candidate)),
                        None,
                    )
                    if matched:
                        self._add_score(scores, reasons, matched, 25, f"importado por {target_analysis.file_path}")

    def _read_excerpt(
        self,
        path: str,
        analysis: CodeAnalysis,
        terms: frozenset[str],
        max_chars: int,
    ) -> tuple[str, bool]:
        # read_bytes preserva CRLF/LF exatamente como o ChangeSetTransaction.
        # Assim, expected_text produzido a partir deste contexto também é uma
        # precondição válida no Windows.
        source = (self.root / path).read_bytes().decode("utf-8", errors="replace")
        if len(source) <= max_chars:
            return source, False
        lines = source.splitlines(keepends=True)
        ranges: list[tuple[int, int]] = [(0, min(len(lines), 40))]
        for symbol in analysis.symbols:
            if symbol.name.casefold() in terms or symbol.qualified_name.casefold() in terms:
                ranges.append((max(0, symbol.start_line - 4), min(len(lines), symbol.end_line + 3)))
        selected: list[str] = []
        seen: set[int] = set()
        for start, end in ranges:
            for index in range(start, end):
                if index in seen:
                    continue
                seen.add(index)
                selected.append(f"{index + 1:>5}: {lines[index]}")
                if sum(len(item) for item in selected) >= max_chars:
                    return "".join(selected)[:max_chars], True
        return "".join(selected)[:max_chars], True

    def select(
        self,
        objective: str,
        explicit_targets: Sequence[str] = (),
        max_chars: int = 12_000,
    ) -> SelectedContext:
        index = self.intelligence.index_repository()
        scores, reasons = self._score(objective, explicit_targets, index)
        by_path = {analysis.file_path: analysis for analysis in index.analyses}
        ranked = sorted(scores, key=lambda path: (-scores[path], path))[: self.max_files]
        terms = self._terms(objective)
        chunks: list[str] = []
        selected_files: list[SelectedFile] = []
        truncated = False
        remaining = max(1000, max_chars)
        for position, path in enumerate(ranked):
            analysis = by_path.get(path)
            candidate = (self.root / path).resolve()
            if analysis is None or not candidate.is_file():
                continue
            per_file = max(800, remaining // max(1, len(ranked) - position))
            excerpt, file_truncated = self._read_excerpt(path, analysis, terms, per_file)
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            reason_values = tuple(sorted(reasons.get(path, {"seleção determinística"})))
            header = (
                f"\n--- {path} ---\n"
                f"sha256: {digest}\n"
                f"seleção: {', '.join(reason_values)}\n"
            )
            chunk = header + excerpt
            if len(chunk) > remaining:
                chunk = chunk[:remaining]
                file_truncated = True
            chunks.append(chunk)
            remaining -= len(chunk)
            truncated = truncated or file_truncated
            selected_files.append(
                SelectedFile(path, scores[path], reason_values, digest)
            )
            if remaining <= 0:
                truncated = True
                break
        return SelectedContext("".join(chunks), tuple(selected_files), truncated)
