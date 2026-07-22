"""Parser determinístico dos comandos explícitos ``/code``."""

from __future__ import annotations

import shlex
from dataclasses import dataclass


class CodeCommandError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedCodeCommand:
    action: str
    objective: str = ""
    targets: tuple[str, ...] = ()
    include_tests: bool = False
    assume_yes: bool = False
    template: str | None = None


CODE_COMMAND_HELP = """Uso:
  /code analyze [arquivo]
  /code review <arquivo...>
  /code generate [targets...] -- <objetivo>
  /code modify <targets...> -- <objetivo>
  /code repair <targets...> -- <objetivo>
  /code refactor <targets...> -- <objetivo>
  /code template parallel_analyze <arquivo...>
  /code template parallel_review <arquivo...>
  /code template analyze_then_modify <arquivo...> -- <objetivo>

Flags: --tests executa testes descobertos; --yes aprova propostas de baixa confiança.
Use aspas para objetivos ou caminhos com espaços."""


def _tokenize(text: str) -> list[str]:
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise CodeCommandError(f"Comando /code inválido: {exc}") from exc
    if not tokens or tokens[0].casefold() != "/code":
        raise CodeCommandError("Comando deve começar com /code.")
    return tokens


def _partition_arguments(raw: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in raw:
        return raw, []
    separator = raw.index("--")
    return raw[:separator], raw[separator + 1 :]


def _parse_template(
    before: list[str],
    after: list[str],
    include_tests: bool,
    assume_yes: bool,
) -> ParsedCodeCommand:
    if not before:
        raise CodeCommandError("/code template exige nome e targets.")
    template, targets = before[0].replace("-", "_"), tuple(before[1:])
    if not targets:
        raise CodeCommandError("Template exige ao menos um target.")
    objective = " ".join(after).strip()
    if template == "analyze_then_modify" and not objective:
        raise CodeCommandError("analyze_then_modify exige objetivo após --.")
    return ParsedCodeCommand(
        "template",
        objective=objective,
        targets=targets,
        include_tests=include_tests,
        assume_yes=assume_yes,
        template=template,
    )


def parse_code_command(text: str) -> ParsedCodeCommand:
    tokens = _tokenize(text)
    if len(tokens) == 1 or tokens[1].casefold() in {"help", "ajuda"}:
        return ParsedCodeCommand("help")

    action = tokens[1].casefold().replace("-", "_")
    allowed = {"analyze", "review", "generate", "modify", "repair", "refactor", "template"}
    if action not in allowed:
        raise CodeCommandError(f"Ação desconhecida: {action}.\n{CODE_COMMAND_HELP}")

    raw = tokens[2:]
    include_tests = "--tests" in raw
    assume_yes = "--yes" in raw
    raw = [token for token in raw if token not in {"--tests", "--yes"}]

    before, after = _partition_arguments(raw)

    if action == "analyze":
        if len(before) > 1 or after:
            raise CodeCommandError("/code analyze aceita zero ou um arquivo.")
        return ParsedCodeCommand(action, targets=tuple(before))
    if action == "review":
        if not before or after:
            raise CodeCommandError("/code review exige um ou mais arquivos.")
        return ParsedCodeCommand(action, targets=tuple(before))
    if action == "template":
        return _parse_template(before, after, include_tests, assume_yes)

    objective = " ".join(after).strip()
    if not objective:
        raise CodeCommandError(f"/code {action} exige objetivo após --.")
    if action in {"modify", "repair", "refactor"} and not before:
        raise CodeCommandError(f"/code {action} exige ao menos um target.")
    return ParsedCodeCommand(
        action,
        objective=objective,
        targets=tuple(before),
        include_tests=include_tests,
        assume_yes=assume_yes,
    )
