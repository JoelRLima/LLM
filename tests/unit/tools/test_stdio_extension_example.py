import json
import subprocess
import sys
from pathlib import Path


def test_demo_extension_example_runs_via_stdio(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3] / "examples" / "extensions" / "demo_extension"
    manifest_path = root / "manifest.json"
    script_path = root / "demo_extension.py"

    payload = {
        "tool": "demo_tool",
        "args": {"text": "hello"},
        "invocation_id": "demo-1",
    }

    process = subprocess.run(
        [sys.executable, str(script_path)],
        input=json.dumps(payload),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        cwd=str(root),
    )

    assert process.returncode == 0
    response = json.loads(process.stdout)
    assert response["status"] == "succeeded"
    assert response["invocation_id"] == payload["invocation_id"]
    assert response["data"]["echo"] == "hello"
    assert manifest_path.exists()
