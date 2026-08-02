import json
import os
from pathlib import Path

import pytest

from agent.skills.code_analyzer import CodeAnalyzerSkill
from agent.skills.grep import GrepSkill


def _external_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        if os.name == "nt":
            pytest.skip(f"Criação de symlink não permitida neste Windows: {exc}")
        raise


def test_grep_skips_file_symlink_that_escapes_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "safe.txt").write_text(
        "SAFE_MATCH\n",
        encoding="utf-8",
    )
    external = outside / "secret.txt"
    external.write_text("EXTERNAL_SECRET\n", encoding="utf-8")
    _external_symlink(workspace / "linked.txt", external)

    result = GrepSkill(str(workspace)).execute(
        {"pattern": "MATCH|EXTERNAL_SECRET", "path": ".", "recursive": True}
    )

    rendered = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is True
    assert result["total_matches"] == 1
    assert result["data"][0]["file"] == "safe.txt"
    assert "EXTERNAL_SECRET" not in rendered
    assert str(outside) not in rendered


def test_code_analyzer_skips_python_symlink_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "safe.py").write_text(
        "def safe():\n    return True\n",
        encoding="utf-8",
    )
    external = outside / "secret.py"
    external.write_text(
        "EXTERNAL_SECRET = 'do-not-read'\n",
        encoding="utf-8",
    )
    _external_symlink(workspace / "linked.py", external)

    result = CodeAnalyzerSkill(str(workspace)).execute(
        {
            "target": ".",
            "mode": "directory",
            "include_code": True,
        }
    )

    rendered = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is True
    assert set(result["data"]["files"]) == {"safe.py"}
    assert "EXTERNAL_SECRET" not in rendered
    assert str(outside) not in rendered
