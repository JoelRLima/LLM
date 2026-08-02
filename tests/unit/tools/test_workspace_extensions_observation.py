import json
from pathlib import Path

import pytest

from agent.tools.extension_catalog_document import ExtensionCatalogDocument, PersistedCatalogEntry
from agent.tools.extension_catalog_service import host_path_flavor
from agent.tools.extension_catalog_validation import (
    ManifestObservation,
    ManifestSummary,
    observe_catalog_document,
)
from agent.tools.extension_path import PersistedManifestPath
from agent.tools.extension_state import fingerprint_for_bytes


def test_manifest_summary_copies_and_canonicalizes_capabilities() -> None:
    capabilities = ["process.execute", "filesystem.read", "filesystem.read"]
    summary = ManifestSummary(capabilities)
    capabilities.append("network")
    assert summary.required_capabilities == ("filesystem.read", "process.execute")
    assert hash(summary) == hash(ManifestSummary(("filesystem.read", "process.execute")))


def test_manifest_summary_rejects_invalid_capability_objects() -> None:
    with pytest.raises((TypeError, ValueError)):
        ManifestSummary([object()])  # type: ignore[list-item]
    with pytest.raises((TypeError, ValueError)):
        ManifestSummary([""])


@pytest.mark.parametrize(
    "args",
    [
        ("Invalid ID", "unchanged", "f" * 64, ManifestSummary()),
        ("demo.extension", "unknown", None, ManifestSummary()),
        ("demo.extension", "unchanged", None, ManifestSummary()),
        ("demo.extension", "missing", None, ManifestSummary(("read",))),
    ],
)
def test_manifest_observation_validates_invariants(args: tuple[object, ...]) -> None:
    with pytest.raises((TypeError, ValueError)):
        ManifestObservation(*args)  # type: ignore[arg-type]


def test_observation_reads_manifest_once_and_extracts_safe_capabilities(tmp_path: Path) -> None:
    payload = {
        "id": "demo.extension",
        "version": "1",
        "protocol_version": "1.0",
        "transport": "stdio",
        "entrypoint": ["python", "demo.py"],
        "timeout_seconds": 5,
        "tools": [
            {"name": "a", "schema": {}, "capabilities": ["write", "read"]},
            {"name": "b", "schema": {}, "capabilities": ["read", "process"]},
        ],
    }
    content = json.dumps(payload, sort_keys=True).encode()
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(content)
    document = ExtensionCatalogDocument(
        (
            PersistedCatalogEntry(
                "demo.extension",
                PersistedManifestPath(manifest.as_posix(), host_path_flavor()),
                fingerprint_for_bytes(content),
            ),
        )
    )
    pairs = observe_catalog_document(document, host_path_flavor())
    observation, diagnostic = pairs[0]
    assert observation.manifest_status == "unchanged"
    assert observation.observed_fingerprint == fingerprint_for_bytes(content)
    assert observation.manifest_summary.required_capabilities == ("process", "read", "write")
    assert diagnostic.state == "unchanged"
