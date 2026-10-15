"""Record and compare tracked source identities without refreshing the Git index."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any


def capture(root: Path) -> dict[str, Any]:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    raw = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root, env=env)
    files = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        header, path = entry.split(b"\t", 1)
        mode, oid, stage = header.decode().split()
        if stage != "0":
            raise ValueError("unmerged source index")
        relative = path.decode("utf-8")
        data = (root / relative).read_bytes()
        actual_oid = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if actual_oid != oid:
            raise ValueError("Git checkout changed authenticated bytes: " + relative)
        files.append({"path": relative, "mode": mode, "sha256": hashlib.sha256(data).hexdigest()})
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, env=env).decode().strip()
    return {"source_tree": tree, "files": files}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["before", "after"])
    args = parser.parse_args()
    evidence = Path(os.environ["RUNNER_TEMP"]) / "w23-evidence"
    evidence.mkdir(exist_ok=True)
    data = capture(Path.cwd())
    (evidence / ("source_" + args.phase + ".json")).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if args.phase == "after" and json.loads((evidence / "source_before.json").read_bytes()) != data:
        raise ValueError("tracked source changed during acceptance")
    print(json.dumps({"state": "OBSERVED PASS", "source_tree": data["source_tree"], "files": len(data["files"])}))


if __name__ == "__main__":
    main()
