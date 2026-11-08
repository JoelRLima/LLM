"""Capture a reviewable source checkpoint without changing Git or reading local secrets."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOURCE_ROOTS = frozenset({"src", "tests", "scripts", "docs", "quality", "distribution", "installer", "examples", ".github"})
SOURCE_FILES = frozenset({"README.md", "CONTRIBUTING.md", "pyproject.toml", "requirements.lock", "requirements-ci.lock",
                          "Workbench.cmd", "Gerenciar_Workbench.ps1", "config.example.json", ".gitignore",
                          ".gitattributes", ".editorconfig"})


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *arguments])


def checkpoint(root: Path, output: Path, *, snapshot: bool = False) -> dict[str, Any]:
    root = root.resolve(strict=True)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    paths = sorted(set(git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").decode("utf-8").split("\0")) - {""})
    rows: list[dict[str, Any]] = []
    skipped = []
    archive = zipfile.ZipFile(output / "source.zip", "x", compression=zipfile.ZIP_DEFLATED) if snapshot else None
    try:
        for name in paths:
            relative = Path(name)
            if relative.parts[0] not in SOURCE_ROOTS and name not in SOURCE_FILES:
                skipped.append(name)
                continue
            path = root / relative
            if path.is_symlink() or path.resolve().is_relative_to(root) is False:
                raise ValueError(f"Unsafe checkpoint input: {name}")
            if not path.exists():
                rows.append({"path": name, "state": "deleted"})
                continue
            if not path.is_file():
                raise ValueError(f"Non-regular checkpoint input: {name}")
            content = path.read_bytes()
            rows.append({"path": name, "group": relative.parts[0], "size": len(content),
                         "sha256": hashlib.sha256(content).hexdigest()})
            if archive is not None:
                archive.writestr(name, content)
    finally:
        if archive is not None:
            archive.close()
    fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    document = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "head": git(root, "rev-parse", "HEAD").decode().strip(), "source_sha256": fingerprint,
                "files": rows, "excluded_paths": skipped, "snapshot": snapshot,
                "scope": "allowlisted source; excludes local state, prototypes and ignored secrets"}
    (output / "manifest.json").write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "git-status.z").write_bytes(git(root, "status", "--porcelain=v1", "-z"))
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True, help="New directory; never overwritten")
    parser.add_argument("--snapshot", action="store_true", help="Also copy allowlisted source bytes into source.zip")
    args = parser.parse_args()
    result = checkpoint(args.root, args.output, snapshot=args.snapshot)
    print(json.dumps({"source_sha256": result["source_sha256"], "files": len(result["files"]), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
