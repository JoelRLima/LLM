"""Application composition surface for Platform extension services."""

from pathlib import Path

from llm_agent.extensions.extension_catalog_service import ExtensionCatalogService
from llm_agent.extensions.extension_catalog_storage import ExtensionCatalogStorage
from llm_agent.extensions.extension_manifest_parser import (
    ExtensionManifest,
    load_extension_manifest_bytes,
)
from llm_agent.extensions.extension_state import validate_extension_id
from llm_agent.extensions.workspace_extensions_service import WorkspaceExtensionService


def load_strict_extension_manifest(path: str | Path) -> ExtensionManifest:
    """Load the canonical strict manifest without entering Agent tool policy."""

    return load_extension_manifest_bytes(
        Path(path).read_bytes(),
        mode="strict_catalog",
    )


__all__ = [
    "ExtensionCatalogService",
    "ExtensionCatalogStorage",
    "ExtensionManifest",
    "WorkspaceExtensionService",
    "load_strict_extension_manifest",
    "validate_extension_id",
]
