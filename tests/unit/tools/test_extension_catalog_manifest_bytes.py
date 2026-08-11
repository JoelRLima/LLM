import json

import pytest

from agent.tools.stdio_adapter import load_extension_manifest_bytes


def _payload() -> dict[str, object]:
    return {
        "id": "demo.extension",
        "version": "1.0.0",
        "protocol_version": "1.0",
        "transport": "stdio",
        "entrypoint": ["python", "demo.py"],
        "timeout_seconds": 5,
        "tools": [{"name": "demo_tool", "schema": {}, "capabilities": ["read", "process"]}],
    }


def test_manifest_bytes_parser_validates_exact_bytes() -> None:
    content = json.dumps(_payload(), ensure_ascii=False).encode("utf-8")

    manifest = load_extension_manifest_bytes(content)

    assert manifest.id == "demo.extension"
    assert manifest.protocol_version == "1.0"


@pytest.mark.parametrize("content", [b"", b"\xff", b"\xef\xbb\xbf{}", b"not-json"])
def test_manifest_bytes_parser_rejects_encoding_and_json_errors(content: bytes) -> None:
    with pytest.raises((TypeError, ValueError)):
        load_extension_manifest_bytes(content)


def test_manifest_bytes_parser_rejects_duplicate_nested_keys() -> None:
    content = (
        b'{"id":"demo.extension","id":"other.extension",'
        b'"version":"1.0.0","protocol_version":"1.0",'
        b'"transport":"stdio","entrypoint":["python","demo.py"],'
        b'"timeout_seconds":5,"tools":[{"name":"demo_tool"}]}'
    )

    with pytest.raises(ValueError, match="duplicada"):
        load_extension_manifest_bytes(content)


def test_manifest_bytes_parser_does_not_coerce_non_bytes() -> None:
    with pytest.raises(TypeError):
        load_extension_manifest_bytes("{}")  # type: ignore[arg-type]
