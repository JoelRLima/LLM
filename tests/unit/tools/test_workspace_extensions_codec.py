import json

import pytest

from agent.tools.extension_catalog_errors import (
    WorkspaceCodecError,
    WorkspaceSchemaError,
    WorkspaceVersionError,
)
from agent.tools.extension_state import WorkspaceExtensionSelection, WorkspaceExtensionsState
from agent.tools.workspace_extensions_codec import (
    decode_workspace_extensions,
    encode_workspace_extensions,
)


def test_empty_round_trip_is_deterministic() -> None:
    state = WorkspaceExtensionsState(
        (
            WorkspaceExtensionSelection("beta.extension", ("write", "read"), False),
            WorkspaceExtensionSelection("alpha.extension"),
        )
    )
    encoded = encode_workspace_extensions(state)
    assert encoded == encode_workspace_extensions(decode_workspace_extensions(encoded))
    assert encoded.decode().endswith("\n")
    assert list(json.loads(encoded)["extensions"]) == ["alpha.extension", "beta.extension"]


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"   ",
        b"\xef\xbb\xbf{}",
        b"\xff",
        b"{",
        b'{"schema_version": true, "extensions": {}}',
        b'{"schema_version": 2, "extensions": {}}',
        b'{"schema_version": 1, "extensions": []}',
        b'{"schema_version": 1, "extensions": {"demo.extension": {"enabled": true}}}',
        b'{"schema_version": 1, "extensions": {"demo.extension": {"enabled": 1, "grants": []}}}',
        b'{"schema_version": 1, "extensions": {"demo.extension": {"enabled": true, "grants": "read"}}}',
        b'{"schema_version": 1, "extensions": {"demo.extension": {"enabled": true, "grants": ["read", "read"]}}}',
        b'{"schema_version": 1, "extensions": {"Demo": {"enabled": true, "grants": []}}}',
        b'{"schema_version": 1, "extensions": {"demo.extension": {"enabled": true, "grants": [], "extra": 1}}}',
        b'{"schema_version": 1, "extensions": {"demo.extension": {"enabled": true, "grants": [""]}}}',
    ],
)
def test_invalid_workspace_documents_fail_closed(payload: bytes) -> None:
    with pytest.raises((WorkspaceCodecError, WorkspaceSchemaError, WorkspaceVersionError)):
        decode_workspace_extensions(payload)


def test_duplicate_json_keys_are_rejected() -> None:
    payload = b'{"schema_version":1,"schema_version":1,"extensions":{}}'
    with pytest.raises(WorkspaceSchemaError, match="duplicada"):
        decode_workspace_extensions(payload)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":1,"extensions":{},"SUPER_SECRET_SENTINEL":1}',
        b'{"schema_version":1,"SUPER_SECRET_SENTINEL":1,"SUPER_SECRET_SENTINEL":2,"extensions":{}}',
        b'{"schema_version":1,"extensions":{"demo.extension":{"enabled":true,"grants":[]},"TOP-SECRET-NAME":1}}',
    ],
)
def test_codec_errors_redact_controlled_field_names(payload: bytes) -> None:
    with pytest.raises(WorkspaceCodecError) as caught:
        decode_workspace_extensions(payload)
    assert "SECRET" not in str(caught.value).upper()
    assert "SECRET" not in repr(caught.value).upper()


def test_orphan_reference_is_accepted_and_immutable() -> None:
    state = decode_workspace_extensions(
        b'{"schema_version":1,"extensions":{"missing.extension":{"enabled":true,"grants":["read"]}}}'
    )
    assert state.get("missing.extension") is not None
    assert state.get("missing.extension").granted_capabilities == ("read",)
    with pytest.raises(AttributeError):
        state.selections.append(None)  # type: ignore[attr-defined]
