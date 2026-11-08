"""Authored test cases, independent of queue scheduling and model execution."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from llm_agent.filesystem.path_safety import assert_no_link_ancestors

LIMIT = 1024 * 1024
MAX_CASES = 1000
MAX_TOP_LEVEL_ENTRIES = 2000
MAX_SEED_FILES = 200
MAX_SEED_ENTRIES = 400
MAX_QUEUE_INPUT_BYTES = 32 * 1024 * 1024
MAX_QUEUE_MANIFEST_BYTES = 32 * 1024 * 1024


def digest(value: Any) -> str:
    hasher = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=True, allow_nan=False)
    for chunk in encoder.iterencode(value):
        hasher.update(chunk.encode("utf-8"))
    return hasher.hexdigest()


def serialized_size(value: Any, *, max_bytes: int) -> int:
    """Measure JSON without materializing an untrusted full manifest in memory."""
    size = 0
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False)
    for chunk in encoder.iterencode(value):
        size += len(chunk.encode("utf-8"))
        if size > max_bytes:
            raise ValueError("Queue inputs exceed 32 MiB")
    return size


def relative_path(value: str) -> str:
    """Validate authored paths without consulting the process working directory."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or ":" in value
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("Expected a relative path")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or normalized.startswith("/") or not path.parts or any(part == ".." for part in path.parts):
        raise ValueError("Path outside test directory")
    if any(part.endswith((".", " ")) or PureWindowsPath(part).is_reserved() for part in path.parts):
        raise ValueError("Reserved or ambiguous Windows path")
    return path.as_posix()


def local_path(root: Path, value: str) -> Path:
    path = root / relative_path(value)
    assert_no_link_ancestors(path)
    path = path.resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("Path outside test directory")
    return path


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON field")
        value[key] = item
    return value


def _invalid_constant(value: str) -> Any:
    raise ValueError(f"Invalid JSON constant: {value}")


def parse_json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_invalid_constant)


def read_document(path: Path, *, max_bytes: int = LIMIT) -> dict[str, Any]:
    assert_no_link_ancestors(path)
    if path.stat().st_size > max_bytes:
        raise ValueError("Test document exceeds size limit")
    value = parse_json(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


@dataclass(frozen=True)
class TestCase:
    case_id: str
    prompt: str
    files: tuple[tuple[str, str], ...]
    checks: tuple[dict[str, Any], ...]

    def document(self) -> dict[str, Any]:
        return {"id": self.case_id, "prompt": self.prompt, "files": dict(self.files), "checks": list(self.checks)}


@dataclass
class _InputBudget:
    total_bytes: int = 0

    def add(self, size: int) -> None:
        self.total_bytes += size
        if self.total_bytes > MAX_QUEUE_INPUT_BYTES:
            raise ValueError("Queue source files exceed 32 MiB")

    def path(self, path: Path, *, limit: int = LIMIT) -> int:
        size = path.stat().st_size
        if size > limit:
            raise ValueError(f"{path.name} too large")
        self.add(size)
        return size

    def name(self, value: str) -> None:
        self.add(len(value.encode("utf-8")))


def _validate_case_id(case_id: Any) -> None:
    if (
        not isinstance(case_id, str)
        or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", case_id)
        or PureWindowsPath(case_id).is_reserved()
    ):
        raise ValueError(f"Pasta de tarefa invalida: {case_id!r}. Use de 1 a 64 letras sem acentos, numeros, '_' ou '-'.")


def _validate_prompt(prompt: Any) -> None:
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
        raise ValueError("Prompt must contain 1..16000 characters")


def _validate_files(files: Any) -> None:
    if not isinstance(files, dict) or len(files) > MAX_SEED_FILES:
        raise ValueError(f"files must be a mapping of at most {MAX_SEED_FILES} UTF-8 files")
    normalized_names = set()
    for name, content in files.items():
        normalized = relative_path(name).casefold()
        if normalized in normalized_names:
            raise ValueError("Duplicate fixture paths on case-insensitive filesystems")
        normalized_names.add(normalized)
        if not isinstance(content, str) or len(content.encode()) > LIMIT:
            raise ValueError("Invalid fixture content")


def _validate_check(check: Any, fields: dict[str, set[str]]) -> None:
    if (
        not isinstance(check, dict)
        or not isinstance(check.get("kind"), str)
        or check["kind"] not in fields
        or set(check) != fields[check["kind"]]
    ):
        raise ValueError("Unknown check kind or fields")
    relative_path(check["path"])
    if check["kind"] in {"text_equals", "sha256", "python"} and not isinstance(check["expected"], str):
        raise ValueError("Expected text")
    if check["kind"] == "sha256" and not re.fullmatch("[a-f0-9]{64}", check["expected"]):
        raise ValueError("Invalid SHA-256")


def _validate_checks(checks: Any) -> None:
    if not isinstance(checks, list) or len(checks) > 100:
        raise ValueError("checks must be a list with at most 100 entries")
    fields = {
        "exists": {"kind", "path"},
        "absent": {"kind", "path"},
        "text_equals": {"kind", "path", "expected"},
        "json_equals": {"kind", "path", "expected"},
        "sha256": {"kind", "path", "expected"},
        "python": {"kind", "path", "expected"},
    }
    for check in checks:
        _validate_check(check, fields)


def parse_case(value: dict[str, Any]) -> TestCase:
    if set(value) != {"id", "prompt", "files", "checks"}:
        raise ValueError("Case requires exactly id, prompt, files, checks")
    case_id, prompt, files, checks = (value[key] for key in ("id", "prompt", "files", "checks"))
    _validate_case_id(case_id)
    _validate_prompt(prompt)
    _validate_files(files)
    _validate_checks(checks)
    digest(value)
    return TestCase(case_id, prompt, tuple(files.items()), tuple(checks))


def _task_folders(directory: Path) -> tuple[Path, ...]:
    entries: list[Path] = []
    for entry in directory.iterdir():
        if len(entries) >= MAX_TOP_LEVEL_ENTRIES:
            raise ValueError(f"Task source has more than {MAX_TOP_LEVEL_ENTRIES} top-level entries")
        assert_no_link_ancestors(entry)
        entries.append(entry)
    return tuple(
        folder for folder in sorted(entries)
        if folder.is_dir() and (folder / "task.md").is_file()
    )


def _seed_files(folder: Path, budget: _InputBudget) -> dict[str, str]:
    seed = local_path(folder, "seed")
    if not seed.exists():
        return {}
    files: dict[str, str] = {}
    entries = 0
    for path in seed.rglob("*"):
        entries += 1
        if entries > MAX_SEED_ENTRIES:
            raise ValueError(f"A task seed has more than {MAX_SEED_ENTRIES} entries")
        assert_no_link_ancestors(path)
        if not path.is_file():
            continue
        if len(files) >= MAX_SEED_FILES:
            raise ValueError(f"A task seed has more than {MAX_SEED_FILES} files")
        relative_name = path.relative_to(seed).as_posix()
        relative_path(relative_name)
        budget.name(relative_name)
        budget.path(path)
        files[relative_name] = path.read_text(encoding="utf-8")
    return files


def _task_checks(folder: Path, budget: _InputBudget) -> list[dict[str, Any]]:
    checks_path = local_path(folder, "checks.json")
    checks_doc: dict[str, Any] = {"checks": []}
    if checks_path.exists():
        budget.path(checks_path)
        checks_doc = read_document(checks_path)
    if set(checks_doc) != {"checks"} or not isinstance(checks_doc["checks"], list):
        raise ValueError(f"{folder.name}/checks.json: expected an object with a checks list")
    verifier = local_path(folder, "verify.py")
    if verifier.exists():
        budget.path(verifier)
        checks_doc["checks"].append({
            "kind": "python",
            "path": "verify.py",
            "expected": verifier.read_text(encoding="utf-8-sig"),
        })
    return checks_doc["checks"]


def _read_task_case(folder: Path, budget: _InputBudget) -> TestCase:
    prompt_path = local_path(folder, "task.md")
    budget.path(prompt_path, limit=64000)
    files = _seed_files(folder, budget)
    checks = _task_checks(folder, budget)
    return parse_case({
        "id": folder.name,
        "prompt": prompt_path.read_text(encoding="utf-8-sig"),
        "files": dict(sorted(files.items())),
        "checks": checks,
    })


def discover_cases(directory: Path) -> tuple[TestCase, ...]:
    """Read a bounded set of task folders and files into validated cases."""
    assert_no_link_ancestors(directory)
    folders = _task_folders(directory)
    if len(folders) > MAX_CASES:
        raise ValueError(f"Expected 1..{MAX_CASES} task directories")
    budget = _InputBudget()
    cases = tuple(_read_task_case(folder, budget) for folder in folders)
    if not cases:
        raise ValueError(f"Expected 1..{MAX_CASES} task directories")
    if len({case.case_id.casefold() for case in cases}) != len(cases):
        raise ValueError("Case IDs must be unique on case-insensitive filesystems")
    return tuple(cases)
