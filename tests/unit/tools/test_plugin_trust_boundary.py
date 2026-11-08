"""The trusted stdio profile must never be mistaken for an OS sandbox."""
import sys

from llm_agent.agent.tools.contracts import ToolInvocation
from llm_agent.agent.tools.stdio_adapter import ExtensionManifest, StdioToolAdapter


def test_trusted_plugin_can_read_os_accessible_data_outside_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("controlled-test-sentinel")
    monkeypatch.setenv("AGENT_TRUST_TEST_SENTINEL", "controlled-env-sentinel")
    script = tmp_path / "plugin.py"
    script.write_text(
        "import json,os,pathlib,sys\n"
        "request=json.load(sys.stdin)\n"
        "print(json.dumps({'invocation_id':request['invocation_id'],'status':'succeeded','data':{'outside':pathlib.Path(sys.argv[1]).read_text(),"
        "'environment':os.environ.get('AGENT_TRUST_TEST_SENTINEL')}}))\n"
    )
    manifest = ExtensionManifest(id="trusted-boundary", version="1", protocol_version="1.0", transport="stdio",
        entrypoint=(sys.executable, str(script), str(outside)), timeout_seconds=5,
        tools=({"name": "inspect", "schema": {"type": "object"}, "capabilities": ["process", "read"]},))
    result = StdioToolAdapter(manifest, cwd=workspace).invoke(ToolInvocation(tool_name="inspect", args={}))
    assert result.ok
    assert "controlled-test-sentinel" in str(result.data)
    assert result.data["environment"] is None  # Existing safe-environment allowlist remains enforced.
