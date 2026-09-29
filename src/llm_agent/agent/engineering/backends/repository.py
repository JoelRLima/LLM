"""Fixed trusted-source RepositoryBackend for installed acceptance."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

from llm_agent.agent.engineering.contracts import (
    EngineeringBackendOutcome,
    EngineeringBackendProtocolError,
    EngineeringBackendStateIndeterminateError,
    EngineeringBackendStatus,
    EngineeringExecutionContext,
    EngineeringRequest,
)
from llm_agent.execution import CommandExecutionError, CommandExecutor, CommandRequest
from llm_agent.filesystem.primitives import inspect_final_path

MAX_ENGINEERING_ACCEPTANCE_SUMMARY_BYTES = 65_536
MAX_ENGINEERING_ACCEPTANCE_PROPERTY_IDS = 64
MAX_ENGINEERING_SUBPROCESS_STDOUT_BYTES = 262_144
MAX_ENGINEERING_SUBPROCESS_STDERR_BYTES = 262_144
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PROPERTY_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class RepositoryBackend:
    def __init__(self, executor: CommandExecutor | None = None) -> None:
        self._executor = executor or CommandExecutor()

    def execute(self, request: EngineeringRequest, context: EngineeringExecutionContext) -> EngineeringBackendOutcome:
        del request
        source = context.source_repository
        if source is None:
            raise EngineeringBackendProtocolError
        verifier = source.root / "scripts" / "verify_installed_package.py"
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        with tempfile.TemporaryDirectory(prefix="llm-agent-w20a-acceptance-") as raw_temp:
            summary_path = Path(raw_temp) / "installed-acceptance.json"
            argv = (
                sys.executable,
                str(verifier),
                "--project-root",
                str(source.root),
                "--python",
                sys.executable,
                "--summary-json",
                str(summary_path),
            )
            try:
                remaining = (
                    max(0.001, context.deadline_monotonic - context.monotonic_now())
                    if context.deadline_monotonic is not None
                    else 1800.0
                )
                result = self._executor.execute(
                    CommandRequest(
                        executable=argv[0],
                        argv=argv[1:],
                        cwd=source.root,
                        environment=environment,
                        timeout_seconds=remaining,
                        cancellation=context.cancellation_token,
                        stdout_limit=MAX_ENGINEERING_SUBPROCESS_STDOUT_BYTES,
                        stderr_limit=MAX_ENGINEERING_SUBPROCESS_STDERR_BYTES,
                    )
                )
                document = _read_summary(summary_path)
                projected = _validate_and_project(document, source.candidate_identity)
                status = (
                    EngineeringBackendStatus.SUCCEEDED
                    if result.return_code == 0 and result.completed and projected["acceptance"] is True
                    else EngineeringBackendStatus.FAILED
                )
                return EngineeringBackendOutcome(status, projected, (), False, True)
            except (EngineeringBackendProtocolError, EngineeringBackendStateIndeterminateError):
                raise
            except CommandExecutionError as exc:
                if exc.code == "WINDOWS_JOB_ASSOCIATION":
                    raise EngineeringBackendProtocolError from exc
                if exc.code == "CLEANUP_ERROR":
                    raise EngineeringBackendStateIndeterminateError from exc
                return EngineeringBackendOutcome(EngineeringBackendStatus.FAILED, {}, (), False, False)
            except OSError as exc:
                raise EngineeringBackendStateIndeterminateError from exc


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _read_summary(path: Path) -> dict[str, Any]:
    try:
        inspection = inspect_final_path(path)
        metadata = inspection.metadata
        if (
            not inspection.exists
            or inspection.is_link_like
            or metadata is None
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > MAX_ENGINEERING_ACCEPTANCE_SUMMARY_BYTES
        ):
            raise ValueError
        with path.open("rb") as stream:
            raw = stream.read(MAX_ENGINEERING_ACCEPTANCE_SUMMARY_BYTES + 1)
        if len(raw) > MAX_ENGINEERING_ACCEPTANCE_SUMMARY_BYTES:
            raise ValueError
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise EngineeringBackendProtocolError from exc


def _validate_and_project(value: dict[str, Any], trusted_identity: str) -> dict[str, Any]:
    try:
        status = value["status"]
        acceptance = value["acceptance"]
        if (
            value["schema_version"] != 2
            or value["evidence_level"] != "installed_deterministic"
            or value["mode"] != "clean-acceptance"
        ):
            raise ValueError
        if status not in {"passed", "failed"} or type(acceptance) is not bool or acceptance != (status == "passed"):
            raise ValueError
        if value["candidate_identity"] != trusted_identity or value["task_files_in_wheel"] is not False:
            raise ValueError
        manifest_hash = value["semantic_manifest_hash"]
        wheel_hash = value["wheel_sha256"]
        if not isinstance(manifest_hash, str) or not _HEX64.fullmatch(manifest_hash):
            raise ValueError
        if wheel_hash is not None and (not isinstance(wheel_hash, str) or not _HEX64.fullmatch(wheel_hash)):
            raise ValueError
        ids = _property_ids(value["properties"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EngineeringBackendProtocolError from exc
    return {
        "acceptance": acceptance,
        "candidate_identity": trusted_identity,
        "evidence_level": "installed_deterministic",
        "mode": "clean-acceptance",
        "property_ids": ids,
        "schema_version": 2,
        "semantic_manifest_hash": manifest_hash,
        "status": status,
        "task_files_in_wheel": False,
        "wheel_sha256": wheel_hash,
    }


def _property_ids(properties: object) -> list[str]:
    if not isinstance(properties, list) or len(properties) > MAX_ENGINEERING_ACCEPTANCE_PROPERTY_IDS:
        raise ValueError
    ids: list[str] = []
    for item in properties:
        property_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(property_id, str) or len(property_id.encode("utf-8")) > 128:
            raise ValueError
        if not _PROPERTY_ID.fullmatch(property_id) or property_id in ids:
            raise ValueError
        ids.append(property_id)
    return ids
