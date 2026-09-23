"""Fixed trusted-source RepositoryBackend for installed acceptance."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent.engineering.contracts import (
    EngineeringBackendOutcome,
    EngineeringBackendProtocolError,
    EngineeringBackendStateIndeterminateError,
    EngineeringBackendStatus,
    EngineeringExecutionContext,
    EngineeringRequest,
)
from agent.runtime.filesystem_primitives import inspect_final_path
from agent.tools.process_tree import (
    assign_windows_job,
    close_windows_job,
    create_windows_job,
    process_group_id,
    terminate_process,
)
from agent.tools.stdio_streams import close_pipes, start_readers

MAX_ENGINEERING_ACCEPTANCE_SUMMARY_BYTES = 65_536
MAX_ENGINEERING_ACCEPTANCE_PROPERTY_IDS = 64
MAX_ENGINEERING_SUBPROCESS_STDOUT_BYTES = 262_144
MAX_ENGINEERING_SUBPROCESS_STDERR_BYTES = 262_144
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PROPERTY_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class RepositoryBackend:
    def __init__(self, popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen) -> None:
        self._popen = popen

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
        state = _ProcessState()
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
                _spawn(self._popen, argv, source.root, environment, state)
                _wait(context, state)
                state.settled = True
                document = _read_summary(summary_path)
                projected = _validate_and_project(document, source.candidate_identity)
                status = (
                    EngineeringBackendStatus.SUCCEEDED
                    if state.process is not None and state.process.returncode == 0 and projected["acceptance"] is True
                    else EngineeringBackendStatus.FAILED
                )
                return EngineeringBackendOutcome(status, projected, (), False, True)
            except (EngineeringBackendProtocolError, EngineeringBackendStateIndeterminateError):
                raise
            except _PreChildLaunchFailure:
                return EngineeringBackendOutcome(EngineeringBackendStatus.FAILED, {}, (), False, False)
            except OSError as exc:
                if state.process is None:
                    return EngineeringBackendOutcome(EngineeringBackendStatus.FAILED, {}, (), False, False)
                raise EngineeringBackendStateIndeterminateError from exc
            finally:
                if not _cleanup(state):
                    raise EngineeringBackendStateIndeterminateError


@dataclass
class _ProcessState:
    process: subprocess.Popen[bytes] | None = None
    windows_job: Any = None
    group_id: int | None = None
    readers: list[Any] = field(default_factory=list)
    stop_readers: Any = None
    reader_errors: list[str] = field(default_factory=list)
    settled: bool = False


def _spawn(popen: Callable[..., subprocess.Popen[bytes]], argv: tuple[str, ...], cwd: Path, env: dict[str, str], state: _ProcessState) -> None:
    kwargs: dict[str, Any] = {"cwd": cwd, "env": env, "shell": False, "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": False}
    if os.name == "nt":
        state.windows_job = create_windows_job()
        if state.windows_job is None:
            raise _PreChildLaunchFailure
        kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kwargs["start_new_session"] = True
    try:
        state.process = popen(argv, **kwargs)
    except OSError as exc:
        raise _PreChildLaunchFailure from exc
    if os.name == "nt" and not assign_windows_job(state.windows_job, state.process):
        if terminate_process(state.process, state.windows_job) is not None:
            raise EngineeringBackendStateIndeterminateError
        raise EngineeringBackendProtocolError
    if os.name != "nt":
        state.group_id = process_group_id(state.process)
    state.readers, _, _, state.stop_readers, state.reader_errors = start_readers(
        state.process, MAX_ENGINEERING_SUBPROCESS_STDOUT_BYTES, MAX_ENGINEERING_SUBPROCESS_STDERR_BYTES
    )


class _PreChildLaunchFailure(RuntimeError):
    """The launch failed before a child process existed."""


def _wait(context: EngineeringExecutionContext, state: _ProcessState) -> None:
    assert state.process is not None
    while state.process.poll() is None:
        cancelled = context.cancellation_token is not None and context.cancellation_token.is_set()
        deadline = context.deadline_monotonic is not None and context.monotonic_now() >= context.deadline_monotonic
        if cancelled or deadline:
            if terminate_process(state.process, state.windows_job, process_group_id=state.group_id) is not None:
                raise EngineeringBackendStateIndeterminateError
            break
        time.sleep(0.05)
    state.process.wait()
    for reader in state.readers:
        reader.join(timeout=1)
    if any(reader.is_alive() for reader in state.readers) or state.reader_errors:
        raise EngineeringBackendStateIndeterminateError


def _cleanup(state: _ProcessState) -> bool:
    certain = True
    if state.process is not None and not state.settled:
        certain = terminate_process(state.process, state.windows_job, process_group_id=state.group_id) is None
    if state.stop_readers is not None:
        state.stop_readers.set()
    if state.process is not None:
        close_pipes(state.process)
    for reader in state.readers:
        reader.join(timeout=1)
    certain = certain and not any(reader.is_alive() for reader in state.readers)
    if state.windows_job is not None:
        certain = close_windows_job(state.windows_job) and certain
    return certain


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
