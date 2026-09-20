"""Bounded atomic persistence for UI-facing output artifacts."""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from agent.memory.path_safety import LinkLikePathError, reject_link_like
from agent.outputs.models import (
    MAX_OUTPUT_ARTIFACTS,
    MAX_OUTPUT_PAYLOAD_BYTES,
    MAX_OUTPUT_STORE_BYTES,
    OUTPUT_ID_INVALID,
    OUTPUT_ID_PATTERN,
    OUTPUT_METADATA_INVALID,
    OUTPUT_NOT_FOUND,
    OUTPUT_PAYLOAD_CORRUPT,
    OUTPUT_PAYLOAD_MISSING,
    OUTPUT_PAYLOAD_TOO_LARGE,
    OUTPUT_PERSIST_FAILED,
    OUTPUT_STORE_BUSY,
    OUTPUT_STORE_LIMIT,
    OUTPUT_STORE_UNSAFE,
    OutputArtifact,
    OutputNotFound,
    OutputStoreError,
    OutputValidationError,
    payload_digest,
)
from agent.runtime.filesystem_primitives import has_reparse_point, write_bytes_atomic
from agent.runtime.instance_lock import InstanceLock, InstanceLockError

_FINAL = re.compile(r"^out-[0-9a-f]{32}\.(?:payload\.txt|meta\.json)$")
_LOCK = ".output-store.lock"
_LOCK_GUARD = ".output-store.lock.guard"


def _default_clock() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_directory(path: Path) -> None:
    current = path
    chain: list[Path] = []
    while True:
        chain.append(current)
        if current == current.parent:
            break
        current = current.parent
    for candidate in reversed(chain):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise OutputStoreError(OUTPUT_STORE_UNSAFE, "output store path cannot be inspected") from exc
        if stat.S_ISLNK(metadata.st_mode) or has_reparse_point(metadata):
            raise OutputStoreError(OUTPUT_STORE_UNSAFE, "output store path contains a link-like entry")
        if not stat.S_ISDIR(metadata.st_mode):
            raise OutputStoreError(OUTPUT_STORE_UNSAFE, "output store path is not a directory")


def _safe_final(path: Path) -> None:
    try:
        reject_link_like(path)
    except LinkLikePathError as exc:
        raise OutputStoreError(OUTPUT_STORE_UNSAFE, "output store entry is link-like") from exc


def _output_paths(root: Path, output_id: str) -> tuple[Path, Path]:
    if OUTPUT_ID_PATTERN.fullmatch(output_id) is None:
        raise OutputValidationError(OUTPUT_ID_INVALID, "output_id is invalid")
    return root / f"{output_id}.payload.txt", root / f"{output_id}.meta.json"


class OutputStore:
    def __init__(self, root: Path, *, clock: Callable[[], str | datetime] = _default_clock) -> None:
        self.root = Path(root)
        self.clock = clock

    @property
    def lock_path(self) -> Path:
        return self.root / _LOCK

    def _prepare_root(self) -> None:
        _safe_directory(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        _safe_directory(self.root)

    def _validate_entries(self) -> None:
        try:
            entries = tuple(self.root.iterdir())
        except FileNotFoundError:
            return
        except OSError as exc:
            raise OutputStoreError(OUTPUT_STORE_UNSAFE, "output store cannot be enumerated") from exc
        for entry in entries:
            _safe_final(entry)
            if entry.name in {_LOCK, _LOCK_GUARD}:
                continue
            if _FINAL.fullmatch(entry.name):
                continue
            if entry.name.startswith(".") and entry.name.endswith(".tmp"):
                if self._recognized_temp(entry.name):
                    continue
            raise OutputStoreError(OUTPUT_STORE_UNSAFE, f"unknown output store entry: {entry.name}")

    @staticmethod
    def _recognized_temp(name: str) -> bool:
        if name.startswith(f"{_LOCK}."):
            return True
        for suffix in (".payload.txt", ".meta.json"):
            for output_id in (name[1:].split(f"{suffix}.", 1)[0],):
                if OUTPUT_ID_PATTERN.fullmatch(output_id) and name.startswith(f".{output_id}{suffix}."):
                    return True
        return False

    def _read_metadata_file(self, path: Path, expected_id: str | None = None) -> OutputArtifact:
        _safe_final(path)
        if not path.exists():
            raise OutputNotFound(OUTPUT_NOT_FOUND, "output metadata was not found")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            artifact = OutputArtifact.from_dict(raw)
        except OutputNotFound:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, OutputValidationError) as exc:
            raise OutputStoreError(OUTPUT_METADATA_INVALID, "output metadata is invalid") from exc
        if expected_id is not None and artifact.output_id != expected_id:
            raise OutputStoreError(OUTPUT_METADATA_INVALID, "output metadata ID does not match filename")
        if path.name != f"{artifact.output_id}.meta.json":
            raise OutputStoreError(OUTPUT_METADATA_INVALID, "output metadata filename does not match ID")
        return artifact

    def _read_payload(self, artifact: OutputArtifact) -> str:
        payload_path, _ = _output_paths(self.root, artifact.output_id)
        _safe_final(payload_path)
        if not payload_path.exists():
            raise OutputStoreError(OUTPUT_PAYLOAD_MISSING, "output payload is missing")
        try:
            raw = payload_path.read_bytes()
        except OSError as exc:
            raise OutputStoreError(OUTPUT_PAYLOAD_CORRUPT, "output payload cannot be read") from exc
        if len(raw) > MAX_OUTPUT_PAYLOAD_BYTES:
            raise OutputStoreError(OUTPUT_PAYLOAD_TOO_LARGE, "output payload exceeds its bound")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OutputStoreError(OUTPUT_PAYLOAD_CORRUPT, "output payload is not UTF-8") from exc
        digest, byte_count, char_count, lines = payload_digest(text)
        if (
            digest != artifact.payload_sha256
            or byte_count != artifact.payload_bytes
            or char_count != artifact.char_count
            or lines != artifact.line_count
        ):
            raise OutputStoreError(OUTPUT_PAYLOAD_CORRUPT, "output payload integrity does not match metadata")
        return text

    def metadata(self, output_id: str) -> OutputArtifact:
        self._prepare_root()
        _, metadata_path = _output_paths(self.root, output_id)
        artifact = self._read_metadata_file(metadata_path, output_id)
        self._read_payload(artifact)
        return artifact

    def read(self, output_id: str) -> str:
        artifact = self.metadata(output_id)
        return self._read_payload(artifact)

    def _all_committed(self) -> list[tuple[OutputArtifact, Path, Path]]:
        self._prepare_root()
        self._validate_entries()
        values: list[tuple[OutputArtifact, Path, Path]] = []
        for metadata_path in sorted(self.root.glob("out-*.meta.json"), key=lambda path: path.name):
            output_id = metadata_path.name[: -len(".meta.json")]
            artifact = self._read_metadata_file(metadata_path, output_id)
            payload_path, _ = _output_paths(self.root, output_id)
            self._read_payload(artifact)
            values.append((artifact, metadata_path, payload_path))
        return values

    def list(self, *, limit: int = 50) -> tuple[OutputArtifact, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_OUTPUT_ARTIFACTS:
            raise OutputStoreError(OUTPUT_STORE_LIMIT, "output list limit is invalid")
        values = sorted(
            self._all_committed(),
            key=lambda item: (item[0].created_at, item[0].output_id),
            reverse=True,
        )
        return tuple(item[0] for item in values[:limit])

    def _prune(self, new_output_id: str) -> None:
        values = self._all_committed()
        total_bytes = sum(item[0].payload_bytes for item in values)
        ordered = sorted(values, key=lambda item: (item[0].created_at, item[0].output_id))
        while len(values) > MAX_OUTPUT_ARTIFACTS or total_bytes > MAX_OUTPUT_STORE_BYTES:
            candidate = next((item for item in ordered if item[0].output_id != new_output_id), None)
            if candidate is None:
                raise OutputStoreError(OUTPUT_STORE_LIMIT, "output retention cannot preserve the new artifact")
            artifact, metadata_path, payload_path = candidate
            _safe_final(metadata_path)
            _safe_final(payload_path)
            try:
                metadata_path.unlink()
                payload_path.unlink(missing_ok=True)
            except OSError as exc:
                raise OutputStoreError(OUTPUT_PERSIST_FAILED, "output retention failed") from exc
            values.remove(candidate)
            ordered.remove(candidate)
            total_bytes -= artifact.payload_bytes

    def commit(self, artifact: OutputArtifact, text: str) -> OutputArtifact:
        if not isinstance(artifact, OutputArtifact) or not isinstance(text, str):
            raise OutputStoreError(OUTPUT_PERSIST_FAILED, "output commit input is invalid")
        digest, byte_count, char_count, lines = payload_digest(text)
        if byte_count > MAX_OUTPUT_PAYLOAD_BYTES:
            raise OutputStoreError(OUTPUT_PAYLOAD_TOO_LARGE, "output payload exceeds its bound")
        if (digest, byte_count, char_count, lines) != (
            artifact.payload_sha256, artifact.payload_bytes, artifact.char_count, artifact.line_count
        ):
            raise OutputStoreError(OUTPUT_PERSIST_FAILED, "output metadata does not match payload")
        self._prepare_root()
        lock = InstanceLock.create(self.lock_path)
        try:
            lock.acquire()
        except InstanceLockError as exc:
            raise OutputStoreError(OUTPUT_STORE_BUSY, "output store is busy") from exc
        payload_path, metadata_path = _output_paths(self.root, artifact.output_id)
        published_payload = False
        try:
            self._validate_entries()
            _safe_final(payload_path)
            _safe_final(metadata_path)
            if payload_path.exists() or metadata_path.exists():
                raise OutputStoreError(OUTPUT_STORE_UNSAFE, "output ID already exists")
            try:
                write_bytes_atomic(payload_path, text.encode("utf-8"))
                published_payload = True
                write_bytes_atomic(
                    metadata_path,
                    (json.dumps(artifact.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
                )
                reloaded = self._read_metadata_file(metadata_path, artifact.output_id)
                self._read_payload(reloaded)
                self._prune(artifact.output_id)
                return reloaded
            except OutputStoreError:
                raise
            except Exception as exc:
                raise OutputStoreError(OUTPUT_PERSIST_FAILED, "output commit failed") from exc
        finally:
            if published_payload and not metadata_path.exists():
                try:
                    _safe_final(payload_path)
                    payload_path.unlink(missing_ok=True)
                except OSError:
                    pass
            lock.release()


__all__ = ["OutputStore"]
