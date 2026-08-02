import os
import stat
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent.tools.extension_catalog_document import (
    ExtensionCatalogDocument,
    PersistedCatalogEntry,
)
from agent.tools.extension_catalog_errors import CatalogCorruptError, CatalogStorageError
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.extension_path import PersistedManifestPath

FINGERPRINT = "a" * 64


def _document() -> ExtensionCatalogDocument:
    return ExtensionCatalogDocument(
        (
            PersistedCatalogEntry(
                "demo.extension",
                PersistedManifestPath("/opt/demo/manifest.json", "posix"),
                FINGERPRINT,
            ),
        )
    )


def test_missing_catalog_loads_empty_without_creating_file(tmp_path: Path) -> None:
    path = tmp_path / "extensions" / "catalog.json"
    storage = ExtensionCatalogStorage(path)

    assert storage.load() == ExtensionCatalogDocument()
    assert not path.exists()
    assert not path.parent.exists()


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    storage = ExtensionCatalogStorage(tmp_path / "extensions" / "catalog.json")

    storage.save(_document())

    assert storage.load() == _document()
    assert storage.path.read_bytes().endswith(b"\n")
    assert list(storage.path.parent.glob(".catalog.json.*.tmp")) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not meaningful on Windows")
def test_new_file_has_restrictive_permissions(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    ExtensionCatalogStorage(path).save(_document())

    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not meaningful on Windows")
def test_existing_permissions_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    path.write_bytes(b"old")
    os.chmod(path, 0o640)
    storage = ExtensionCatalogStorage(path)

    storage.save(_document())

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_replace_callback_only_observes_complete_temp_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "catalog.json"
    storage = ExtensionCatalogStorage(path)
    observed: list[bytes] = []
    real_replace = os.replace

    def observe_then_replace(source: str | bytes | os.PathLike[str], destination: str | bytes | os.PathLike[str]) -> None:
        observed.append(Path(source).read_bytes())
        real_replace(source, destination)

    monkeypatch.setattr("agent.tools.extension_catalog_storage.os.replace", observe_then_replace)

    storage.save(_document())

    assert observed == [path.read_bytes()]


def test_failure_before_replace_preserves_previous_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "catalog.json"
    path.write_bytes(b"previous")
    storage = ExtensionCatalogStorage(path)
    monkeypatch.setattr(
        "agent.tools.extension_catalog_storage.os.replace",
        lambda source, destination: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(CatalogStorageError):
        storage.save(_document())

    assert path.read_bytes() == b"previous"
    assert list(path.parent.glob(".catalog.json.*.tmp")) == []


@pytest.mark.skipif(os.name == "nt", reason="symlink privilege varies on Windows")
def test_final_symlink_is_rejected_without_following_it(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(b"target")
    path = tmp_path / "catalog.json"
    path.symlink_to(target)

    with pytest.raises(CatalogStorageError, match="symlink"):
        ExtensionCatalogStorage(path).save(_document())


def test_corrupt_present_catalog_is_not_treated_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    path.write_bytes(b"not json")

    with pytest.raises(CatalogCorruptError):
        ExtensionCatalogStorage(path).load()


def test_parent_mkdir_failure_is_typed(tmp_path: Path) -> None:
    parent = tmp_path / "not-a-directory"
    parent.write_text("occupied", encoding="utf-8")

    with pytest.raises(CatalogStorageError):
        ExtensionCatalogStorage(parent / "catalog.json").save(_document())


def test_mkstemp_failure_is_typed_and_preserves_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "catalog.json"
    path.write_bytes(b"previous")
    monkeypatch.setattr(
        "agent.tools.extension_catalog_storage.tempfile.mkstemp",
        Mock(side_effect=OSError("mkstemp failed")),
    )

    with pytest.raises(CatalogStorageError):
        ExtensionCatalogStorage(path).save(_document())
    assert path.read_bytes() == b"previous"


@pytest.mark.parametrize("failure", ["chmod", "fdopen"])
def test_setup_failure_closes_descriptor_and_removes_tempfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path = tmp_path / "catalog.json"
    path.write_bytes(b"previous")
    real_close = os.close
    closed: list[int] = []

    def track_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr("agent.tools.extension_catalog_storage.os.close", track_close)
    if failure == "chmod":
        monkeypatch.setattr(
            "agent.tools.extension_catalog_storage.os.chmod",
            Mock(side_effect=OSError("chmod failed")),
        )
    else:
        monkeypatch.setattr(
            "agent.tools.extension_catalog_storage.os.fdopen",
            Mock(side_effect=OSError("fdopen failed")),
        )

    with pytest.raises(CatalogStorageError, match="Falha ao salvar"):
        ExtensionCatalogStorage(path).save(_document())
    assert closed
    assert path.read_bytes() == b"previous"
    assert list(path.parent.glob(".catalog.json.*.tmp")) == []


class _FailingHandle:
    def __init__(self, descriptor: int, stage: str, *, close_fails: bool = False) -> None:
        self.descriptor = descriptor
        self.stage = stage
        self.close_fails = close_fails

    def __enter__(self) -> "_FailingHandle":
        return self

    def close(self) -> None:
        os.close(self.descriptor)
        if self.close_fails:
            raise OSError("close failed")

    def __exit__(self, *args: object) -> None:
        self.close()
        return None

    def write(self, payload: bytes) -> None:
        if self.stage == "write":
            raise OSError("write failed")

    def flush(self) -> None:
        if self.stage == "flush":
            raise OSError("flush failed")

    def fileno(self) -> int:
        return 1


@pytest.mark.parametrize("stage", ["write", "flush", "fsync"])
def test_write_flush_and_fsync_failures_preserve_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    path = tmp_path / "catalog.json"
    path.write_bytes(b"previous")
    monkeypatch.setattr(
        "agent.tools.extension_catalog_storage.os.fdopen",
        lambda descriptor, *_args, **_kwargs: _FailingHandle(descriptor, stage),
    )
    if stage == "fsync":
        monkeypatch.setattr(
            "agent.tools.extension_catalog_storage.os.fsync",
            Mock(side_effect=OSError("fsync failed")),
        )

    with pytest.raises(CatalogStorageError, match="Falha ao salvar"):
        ExtensionCatalogStorage(path).save(_document())
    assert path.read_bytes() == b"previous"
    assert list(path.parent.glob(".catalog.json.*.tmp")) == []


@pytest.mark.parametrize("stage", ["write", "flush", "fsync"])
def test_primary_payload_failure_survives_close_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    path = tmp_path / "catalog.json"
    path.write_bytes(b"previous")
    monkeypatch.setattr(
        "agent.tools.extension_catalog_storage.os.fdopen",
        lambda descriptor, *_args, **_kwargs: _FailingHandle(
            descriptor, stage, close_fails=True
        ),
    )
    if stage == "fsync":
        monkeypatch.setattr(
            "agent.tools.extension_catalog_storage.os.fsync",
            lambda _fd: (_ for _ in ()).throw(OSError("fsync failed")),
        )

    with pytest.raises(CatalogStorageError, match="Falha ao salvar") as caught:
        ExtensionCatalogStorage(path).save(_document())

    assert isinstance(caught.value.__cause__, OSError)
    assert str(caught.value.__cause__) == f"{stage} failed"
    assert len(caught.value.secondary_errors) == 1
    assert str(caught.value.secondary_errors[0]) == "close failed"
    assert path.read_bytes() == b"previous"
    assert list(path.parent.glob(".catalog.json.*.tmp")) == []


def test_isolated_close_failure_is_the_primary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "catalog.json"
    path.write_bytes(b"previous")
    monkeypatch.setattr(
        "agent.tools.extension_catalog_storage.os.fdopen",
        lambda descriptor, *_args, **_kwargs: _FailingHandle(
            descriptor, "close", close_fails=True
        ),
    )
    monkeypatch.setattr("agent.tools.extension_catalog_storage.os.fsync", lambda _fd: None)

    with pytest.raises(CatalogStorageError) as caught:
        ExtensionCatalogStorage(path).save(_document())

    assert isinstance(caught.value.__cause__, OSError)
    assert str(caught.value.__cause__) == "close failed"
    assert caught.value.secondary_errors == ()
    assert path.read_bytes() == b"previous"
    assert list(path.parent.glob(".catalog.json.*.tmp")) == []


def test_cleanup_failure_does_not_mask_primary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "catalog.json"
    path.write_bytes(b"previous")
    real_unlink = Path.unlink

    def fail_temp_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.endswith(".tmp"):
            raise OSError("cleanup failed")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_temp_unlink)
    monkeypatch.setattr(
        "agent.tools.extension_catalog_storage.os.replace",
        Mock(side_effect=OSError("replace failed")),
    )

    with pytest.raises(CatalogStorageError, match="Falha ao salvar") as caught:
        ExtensionCatalogStorage(path).save(_document())
    assert any("cleanup" in note for note in getattr(caught.value, "__notes__", []))
    assert path.read_bytes() == b"previous"


def test_directory_fsync_failure_is_reported_as_uncertain_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    storage = ExtensionCatalogStorage(tmp_path / "catalog.json")
    monkeypatch.setattr(storage, "_fsync_directory", lambda: False)

    storage.save(_document())

    assert storage.path.exists()
    assert "durabilidade" in caplog.text.casefold()
