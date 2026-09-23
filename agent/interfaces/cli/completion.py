"""Pure parser-derived native CLI completion."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable
from typing import TypedDict

from agent.interfaces.cli.parser import build_parser

MAX_COMPLETION_ITEMS = 64
_PATH_METAVARS = frozenset({"dir", "arquivo", "source"})


class _ParserNode(TypedDict):
    path: list[str]
    children: list[str]
    options: list[str]
    value_options: list[str]
    path_options: list[str]


def _parser_projection(
    parser: argparse.ArgumentParser,
    *,
    engineering_operation_ids: Iterable[str] = (),
) -> tuple[_ParserNode, ...]:
    """Project the canonical argparse tree into deterministic completion data."""

    nodes: dict[tuple[str, ...], _ParserNode] = {}

    def visit(current: argparse.ArgumentParser, path: tuple[str, ...]) -> None:
        options: set[str] = set()
        value_options: set[str] = set()
        path_options: set[str] = set()
        for action in getattr(current, "_actions", ()):
            option_strings = tuple(
                option
                for option in getattr(action, "option_strings", ())
                if isinstance(option, str)
            )
            options.update(option_strings)
            if getattr(action, "nargs", None) != 0:
                value_options.update(option_strings)
            metavar = getattr(action, "metavar", None)
            if isinstance(metavar, str) and metavar.casefold() in _PATH_METAVARS:
                path_options.update(option_strings)

        children: set[str] = set()
        subparsers = next(
            (
                action
                for action in getattr(current, "_actions", ())
                if isinstance(action, argparse._SubParsersAction)
            ),
            None,
        )
        if subparsers is not None:
            children.update(
                token
                for token in subparsers.choices
                if isinstance(token, str)
            )

        nodes[path] = {
            "path": list(path),
            "children": sorted(children),
            "options": sorted(options),
            "value_options": sorted(value_options),
            "path_options": sorted(path_options),
        }

        if subparsers is not None:
            for token, child in sorted(subparsers.choices.items()):
                if isinstance(token, str) and isinstance(child, argparse.ArgumentParser):
                    visit(child, (*path, token))

    visit(parser, ())
    operation_ids = sorted(
        value for value in engineering_operation_ids if isinstance(value, str) and value
    )
    run_node = nodes.get(("test", "run"))
    if run_node is not None:
        children = set(run_node["children"])
        children.update(operation_ids)
        run_node["children"] = sorted(children)
    return tuple(nodes[path] for path in sorted(nodes))


def _node_for(
    nodes: tuple[_ParserNode, ...],
    path: tuple[str, ...],
) -> _ParserNode | None:
    return next(
        (node for node in nodes if tuple(node["path"]) == path),
        None,
    )


def _command_context(
    tokens: Iterable[str],
    nodes: tuple[_ParserNode, ...],
) -> tuple[tuple[str, ...], _ParserNode | None]:
    path: list[str] = []
    values = tuple(tokens)
    index = 0
    while index < len(values):
        node = _node_for(nodes, tuple(path))
        if node is None:
            break
        token = values[index]
        if token == "--":
            break
        if token.startswith("-"):
            if token in node["value_options"] and index + 1 < len(values):
                index += 2
            else:
                index += 1
            continue
        if token in node["children"]:
            path.append(token)
            index += 1
            continue
        break
    return tuple(path), _node_for(nodes, tuple(path))


def _parser_metadata(parser: argparse.ArgumentParser) -> tuple[tuple[str, ...], frozenset[str]]:
    nodes = _parser_projection(parser)
    values: set[str] = set()
    path_options: set[str] = set()
    for node in nodes:
        path = tuple(node["path"])
        children = tuple(node["children"])
        options = tuple(node["options"])
        values.update(" ".join((*path, child)) for child in children)
        values.update(" ".join((*path, option)) for option in options)
        path_options.update(node["path_options"])
        if path and not children:
            values.add(" ".join(path))
    return tuple(sorted(values)), frozenset(path_options)


def _dynamic_engineering_paths(operation_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            f"test run {value}"
            for value in operation_ids
            if isinstance(value, str) and value
        )
    )


def completion_commands(*, engineering_operation_ids: Iterable[str] = ()) -> tuple[str, ...]:
    parser_values, _ = _parser_metadata(build_parser())
    return tuple(
        sorted(set((*parser_values, *_dynamic_engineering_paths(engineering_operation_ids))))
    )


def _completion_parts(text: str) -> tuple[tuple[str, ...], str]:
    trailing_space = bool(text) and text[-1].isspace()
    tokens = tuple(text.split())
    if trailing_space:
        return tokens, ""
    return tokens[:-1], tokens[-1] if tokens else ""


def complete_command(
    text: str,
    *,
    engineering_operation_ids: Iterable[str] = (),
    filesystem: Callable[[str], Iterable[str]] | None = None,
) -> tuple[str, ...]:
    """Return replacements for only the current token in a CLI prefix."""

    if not isinstance(text, str):
        return ()
    nodes = _parser_projection(
        build_parser(),
        engineering_operation_ids=engineering_operation_ids,
    )
    prior, prefix = _completion_parts(text)
    _path, node = _command_context(prior, nodes)
    if node is None:
        return ()

    path_options = frozenset(node["path_options"])
    value_options = frozenset(node["value_options"])
    if prefix in path_options:
        if filesystem is None:
            return ()
        return tuple(filesystem(prefix))[:MAX_COMPLETION_ITEMS]
    if prior and prior[-1] in path_options:
        if filesystem is None:
            return ()
        return tuple(filesystem(prior[-1]))[:MAX_COMPLETION_ITEMS]
    if prior and prior[-1] in value_options:
        return ()

    values: tuple[str, ...] = tuple(node["options"] if prefix.startswith("-") else node["children"])
    return tuple(value for value in values if value.startswith(prefix))[:MAX_COMPLETION_ITEMS]


def _powershell_projection(*, engineering_operation_ids: Iterable[str]) -> dict[str, object]:
    return {
        "nodes": list(
            _parser_projection(
                build_parser(),
                engineering_operation_ids=engineering_operation_ids,
            )
        )
    }


def powershell_completion_script(*, engineering_operation_ids: Iterable[str] = ()) -> str:
    """Generate a deterministic native PowerShell completer."""

    projection = json.dumps(
        _powershell_projection(engineering_operation_ids=engineering_operation_ids),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).replace("'", "''")
    return (
        "$projectionJson = '" + projection + "'\n"
        "$projection = $projectionJson | ConvertFrom-Json\n"
        "Register-ArgumentCompleter -Native -CommandName 'llm-agent' -ScriptBlock {\n"
        "    param($wordToComplete, $commandAst, $cursorPosition)\n"
        "    $elements = @($commandAst.CommandElements | Where-Object { $_.Extent.StartOffset -lt $cursorPosition } | ForEach-Object { [string]$_.Extent.Text })\n"
        "    if ($elements.Count -gt 0 -and $elements[0] -eq 'llm-agent') { $elements = @($elements | Select-Object -Skip 1) }\n"
        "    $line = [string]$commandAst.Extent.Text\n"
        "    $cursor = [Math]::Min([int]$cursorPosition, $line.Length)\n"
        "    $trailingSpace = $cursor -gt 0 -and $line.Substring(0, $cursor) -match '\\s$'\n"
        "    if ($trailingSpace) { $prior = @($elements); $prefix = '' }\n"
        "    elseif ($elements.Count -gt 0) { if ($elements.Count -eq 1) { $prior = @() } else { $prior = @($elements[0..($elements.Count - 2)]) }; $prefix = [string]$wordToComplete }\n"
        "    else { $prior = @(); $prefix = [string]$wordToComplete }\n"
        "    $commandPath = New-Object System.Collections.Generic.List[string]\n"
        "    $node = $projection.nodes | Where-Object { (@($_.path) -join ' ') -eq '' } | Select-Object -First 1\n"
        "    $index = 0\n"
        "    while ($index -lt $prior.Count -and $null -ne $node) {\n"
        "        $token = [string]$prior[$index]\n"
        "        if ($token -eq '--') { break }\n"
        "        if ($token.StartsWith('-')) {\n"
        "            if (@($node.value_options) -contains $token -and $index + 1 -lt $prior.Count) { $index += 2 } else { $index++ }\n"
        "            continue\n"
        "        }\n"
        "        if (@($node.children) -contains $token) {\n"
        "            [void]$commandPath.Add($token)\n"
        "            $key = @($commandPath) -join ' '\n"
        "            $node = $projection.nodes | Where-Object { (@($_.path) -join ' ') -eq $key } | Select-Object -First 1\n"
        "            $index++\n"
        "            continue\n"
        "        }\n"
        "        break\n"
        "    }\n"
        "    if ($null -eq $node) { return }\n"
        "    $pathOptions = @($node.path_options)\n"
        "    $valueOptions = @($node.value_options)\n"
        "    $pathOption = $null\n"
        "    if ($pathOptions -contains $prefix) { $pathOption = $prefix }\n"
        "    elseif ($prior.Count -gt 0 -and $pathOptions -contains [string]$prior[-1]) { $pathOption = [string]$prior[-1] }\n"
        "    if ($null -ne $pathOption) {\n"
        "        $pathPrefix = if ($pathOption -eq $prefix) { '' } else { [string]$wordToComplete }\n"
        "        $search = if ([string]::IsNullOrEmpty($pathPrefix)) { '*' } else { $pathPrefix + '*' }\n"
        "        Get-ChildItem -Path $search -Force -ErrorAction SilentlyContinue | Sort-Object -Property FullName | ForEach-Object { [System.Management.Automation.CompletionResult]::new($_.FullName, $_.Name, 'ParameterValue', $_.FullName) }\n"
        "        return\n"
        "    }\n"
        "    if ($prior.Count -gt 0 -and $valueOptions -contains [string]$prior[-1]) { return }\n"
        "    if ($prefix.StartsWith('-')) { $candidates = @($node.options) } else { $candidates = @($node.children) }\n"
        "    $candidates | Where-Object { [string]$_ -like ([string]$prefix + '*') } | ForEach-Object { [System.Management.Automation.CompletionResult]::new([string]$_, [string]$_, 'Command', [string]$_) }\n"
        "}\n"
    )


__all__ = ["complete_command", "completion_commands", "powershell_completion_script"]
