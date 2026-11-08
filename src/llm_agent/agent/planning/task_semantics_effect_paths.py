"""Bounded extraction and role classification for file paths."""

from __future__ import annotations

from collections.abc import Sequence

from llm_agent.agent.planning.task_semantics_effect_data import (
    _EFFECT_TERMS,
    _FILE_RE,
    _MUTATION_VERBS,
    _OUTPUT_ONLY_NOUNS,
    _OUTPUT_VERBS,
    _PERSISTENCE_VERBS,
    _RESPONSE_VERBS,
    _WORD_RE,
    PathRole,
    _PathMention,
)
from llm_agent.agent.planning.task_semantics_effect_tokens import (
    _repair_mojibake,
)


def _path_mentions(clause: str) -> tuple[tuple[str, int], ...]:
    """Return concrete paths and their bounded token positions."""

    repaired = _repair_mojibake(clause)
    word_matches = list(_WORD_RE.finditer(repaired))
    mentions: list[tuple[str, int]] = []
    for match in _FILE_RE.finditer(repaired):
        value = match.group(1).strip(".,;:()[]{}")
        if not value:
            continue
        token_index = next(
            (
                index
                for index, word_match in enumerate(word_matches)
                if word_match.start() < match.end() and word_match.end() > match.start()
            ),
            len(word_matches),
        )
        mentions.append((value, token_index))
    return tuple(mentions)


def _between(tokens: Sequence[str], left: int, right: int) -> tuple[str, ...]:
    return tuple(tokens[left + 1 : right]) if right > left else ()


def _destination_relation(tokens: Sequence[str], effect_index: int, path_index: int) -> bool:
    between = _between(tokens, effect_index, path_index)
    # These are relation markers in an explicit destination construction.  A
    # role classifier, rather than a growing exception list, owns their use.
    return bool(set(between) & {"em", "no", "na", "nos", "nas", "to", "into", "in"})


def _source_relation(tokens: Sequence[str], effect_index: int, path_index: int) -> bool:
    return bool(
        set(_between(tokens, effect_index, path_index))
        & {"about", "of", "de", "do", "da", "dos", "das", "sobre", "from", "using", "with"}
    )


def _response_context(tokens: Sequence[str], effect_index: int, path_index: int) -> bool:
    between = _between(tokens, effect_index, path_index)
    return bool(set(between) & (_OUTPUT_ONLY_NOUNS | _RESPONSE_VERBS))


def _conditional_symbolic_target(tokens: Sequence[str], effect_index: int) -> str | None:
    """Return a bounded symbolic branch target without treating it as a path."""

    if not any(token in {"se", "if", "caso", "otherwise"} for token in tokens[:effect_index]):
        return None
    candidate = tokens[effect_index + 1] if effect_index + 1 < len(tokens) else ""
    if (
        candidate
        in {
            "a",
            "o",
            "as",
            "os",
            "um",
            "uma",
            "an",
            "the",
            "texto",
            "text",
            "exatamente",
            "exactly",
        }
        or candidate in _OUTPUT_ONLY_NOUNS
    ):
        return None
    return candidate or None


def _classify_path_roles(clause: str, tokens: Sequence[str]) -> tuple[_PathMention, ...]:
    """Classify paths before creating any durable effect intent."""

    raw_mentions = _path_mentions(clause)
    if not raw_mentions:
        return ()
    mutation_positions, output_positions, response_positions = _effect_positions(tokens)
    return tuple(
        _classify_path_mention(
            value, path_index, raw_mentions, tokens, mutation_positions, output_positions, response_positions
        )
        for value, path_index in raw_mentions
    )


def _effect_positions(
    tokens: Sequence[str],
) -> tuple[tuple[int, ...], tuple[tuple[int, str], ...], tuple[int, ...]]:
    effects = tuple((index, token) for index, token in enumerate(tokens) if token in _EFFECT_TERMS)
    mutation = tuple(index for index, token in effects if token in _MUTATION_VERBS)
    output = tuple((index, token) for index, token in effects if token in _OUTPUT_VERBS or token in _PERSISTENCE_VERBS)
    response = tuple(index for index, token in enumerate(tokens) if token in _RESPONSE_VERBS)
    return mutation, output, response


def _classify_path_mention(
    value: str,
    path_index: int,
    raw_mentions: tuple[tuple[str, int], ...],
    tokens: Sequence[str],
    mutations: tuple[int, ...],
    outputs: tuple[tuple[int, str], ...],
    responses: tuple[int, ...],
) -> _PathMention:
    mutation = _preceding_index(mutations, path_index)
    if mutation is not None:
        return _PathMention(value, path_index, PathRole.MUTATION_TARGET, mutation)
    if mutations and not any(index < path_index for index in mutations):
        if len(raw_mentions) == 1:
            return _PathMention(value, path_index, PathRole.MUTATION_TARGET, mutations[0])
        return _PathMention(value, path_index, PathRole.UNKNOWN)
    output = _preceding_output(outputs, path_index)
    if output is not None:
        role = _classify_output_path_role(output, path_index, raw_mentions, tokens)
        return _PathMention(value, path_index, role, output[0])
    if any(index < path_index for index in responses):
        return _PathMention(value, path_index, PathRole.SOURCE)
    return _PathMention(value, path_index, PathRole.UNKNOWN)


def _preceding_index(positions: tuple[int, ...], path_index: int) -> int | None:
    return next(
        (
            item
            for item in reversed(positions)
            if item < path_index and not any(item < later < path_index for later in positions)
        ),
        None,
    )


def _preceding_output(positions: tuple[tuple[int, str], ...], path_index: int) -> tuple[int, str] | None:
    return next(
        (
            item
            for item in reversed(positions)
            if item[0] < path_index and not any(item[0] < later[0] < path_index for later in positions)
        ),
        None,
    )


def _classify_output_path_role(
    output: tuple[int, str],
    path_index: int,
    mentions: tuple[tuple[str, int], ...],
    tokens: Sequence[str],
) -> PathRole:
    output_index, output_token = output
    later_destinations = tuple(
        later_index
        for _value, later_index in mentions
        if later_index > path_index and _destination_relation(tokens, output_index, later_index)
    )
    if later_destinations:
        return PathRole.SOURCE
    destination = _destination_relation(tokens, output_index, path_index)
    if output_token in _PERSISTENCE_VERBS and (destination or not _response_context(tokens, output_index, path_index)):
        return PathRole.DESTINATION
    if destination:
        return PathRole.DESTINATION
    if _response_context(tokens, output_index, path_index) or _source_relation(tokens, output_index, path_index):
        return PathRole.SOURCE
    return PathRole.MUTATION_TARGET


def _file_targets(objective: str) -> tuple[str, ...]:
    values: list[str] = []
    for match in _FILE_RE.finditer(objective):
        value = match.group(1).strip(".,;:()[]{}")
        if value and value.casefold() not in {item.casefold() for item in values}:
            values.append(value)
    return tuple(values)
