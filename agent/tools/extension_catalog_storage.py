"""Atomic filesystem storage for the persisted extension catalog."""

from __future__ import annotations

import logging
import os
import stat
import tempfile
from pathlib import Path

from agent.runtime.filesystem_primitives import is_link_like
from agent.tools.extension_catalog_codec import decode_catalog, encode_catalog
from agent.tools.extension_catalog_document import ExtensionCatalogDocument
from agent.tools.extension_catalog_errors import (
    CatalogCodecError,
    CatalogCorruptError,
    CatalogStorageError,
)

_LOGGER = logging.getLogger(__name__)


class _PayloadFailure(Exception):
    """Carry a primary payload error and secondary close errors together."""

    def __init__(
        self,
        primary: BaseException,
        secondary: tuple[BaseException, ...] = (),
    ) -> None:
        super().__init__(str(primary))
        self.primary = primary
        self.secondary = secondary


def _record_secondary_error(
    primary: CatalogStorageError,
    secondary: BaseException,
    context: str,
) -> None:
    primary.add_secondary_error(secondary)
    note = f"Falha secundaria durante {context}: {secondary}"
    add_note = getattr(primary, "add_note", None)
    if callable(add_note):
        add_note(note)
    else:  # pragma: no cover - Python 3.10 compatibility fallback.
        _LOGGER.warning(note, exc_info=secondary)


class ExtensionCatalogStorage:
    """Load and atomically save one catalog path without manifest semantics."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> ExtensionCatalogDocument:
        if is_link_like(self.path):
            raise CatalogStorageError(f"Destino do catalogo nao pode ser symlink: {self.path}")
        if not self.path.exists():
            return ExtensionCatalogDocument()
        try:
            payload = self.path.read_bytes()
        except OSError as exc:
            raise CatalogStorageError(f"Falha ao ler catalogo: {self.path}") from exc
        try:
            return decode_catalog(payload)
        except CatalogCodecError as exc:
            raise CatalogCorruptError(f"Catalogo corrompido: {self.path}: {exc}") from exc

    def save(self, document: ExtensionCatalogDocument) -> None:
        if is_link_like(self.path):
            raise CatalogStorageError(f"Destino do catalogo nao pode ser symlink: {self.path}")
        try:
            payload = encode_catalog(document)
        except CatalogCodecError as exc:
            raise CatalogStorageError(f"Catalogo nao pode ser codificado: {exc}") from exc
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CatalogStorageError(f"Nao foi possivel criar diretorio do catalogo: {self.path.parent}") from exc

        existing_mode: int | None = None
        if self.path.exists():
            try:
                existing_mode = stat.S_IMODE(self.path.stat().st_mode)
            except OSError as exc:
                raise CatalogStorageError(f"Nao foi possivel obter permissoes: {self.path}") from exc

        self._save_atomically(payload, existing_mode)

    def _save_atomically(self, payload: bytes, existing_mode: int | None) -> None:
        descriptor, temporary_name = self._create_tempfile()
        temporary = Path(temporary_name)
        promoted = False
        operation_error: CatalogStorageError | None = None
        try:
            self._write_payload(descriptor, temporary, payload, existing_mode)
            os.replace(temporary, self.path)
            promoted = True
            if not self._fsync_directory():
                _LOGGER.warning("Durabilidade do catalogo incerta apos os.replace: %s", self.path)
        except _PayloadFailure as exc:
            operation_error = CatalogStorageError(
                f"Falha ao salvar catalogo: {self.path}",
                secondary_errors=exc.secondary,
            )
            raise operation_error from exc.primary
        except (OSError, ValueError) as exc:
            operation_error = CatalogStorageError(f"Falha ao salvar catalogo: {self.path}")
            raise operation_error from exc
        finally:
            self._cleanup_tempfile(temporary, promoted, operation_error)

    @staticmethod
    def _write_payload(
        descriptor: int,
        temporary: Path,
        payload: bytes,
        existing_mode: int | None,
    ) -> None:
        owned_descriptor: int | None = descriptor
        handle = None
        primary: BaseException | None = None
        secondary: list[BaseException] = []
        try:
            if existing_mode is not None:
                os.chmod(temporary, existing_mode)
            handle = os.fdopen(descriptor, "wb")
            owned_descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException as exc:
            primary = exc
        finally:
            if handle is not None:
                try:
                    handle.close()
                except BaseException as close_error:
                    if primary is None:
                        primary = close_error
                    else:
                        secondary.append(close_error)
            elif owned_descriptor is not None:
                try:
                    os.close(owned_descriptor)
                except BaseException as close_error:
                    if primary is None:
                        primary = close_error
                    else:
                        secondary.append(close_error)
        if primary is not None:
            raise _PayloadFailure(primary, tuple(secondary)) from primary

    def _create_tempfile(self) -> tuple[int, str]:
        try:
            return tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
        except OSError as exc:
            raise CatalogStorageError(f"Nao foi possivel criar temporario do catalogo: {self.path}") from exc

    @staticmethod
    def _cleanup_tempfile(
        temporary: Path,
        promoted: bool,
        operation_error: CatalogStorageError | None,
    ) -> None:
        if promoted:
            return
        try:
            temporary.unlink(missing_ok=True)
        except OSError as cleanup_error:
            if operation_error is not None:
                _record_secondary_error(operation_error, cleanup_error, "cleanup do catalogo")
                return
            raise CatalogStorageError(
                f"Falha ao limpar temporario do catalogo: {temporary}"
            ) from cleanup_error

    def _fsync_directory(self) -> bool:
        if os.name == "nt":
            return True
        try:
            descriptor = os.open(self.path.parent, os.O_RDONLY)
        except OSError:
            return False
        fsync_ok = True
        try:
            os.fsync(descriptor)
        except OSError:
            fsync_ok = False
        try:
            os.close(descriptor)
        except OSError:
            return False
        return fsync_ok


__all__ = ["ExtensionCatalogStorage"]
