"""Carregamento validado de cenários JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

from agent.evaluation.contracts import CapabilityScenario, FileExpectation, ScenarioExpectation


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"'{field_name}' deve ser uma lista de strings.")
    return tuple(value)


def _parse_file_expectation(data: Any) -> FileExpectation:
    if not isinstance(data, dict):
        raise ValueError("Cada item de 'files' deve ser um objeto.")
    path = data.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Expectativa de arquivo sem 'path' válido.")
    exact_content = data.get("exact_content")
    if exact_content is not None and not isinstance(exact_content, str):
        raise ValueError("'exact_content' deve ser string ou null.")
    return FileExpectation(
        path=path,
        exists=bool(data.get("exists", True)),
        exact_content=exact_content,
        contains=_string_tuple(data.get("contains"), "contains"),
        not_contains=_string_tuple(data.get("not_contains"), "not_contains"),
    )


def _parse_expectation(data: Any) -> ScenarioExpectation:
    if not isinstance(data, dict):
        raise ValueError("O campo 'expectation' deve ser um objeto.")
    raw_files = data.get("files", [])
    if not isinstance(raw_files, list):
        raise ValueError("O campo 'expectation.files' deve ser uma lista.")
    max_steps = data.get("max_steps")
    if max_steps is not None and (not isinstance(max_steps, int) or max_steps < 0):
        raise ValueError("'max_steps' deve ser inteiro não negativo ou null.")
    return ScenarioExpectation(
        success=bool(data.get("success", True)),
        files=tuple(_parse_file_expectation(item) for item in raw_files),
        unchanged_files=_string_tuple(data.get("unchanged_files"), "unchanged_files"),
        allowed_changed_files=_string_tuple(
            data.get("allowed_changed_files"), "allowed_changed_files"
        ),
        answer_contains=_string_tuple(data.get("answer_contains"), "answer_contains"),
        answer_not_contains=_string_tuple(data.get("answer_not_contains"), "answer_not_contains"),
        max_steps=max_steps,
    )


def _parse_scenario(data: Any, source: Path) -> CapabilityScenario:
    if not isinstance(data, dict):
        raise ValueError(f"Cenário '{source}' deve ser um objeto JSON.")
    scenario_id_raw = data.get("scenario_id")
    capability_raw = data.get("capability")
    objective_raw = data.get("objective")
    for name, value in (
        ("scenario_id", scenario_id_raw),
        ("capability", capability_raw),
        ("objective", objective_raw),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Cenário '{source}' sem '{name}' válido.")
    assert isinstance(scenario_id_raw, str)
    assert isinstance(capability_raw, str)
    assert isinstance(objective_raw, str)
    initial_files = data.get("initial_files", {})
    if not isinstance(initial_files, dict) or not all(
        isinstance(path, str) and isinstance(content, str)
        for path, content in initial_files.items()
    ):
        raise ValueError("'initial_files' deve mapear caminhos para strings.")
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("'metadata' deve ser um objeto.")
    return CapabilityScenario(
        scenario_id=scenario_id_raw,
        capability=capability_raw,
        objective=objective_raw,
        initial_files=dict(initial_files),
        expectation=_parse_expectation(data.get("expectation", {})),
        metadata=dict(metadata),
    )


def load_scenario(path: str | Path) -> CapabilityScenario:
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        return _parse_scenario(json.load(handle), source)


def load_scenarios(directory: str | Path) -> List[CapabilityScenario]:
    root = Path(directory)
    scenarios = [load_scenario(path) for path in sorted(root.glob("*.json"))]
    ids = [scenario.scenario_id for scenario in scenarios]
    duplicates = sorted({scenario_id for scenario_id in ids if ids.count(scenario_id) > 1})
    if duplicates:
        raise ValueError(f"IDs de cenário duplicados: {', '.join(duplicates)}")
    return scenarios
