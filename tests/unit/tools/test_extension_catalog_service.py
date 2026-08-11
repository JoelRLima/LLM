import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from agent.tools.extension_catalog_document import ExtensionCatalogDocument, PersistedCatalogEntry
from agent.tools.extension_catalog_errors import (
    CatalogDriftError,
    CatalogIdConflictError,
    CatalogManifestIncompatibleError,
    CatalogManifestInvalidError,
    CatalogManifestMissingError,
    CatalogPathConflictError,
    CatalogReplaceConflictError,
)
from agent.tools.extension_catalog_service import ExtensionCatalogService, host_path_flavor
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.extension_path import PersistedManifestPath


def _manifest(
    extension_id: str = "demo.extension",
    *,
    protocol: str = "1.0",
    version: str = "1.0.0",
) -> dict[str, object]:
    return {
        "id": extension_id,
        "version": version,
        "protocol_version": protocol,
        "transport": "stdio",
        "entrypoint": ["python", "demo.py"],
        "timeout_seconds": 5,
        "tools": [{"name": "demo_tool", "schema": {}, "capabilities": ["read", "process"]}],
    }


def _write_manifest(path: Path, payload: dict[str, object] | bytes | str | None = None) -> None:
    if payload is None:
        payload = _manifest()
    if isinstance(payload, dict):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    elif isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_bytes(payload)


def _service(tmp_path: Path) -> ExtensionCatalogService:
    return ExtensionCatalogService(ExtensionCatalogStorage(tmp_path / "extensions" / "catalog.json"))


class _CountingStorage(ExtensionCatalogStorage):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.save_calls = 0

    def save(self, document: ExtensionCatalogDocument) -> None:
        self.save_calls += 1
        super().save(document)


def test_add_reload_validate_change_replace_remove_and_preserve_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    service = _service(tmp_path)

    added = service.add(manifest)
    assert added.changed is True
    assert service.load().get("demo.extension").manifest_path.persisted_value == manifest.as_posix()
    assert service.add(manifest).changed is False
    assert service.validate()[0].state == "unchanged"

    _write_manifest(manifest, json.dumps({**_manifest(), "version": "1.0.1"}))
    assert service.validate()[0].state == "changed"
    old_fingerprint = added.entry.manifest_sha256
    with pytest.raises(CatalogDriftError):
        service.replace("demo.extension", manifest, expected_fingerprint="b" * 64)
    replaced = service.replace("demo.extension", manifest, expected_fingerprint=old_fingerprint)
    assert replaced.changed is True
    assert service.validate()[0].state == "unchanged"
    removed = service.remove("demo.extension")
    assert removed.changed is True
    assert service.load() == ExtensionCatalogDocument()
    assert manifest.exists()
    assert service.remove("demo.extension").changed is False


def test_relative_path_requires_explicit_absolute_base_dir(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    service = _service(tmp_path)

    with pytest.raises(CatalogManifestIncompatibleError):
        service.add("manifest.json")
    with pytest.raises(CatalogManifestIncompatibleError):
        service.add("manifest.json", base_dir="relative-base")
    assert service.add("manifest.json", base_dir=tmp_path).changed is True


def test_add_rejects_missing_invalid_and_incompatible_manifests(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(CatalogManifestMissingError):
        service.add(tmp_path / "missing.json")
    invalid = tmp_path / "invalid.json"
    _write_manifest(invalid, "not json")
    with pytest.raises(CatalogManifestInvalidError):
        service.add(invalid)
    incompatible = tmp_path / "incompatible.json"
    _write_manifest(incompatible, {**_manifest(), "protocol_version": "2.0"})
    with pytest.raises(CatalogManifestIncompatibleError):
        service.add(incompatible)


def test_add_reads_manifest_bytes_once_for_validation_and_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    service = _service(tmp_path)
    real_read_bytes = Path.read_bytes
    reads = 0

    def count_manifest_reads(path: Path) -> bytes:
        nonlocal reads
        if path == manifest:
            reads += 1
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", count_manifest_reads)
    service.add(manifest)

    assert reads == 1


def test_add_rejects_id_and_path_conflicts_without_overwrite(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _write_manifest(first)
    _write_manifest(second, {**_manifest(), "version": "2"})
    service = _service(tmp_path)
    service.add(first)

    _write_manifest(first, {**_manifest(), "version": "2"})
    with pytest.raises(CatalogDriftError):
        service.add(first)
    with pytest.raises(CatalogIdConflictError):
        service.add(second)

    other_entry = PersistedCatalogEntry(
        "other.extension",
        PersistedManifestPath(first.as_posix(), host_path_flavor()),
        "c" * 64,
    )
    service.storage.save(ExtensionCatalogDocument((other_entry,)))
    _write_manifest(first)
    with pytest.raises(CatalogPathConflictError):
        service.add(first)


def test_replace_exact_repeat_is_idempotent_with_original_expected_fingerprint(tmp_path: Path) -> None:
    old_manifest = tmp_path / "old.json"
    new_manifest = tmp_path / "new.json"
    _write_manifest(old_manifest, _manifest("demo.extension", protocol="1.0"))
    _write_manifest(new_manifest, {**_manifest("demo.extension"), "version": "2"})
    storage = _CountingStorage(tmp_path / "catalog.json")
    service = ExtensionCatalogService(storage)
    added = service.add(old_manifest)

    first = service.replace(
        "demo.extension",
        new_manifest,
        expected_fingerprint=added.entry.manifest_sha256,
    )
    repeated = service.replace(
        "demo.extension",
        new_manifest,
        expected_fingerprint=added.entry.manifest_sha256,
    )

    assert first.changed is True
    assert repeated.changed is False
    assert repeated.entry == first.entry
    assert storage.save_calls == 2


def test_replace_expected_mismatch_uses_specific_conflict_type(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    replacement = tmp_path / "replacement.json"
    _write_manifest(manifest)
    _write_manifest(replacement, {**_manifest(), "version": "2"})
    service = _service(tmp_path)
    service.add(manifest)

    with pytest.raises(CatalogReplaceConflictError):
        service.replace("demo.extension", replacement, expected_fingerprint="b" * 64)


def test_replace_rejects_third_state_after_reload(tmp_path: Path) -> None:
    old = tmp_path / "old.json"
    middle = tmp_path / "middle.json"
    third = tmp_path / "third.json"
    _write_manifest(old, _manifest(version="old"))
    _write_manifest(middle, _manifest(version="middle"))
    _write_manifest(third, _manifest(version="third"))
    service = _service(tmp_path)
    added = service.add(old)
    first = service.replace("demo.extension", middle, expected_fingerprint=added.entry.manifest_sha256)
    service.replace("demo.extension", third, expected_fingerprint=first.entry.manifest_sha256)

    with pytest.raises(CatalogReplaceConflictError):
        service.replace("demo.extension", middle, expected_fingerprint=added.entry.manifest_sha256)


def test_replace_same_fingerprint_different_path_is_not_idempotent(tmp_path: Path) -> None:
    old = tmp_path / "old.json"
    replacement = tmp_path / "replacement.json"
    duplicate = tmp_path / "duplicate.json"
    _write_manifest(old, _manifest(version="old"))
    _write_manifest(replacement, _manifest(version="new"))
    duplicate.write_bytes(replacement.read_bytes())
    service = _service(tmp_path)
    added = service.add(old)
    service.replace("demo.extension", replacement, expected_fingerprint=added.entry.manifest_sha256)

    with pytest.raises(CatalogReplaceConflictError):
        service.replace("demo.extension", duplicate, expected_fingerprint=added.entry.manifest_sha256)


def test_replace_same_path_different_fingerprint_is_not_idempotent(tmp_path: Path) -> None:
    old = tmp_path / "old.json"
    replacement = tmp_path / "replacement.json"
    _write_manifest(old, _manifest(version="old"))
    _write_manifest(replacement, _manifest(version="new"))
    service = _service(tmp_path)
    added = service.add(old)
    service.replace("demo.extension", replacement, expected_fingerprint=added.entry.manifest_sha256)
    _write_manifest(replacement, _manifest(version="changed"))

    with pytest.raises(CatalogReplaceConflictError):
        service.replace("demo.extension", replacement, expected_fingerprint=added.entry.manifest_sha256)


def test_replace_rejects_removed_entry(tmp_path: Path) -> None:
    old = tmp_path / "old.json"
    replacement = tmp_path / "replacement.json"
    _write_manifest(old, _manifest(version="old"))
    _write_manifest(replacement, _manifest(version="new"))
    service = _service(tmp_path)
    added = service.add(old)
    service.remove("demo.extension")

    with pytest.raises(CatalogReplaceConflictError):
        service.replace("demo.extension", replacement, expected_fingerprint=added.entry.manifest_sha256)


def test_replace_rejects_path_conflict_with_other_extension(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    other = tmp_path / "other.json"
    _write_manifest(target, _manifest("demo.extension", version="old"))
    _write_manifest(other, _manifest("other.extension", version="other"))
    service = _service(tmp_path)
    added = service.add(target)
    service.add(other)
    _write_manifest(other, _manifest("demo.extension", version="new"))

    with pytest.raises(CatalogPathConflictError):
        service.replace("demo.extension", other, expected_fingerprint=added.entry.manifest_sha256)


def test_replace_rejects_divergent_manifest_id(tmp_path: Path) -> None:
    old = tmp_path / "old.json"
    replacement = tmp_path / "replacement.json"
    _write_manifest(old, _manifest("demo.extension", version="old"))
    _write_manifest(replacement, _manifest("other.extension", version="new"))
    service = _service(tmp_path)
    added = service.add(old)

    with pytest.raises(CatalogManifestIncompatibleError):
        service.replace("demo.extension", replacement, expected_fingerprint=added.entry.manifest_sha256)


def test_validate_reports_all_required_states_without_mutating_catalog(tmp_path: Path) -> None:
    unchanged = tmp_path / "unchanged.json"
    changed = tmp_path / "changed.json"
    invalid = tmp_path / "invalid.json"
    incompatible = tmp_path / "incompatible.json"
    missing = tmp_path / "missing.json"
    for path in (unchanged, changed):
        _write_manifest(path, _manifest(path.stem + ".extension"))
    _write_manifest(invalid, "not-json")
    _write_manifest(incompatible, _manifest("incompatible.extension", protocol="2.0"))
    service = _service(tmp_path)
    for path in (unchanged, changed, invalid, incompatible):
        if path in (invalid, incompatible):
            continue
        service.add(path)
    _write_manifest(changed, {**_manifest("changed.extension"), "version": "2"})
    document = service.load()
    foreign_flavor = "posix" if host_path_flavor() == "windows" else "windows"
    foreign_path = "/foreign/manifest.json" if foreign_flavor == "posix" else "C:/foreign/manifest.json"
    entries = document.entries + (
        PersistedCatalogEntry(
            "invalid.extension",
            PersistedManifestPath(invalid.as_posix(), host_path_flavor()),
            "a" * 64,
        ),
        PersistedCatalogEntry(
            "incompatible.extension",
            PersistedManifestPath(incompatible.as_posix(), host_path_flavor()),
            "b" * 64,
        ),
        PersistedCatalogEntry(
            "missing.extension",
            PersistedManifestPath(missing.as_posix(), host_path_flavor()),
            "c" * 64,
        ),
        PersistedCatalogEntry(
            "foreign.extension",
            PersistedManifestPath(foreign_path, foreign_flavor),
            "d" * 64,
        ),
    )
    service.storage.save(ExtensionCatalogDocument(entries))

    diagnostics = {item.extension_id: item.state for item in service.validate()}

    assert diagnostics == {
        "changed.extension": "changed",
        "foreign.extension": "incompatible",
        "incompatible.extension": "incompatible",
        "invalid.extension": "invalid",
        "missing.extension": "missing",
        "unchanged.extension": "unchanged",
    }
    assert service.load().entries == ExtensionCatalogDocument(entries).entries


def test_validate_does_not_expose_manifest_content(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    service = _service(tmp_path)
    service.add(manifest)
    _write_manifest(manifest, "invalid secret-content")

    diagnostic = service.validate()[0]

    assert "secret-content" not in diagnostic.message
    assert "tools" not in diagnostic.message


def test_validate_does_not_classify_unknown_protocol_named_field_as_incompatible(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    service = _service(tmp_path)
    service.add(manifest)
    invalid_payload = _manifest()
    invalid_payload["protocolo_extra"] = True
    _write_manifest(manifest, invalid_payload)

    diagnostic = service.validate()[0]

    assert diagnostic.state == "invalid"
    assert diagnostic.code == "MANIFEST_INVALID"
    assert "protocolo_extra" not in diagnostic.message


def test_validate_classifies_real_protocol_change_as_incompatible(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    service = _service(tmp_path)
    service.add(manifest)
    _write_manifest(manifest, _manifest(protocol="2.0"))

    diagnostic = service.validate()[0]

    assert diagnostic.state == "incompatible"
    assert diagnostic.code == "MANIFEST_PROTOCOL_INCOMPATIBLE"


def test_validate_classifies_id_change_as_incompatible(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    service = _service(tmp_path)
    service.add(manifest)
    _write_manifest(manifest, _manifest("other.extension"))

    diagnostic = service.validate()[0]

    assert diagnostic.state == "incompatible"
    assert diagnostic.code == "MANIFEST_ID_MISMATCH"


def test_validate_redacts_manifest_tool_name(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    service = _service(tmp_path)
    service.add(manifest)
    invalid_payload = _manifest()
    invalid_payload["tools"][0]["description"] = 123  # type: ignore[index]
    invalid_payload["tools"][0]["name"] = "TOP-SECRET-NAME"  # type: ignore[index]
    _write_manifest(manifest, invalid_payload)

    diagnostic = service.validate()[0]

    assert diagnostic.state == "invalid"
    assert "TOP-SECRET-NAME" not in diagnostic.message


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["tools"][0].update(  # type: ignore[index]
            name="TOP-SECRET-NAME", description=123
        ),
        lambda payload: payload["tools"][0].update(  # type: ignore[index]
            name="demo_tool", description={"SECRET-DESCRIPTION": "CONFIDENTIAL-VALUE"}
        ),
        lambda payload: payload.update({"PRIVATE-FIELD": "CONFIDENTIAL-VALUE"}),
        lambda payload: payload["tools"][0].update(  # type: ignore[index]
            schema="PRIVATE-FIELD"
        ),
        lambda payload: payload.update({"protocol_version": "CONFIDENTIAL-VALUE"}),
        lambda payload: payload.update({"id": "CONFIDENTIAL-VALUE"}),
    ],
)
def test_validate_redacts_all_manifest_controlled_sentinels(
    tmp_path: Path, mutation
) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    service = _service(tmp_path)
    service.add(manifest)
    payload = _manifest()
    mutation(payload)
    _write_manifest(manifest, payload)

    diagnostic = service.validate()[0]
    public = json.dumps(
        {"message": diagnostic.message, "repr": repr(diagnostic), "data": asdict(diagnostic)},
        ensure_ascii=False,
    )
    assert all(
        sentinel not in public
        for sentinel in (
            "TOP-SECRET-NAME",
            "SECRET-DESCRIPTION",
            "PRIVATE-FIELD",
            "CONFIDENTIAL-VALUE",
        )
    )


def test_inspect_returns_safe_diagnostic_for_corrupt_catalog(tmp_path: Path) -> None:
    catalog = tmp_path / "extensions" / "catalog.json"
    catalog.parent.mkdir()
    catalog.write_bytes(b"not json")
    service = _service(tmp_path)

    inspection = service.inspect()

    assert inspection.document is None
    assert inspection.diagnostics[0].code == "CatalogCorruptError"
    assert "not json" not in inspection.diagnostics[0].message


def test_inspect_propagates_unexpected_programming_errors(tmp_path: Path) -> None:
    class ExplodingStorage:
        path = tmp_path / "catalog.json"

        def load(self) -> ExtensionCatalogDocument:
            raise AssertionError("programmer bug")

    service = ExtensionCatalogService(ExplodingStorage())  # type: ignore[arg-type]

    with pytest.raises(AssertionError, match="programmer bug"):
        service.inspect()


_DETERMINISTIC_CHILD = """
import sys
import time
from pathlib import Path
from agent.tools.extension_catalog_errors import CatalogLockBusyError
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage

catalog, ready, proceed, action, value, paused = sys.argv[1:]

class PausingStorage(ExtensionCatalogStorage):
    def save(self, document):
        Path(ready).write_text("ready", encoding="utf-8")
        while not Path(proceed).exists():
            time.sleep(0.005)
        super().save(document)

storage = PausingStorage(catalog) if paused == "yes" else ExtensionCatalogStorage(catalog)
service = ExtensionCatalogService(storage)
try:
    result = service.add(value) if action == "add" else service.remove(value)
except CatalogLockBusyError:
    print("BUSY")
else:
    print("DONE", result.changed)
"""


def _run_deterministic_pair(
    catalog: Path,
    first_action: str,
    first_value: Path | str,
    second_action: str,
    second_value: Path | str,
    tmp_path: Path,
) -> tuple[str, str]:
    ready = tmp_path / "writer-a-ready"
    proceed = tmp_path / "writer-a-proceed"
    first = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _DETERMINISTIC_CHILD,
            str(catalog),
            str(ready),
            str(proceed),
            first_action,
            str(first_value),
            "yes",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(2000):
            if ready.exists():
                break
            time.sleep(0.005)
        else:
            raise AssertionError("writer A did not reach the pre-promotion hook")
        second = subprocess.run(
            [
                sys.executable,
                "-c",
                _DETERMINISTIC_CHILD,
                str(catalog),
                str(ready),
                str(proceed),
                second_action,
                str(second_value),
                "no",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        proceed.write_text("continue", encoding="utf-8")
        first_output = first.communicate(timeout=15)
    finally:
        if first.poll() is None:
            proceed.write_text("continue", encoding="utf-8")
            first.kill()
            first.wait(timeout=5)
    assert first.returncode == 0, first_output
    return first_output[0].strip(), second.stdout.strip()


def test_deterministic_add_add_excludes_second_writer(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _write_manifest(first, _manifest("first.extension"))
    _write_manifest(second, _manifest("second.extension"))
    catalog = tmp_path / "catalog.json"

    assert _run_deterministic_pair(
        catalog, "add", first, "add", second, tmp_path
    ) == ("DONE True", "BUSY")
    assert [entry.extension_id for entry in ExtensionCatalogStorage(catalog).load().entries] == [
        "first.extension"
    ]


def test_deterministic_add_remove_excludes_second_writer(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _write_manifest(first, _manifest("first.extension"))
    _write_manifest(second, _manifest("second.extension"))
    catalog = tmp_path / "catalog.json"
    service = ExtensionCatalogService(ExtensionCatalogStorage(catalog))
    service.add(first)

    assert _run_deterministic_pair(
        catalog, "add", second, "remove", "first.extension", tmp_path
    ) == ("DONE True", "BUSY")
    assert [entry.extension_id for entry in service.load().entries] == [
        "first.extension",
        "second.extension",
    ]
