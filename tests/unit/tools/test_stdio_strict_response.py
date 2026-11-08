from pathlib import Path

import pytest

from llm_agent.agent.tools.contracts import ToolResult, ToolStatus
from llm_agent.agent.tools.stdio_adapter import StdioToolAdapter, load_extension_manifest


@pytest.mark.parametrize(
    "raw",
    [
        '{"invocation_id":"other","invocation_id":"call","status":"succeeded"}',
        '{"invocation_id":"call","data":{"x":1,"x":2}}',
        '{"invocation_id":"call","data":NaN}',
        '{"invocation_id":"call","data":Infinity}',
        '{"invocation_id":"call","data":-Infinity}',
        '{"invocation_id":"call","data":1e9999}',
    ],
)
def test_external_responses_reject_ambiguous_or_non_finite_json(raw):
    path = Path(__file__).resolve().parents[3] / "examples/extensions/demo_extension/manifest.json"
    adapter = StdioToolAdapter(load_extension_manifest(path))
    result = adapter._decode_response(raw, "call")
    assert isinstance(result, ToolResult)
    assert result.status is ToolStatus.PROTOCOL_ERROR
    assert result.data is None
    assert result.error.code == "INVALID_RESPONSE"
