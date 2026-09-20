from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.outputs.models import (
    OUTPUT_PAYLOAD_CORRUPT,
    OUTPUT_STORE_UNSAFE,
    OutputArtifact,
    OutputKind,
    OutputSource,
    OutputStoreError,
    payload_digest,
)
from agent.outputs.store import OutputStore


def _artifact(text: str, output_id: str = "out-" + "a" * 32) -> OutputArtifact:
    digest, byte_count, char_count, lines = payload_digest(text)
    return OutputArtifact(
        schema_version=1,
        output_id=output_id,
        kind=OutputKind.TEXT,
        source=OutputSource.OTHER_PUBLIC,
        title="test",
        created_at="2026-01-01T00:00:00+00:00",
        media_type="text/plain; charset=utf-8",
        payload_sha256=digest,
        payload_bytes=byte_count,
        char_count=char_count,
        line_count=lines,
        source_truncated=False,
        redaction_applied=False,
        run_id=None,
        action_id=None,
        metadata={},
    )


def test_commit_reloads_metadata_and_verifies_payload(tmp_path: Path) -> None:
    store = OutputStore(tmp_path / "outputs")
    artifact = store.commit(_artifact("á\n🙂"), "á\n🙂")
    assert artifact.output_id.startswith("out-")
    assert store.read(artifact.output_id) == "á\n🙂"
    assert store.list()[0] == artifact
    metadata = json.loads((tmp_path / "outputs" / f"{artifact.output_id}.meta.json").read_text())
    assert set(metadata) == {
        "schema_version", "output_id", "kind", "source", "title", "created_at", "media_type",
        "payload_sha256", "payload_bytes", "char_count", "line_count", "source_truncated",
        "redaction_applied", "run_id", "action_id", "metadata",
    }


def test_hash_mismatch_is_explicit_and_orphan_payload_is_not_committed(tmp_path: Path) -> None:
    store = OutputStore(tmp_path / "outputs")
    artifact = store.commit(_artifact("payload"), "payload")
    payload = tmp_path / "outputs" / f"{artifact.output_id}.payload.txt"
    payload.write_text("tampered", encoding="utf-8")
    with pytest.raises(OutputStoreError) as error:
        store.read(artifact.output_id)
    assert error.value.reason_code == OUTPUT_PAYLOAD_CORRUPT

    orphan_root = tmp_path / "orphan-outputs"
    orphan_root.mkdir()
    orphan_store = OutputStore(orphan_root)
    orphan = orphan_root / ("out-" + "b" * 32 + ".payload.txt")
    orphan.write_text("orphan", encoding="utf-8")
    assert orphan_store.list() == ()


def test_foreign_entry_fails_closed_for_mutation(tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    root.mkdir()
    (root / "foreign.txt").write_text("do not touch", encoding="utf-8")
    with pytest.raises(OutputStoreError) as error:
        OutputStore(root).commit(_artifact("payload"), "payload")
    assert error.value.reason_code == OUTPUT_STORE_UNSAFE
    assert (root / "foreign.txt").read_text(encoding="utf-8") == "do not touch"
