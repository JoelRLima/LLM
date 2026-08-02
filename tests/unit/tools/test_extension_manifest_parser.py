import json

import pytest

from agent.tools.extension_manifest_parser import (
    ManifestParseError,
    ManifestProtocolError,
    ManifestStructureError,
    load_extension_manifest_bytes,
)
from agent.tools.stdio_adapter import (
    SUPPORTED_PROTOCOL,
    ExtensionManifest,
    load_extension_manifest,
)


def _payload() -> dict[str, object]:
    return {
        "id": "demo.extension",
        "version": "1.0.0",
        "protocol_version": "1.0",
        "transport": "stdio",
        "entrypoint": ["python", "demo.py"],
        "timeout_seconds": 5,
        "tools": [{"name": "demo_tool", "schema": {}}],
    }


def test_legacy_mode_accepts_nan_and_strict_mode_rejects_it() -> None:
    payload = _payload()
    payload["tools"][0]["schema"] = {"default": float("nan")}  # type: ignore[index]
    content = json.dumps(payload).encode("utf-8")

    assert load_extension_manifest_bytes(content, mode="legacy_stdio_compatibility").id == "demo.extension"
    with pytest.raises(ManifestParseError):
        load_extension_manifest_bytes(content, mode="strict_catalog")


def test_legacy_mode_preserves_unicode_decode_error() -> None:
    with pytest.raises(UnicodeDecodeError):
        load_extension_manifest_bytes(b"\xff", mode="legacy_stdio_compatibility")
    with pytest.raises(ManifestParseError):
        load_extension_manifest_bytes(b"\xff", mode="strict_catalog")


def test_legacy_mode_preserves_empty_json_error() -> None:
    with pytest.raises(json.JSONDecodeError):
        load_extension_manifest_bytes(b"", mode="legacy_stdio_compatibility")


def test_legacy_duplicate_key_policy_is_distinct_from_strict_mode() -> None:
    content = (
        b'{"id":"demo.extension","id":"other.extension",'
        b'"version":"1.0.0","protocol_version":"1.0",'
        b'"transport":"stdio","entrypoint":["python","demo.py"],'
        b'"timeout_seconds":5,"tools":[{"name":"demo_tool"}]}'
    )

    assert load_extension_manifest_bytes(content, mode="legacy_stdio_compatibility").id == "other.extension"
    with pytest.raises(ManifestStructureError):
        load_extension_manifest_bytes(content, mode="strict_catalog")


def test_protocol_error_is_typed() -> None:
    payload = _payload()
    payload["protocol_version"] = "2.0"

    with pytest.raises(ManifestProtocolError):
        load_extension_manifest_bytes(json.dumps(payload).encode("utf-8"))


def test_gate1_module_still_exports_protocol_and_path_wrapper(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_payload()), encoding="utf-8")

    assert SUPPORTED_PROTOCOL == "1.0"
    result = load_extension_manifest(manifest_path)
    assert type(result) is ExtensionManifest
    assert type(result).__module__ == "agent.tools.stdio_adapter"
    assert isinstance(result, ExtensionManifest)
    assert result.id == "demo.extension"


def test_gate1_path_api_preserves_nan_and_duplicate_key_behavior(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    payload = _payload()
    payload["tools"][0]["schema"] = {"default": float("nan")}  # type: ignore[index]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_extension_manifest(manifest_path).id == "demo.extension"

    manifest_path.write_bytes(
        b'{"id":"first.extension","id":"second.extension",'
        b'"version":"1.0.0","protocol_version":"1.0",'
        b'"transport":"stdio","entrypoint":["python"],'
        b'"timeout_seconds":5,"tools":[{"name":"demo_tool"}]}'
    )
    assert load_extension_manifest(manifest_path).id == "second.extension"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            {"protocol_version": "2.0"},
            "Versão de protocolo não suportada",
        ),
        (
            {"unexpected": True},
            "Campos desconhecidos em manifest: unexpected",
        ),
        (
            {"id": None},
            "Campo 'id' invalido no manifest",
        ),
    ],
)
def test_gate1_path_api_preserves_historical_value_error_messages(
    tmp_path, mutation: dict[str, object], message: str
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({**_payload(), **mutation}), encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        load_extension_manifest(manifest_path)

    assert type(caught.value) is ValueError
    assert str(caught.value) == message


def test_gate1_path_api_preserves_unicode_decode_error(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b"\xff")

    with pytest.raises(UnicodeDecodeError):
        load_extension_manifest(manifest_path)


def test_gate1_path_api_preserves_root_and_incomplete_messages(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="Manifest de extensão deve ser um objeto JSON"):
        load_extension_manifest(manifest_path)

    manifest_path.write_text(json.dumps({"id": "demo.extension"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Manifest de extensão incompleto"):
        load_extension_manifest(manifest_path)
