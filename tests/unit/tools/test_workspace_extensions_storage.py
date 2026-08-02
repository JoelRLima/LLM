from pathlib import Path

import pytest

from agent.tools.extension_catalog_errors import (
    CatalogStorageError,
    WorkspaceConfigurationCorruptError,
    WorkspaceStorageError,
)
from agent.tools.extension_state import WorkspaceExtensionSelection, WorkspaceExtensionsState
from agent.tools.workspace_extensions_storage import WorkspaceExtensionsStorage


def test_missing_workspace_file_is_empty_without_creation(tmp_path: Path) -> None:
    path = tmp_path / "data" / "extensions.json"
    storage = WorkspaceExtensionsStorage(path)
    assert storage.load() == WorkspaceExtensionsState()
    assert not path.exists()
    assert not path.parent.exists()


def test_workspace_storage_round_trip_and_atomic_temp_cleanup(tmp_path: Path) -> None:
    path = tmp_path / "data" / "extensions.json"
    state = WorkspaceExtensionsState((WorkspaceExtensionSelection("demo.extension", ("read",)),))
    storage = WorkspaceExtensionsStorage(path)
    storage.save(state)
    assert storage.load() == state
    assert list(path.parent.glob(".extensions.json.*.tmp")) == []


@pytest.mark.parametrize("payload", [b"", b"not-json", b"\xef\xbb\xbf{}"])
def test_present_invalid_workspace_file_is_not_empty(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "extensions.json"
    path.write_bytes(payload)
    with pytest.raises(WorkspaceConfigurationCorruptError):
        WorkspaceExtensionsStorage(path).load()


def test_workspace_storage_preserves_catalog_storage_cause_and_secondary_errors(
    tmp_path: Path,
) -> None:
    storage = WorkspaceExtensionsStorage(tmp_path / "extensions.json")
    secondary = OSError("cleanup sentinel")

    def fail(_payload: bytes, _mode: int | None) -> None:
        raise CatalogStorageError("primary", secondary_errors=(secondary,))

    storage._atomic._save_atomically = fail  # type: ignore[method-assign]
    with pytest.raises(WorkspaceStorageError) as caught:
        storage.save(WorkspaceExtensionsState())
    assert caught.value.secondary_errors == (secondary,)
    assert isinstance(caught.value.__cause__, CatalogStorageError)
