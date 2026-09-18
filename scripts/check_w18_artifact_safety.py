"""Fail closed before W18 evidence JSON is uploaded as a workflow artifact."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HASH_KEYS = {
    "raw_transcript_sha256",
    "transcript_sha256",
    "normalized_transcript_sha256",
}
FORBIDDEN_KEY_RE = re.compile(
    r"(?:secret|token|password|api[_-]?key|authorization|environment_dump|raw_transcript$|"
    r"raw_transcript_(?:b64|bytes)$|transcript_tail$)",
    re.IGNORECASE,
)
FORBIDDEN_RAW_INPUT_KEYS = frozenset({"inputs", "input_sequence"})
SECRET_VALUE_RE = re.compile(
    r"(?:sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|xox[baprs]-[A-Za-z0-9-]{16,}|"
    r"AKIA[0-9A-Z]{16}|(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
WINDOWS_DRIVE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]")
UNC_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])\\\\[^\\/\s]+[\\/][^\\/\s]+")
POSIX_PATH_RE = re.compile(r"(?<![\w:/])/(?:[^/\s]+/)+[^/\s]*")
FILE_URI_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])(?i:file:///[^\s]+)")
LOCAL_PATH_VALUE_RE = re.compile(
    rf"(?:{WINDOWS_DRIVE_PATH_RE.pattern}|{UNC_PATH_RE.pattern}|"
    rf"{POSIX_PATH_RE.pattern}|{FILE_URI_PATH_RE.pattern})"
)
FORBIDDEN_TEXT = (
    "\x1b",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "PYTHONPATH=",
    "PYTHONHOME=",
    "os.environ",
)
PRIVATE_PATH_KEYS = {
    "bundle_root",
    "install_root",
    "application_home",
    "application_paths",
    "real_user_config",
    "path",
    "cwd",
    "probe_home",
    "candidate_python",
    "stable_launcher",
    "candidate_launcher",
    "candidate_runtime",
    "candidate_path",
    "working",
    "outside",
    "sentinels",
    "preserved",
    "path_value",
    "machine_path",
    "user_path",
    "receipt_path",
    "transaction_root",
    "environment",
    "environment_dump",
    "env",
    "environment_variables",
    "env_dump",
    "config",
    "config_json",
    "config_content",
    "configuration",
    "user_config",
}


class ArtifactSafetyError(ValueError):
    """Raised when bounded W18 evidence is unsafe to upload."""


def _validate_field_name(key: str, path: str) -> None:
    lowered = key.casefold()
    if lowered in FORBIDDEN_RAW_INPUT_KEYS:
        raise ArtifactSafetyError(f"raw input sequence field is not uploadable: {path}.{key}")
    if lowered in PRIVATE_PATH_KEYS:
        raise ArtifactSafetyError(f"local path/environment field is not uploadable: {path}.{key}")
    if lowered not in HASH_KEYS and FORBIDDEN_KEY_RE.search(key):
        raise ArtifactSafetyError(f"forbidden evidence field: {path}.{key}")
    if lowered in {"transcript", "raw_transcript", "normalized_transcript", "decoded_transcript"}:
        raise ArtifactSafetyError(f"complete transcript field is not uploadable: {path}.{key}")
    if lowered == "environment" or "environment_dump" in lowered:
        raise ArtifactSafetyError(f"environment dump is not uploadable: {path}.{key}")


def _validate_string(value: str, path: str) -> None:
    if any(marker.casefold() in value.casefold() for marker in FORBIDDEN_TEXT):
        raise ArtifactSafetyError(f"forbidden raw/environment text is not uploadable: {path}")
    if SECRET_VALUE_RE.search(value):
        raise ArtifactSafetyError(f"secret-like value is not uploadable: {path}")
    if LOCAL_PATH_VALUE_RE.search(value):
        raise ArtifactSafetyError(f"local path content is not uploadable: {path}")


def _walk_mapping(value: dict[Any, Any], path: str) -> None:
    for raw_key, child in value.items():
        key = str(raw_key)
        _validate_field_name(key, path)
        _walk(child, f"{path}.{key}")


def _walk_sequence(value: list[Any], path: str) -> None:
    for index, child in enumerate(value):
        _walk(child, f"{path}[{index}]")


def _walk(value: Any, path: str) -> None:
    if isinstance(value, dict):
        _walk_mapping(value, path)
    elif isinstance(value, list):
        _walk_sequence(value, path)
    elif isinstance(value, str):
        _validate_string(value, path)


def check_artifact_safety(paths: list[Path]) -> None:
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ArtifactSafetyError(f"evidence JSON is unreadable: {path}") from exc
        _walk(document, str(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", dest="paths", type=Path, action="append", required=True)
    args = parser.parse_args(argv)
    try:
        check_artifact_safety(args.paths)
    except ArtifactSafetyError as exc:
        print(f"W18_ARTIFACT_SAFETY=FAIL ({exc})", file=sys.stderr)
        return 1
    print(f"W18_ARTIFACT_SAFETY=PASS ({len(args.paths)} JSON files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ArtifactSafetyError", "check_artifact_safety", "main"]
