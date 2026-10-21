"""Coarse read-only observations of a fresh disposable install root; no payload walk."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

STATES = frozenset({"PREPARED", "STAGED", "LAUNCHER_PROMOTED", "PATH_MUTATED", "FRESH_SHELL_VERIFIED",
                    "COMMITTED", "ROLLING_BACK", "ROLLED_BACK", "FAILED_RECOVERY"})


def safe_path(root: Path, target: Path) -> bool:
    """Check every existing ancestor before touching the next component; reject links."""
    if (not root.is_absolute() or not target.is_absolute() or not target.is_relative_to(root)
            or ".." in root.parts or ".." in target.parts):
        raise ValueError("Observation outside authorized root")
    present = True
    for item in [*reversed(target.parents), target]:
        if not present:
            continue
        try:
            info = item.lstat()
        except FileNotFoundError:
            present = False
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Reparse observation rejected")
        if item != target and not stat.S_ISDIR(info.st_mode):
            raise ValueError("Non-directory observation ancestor")
    return present


class Milestones:
    def __init__(self, root: Path, authorized: Path) -> None:
        if root != authorized:
            raise ValueError("Invalid disposable observation root")
        if safe_path(authorized, root) and not root.is_dir():
            raise ValueError("Observation root is not a directory")
        self.root = root

    def exists(self, relative: str) -> bool:
        return safe_path(self.root, self.root / relative)

    def journal(self) -> str:
        path = self.root / "transactions/current.journal.json"
        if not safe_path(self.root, path):
            return "ABSENT"
        if not path.is_file() or path.stat().st_size > 65536:
            raise ValueError("Invalid journal observation")
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("Journal observation cap exceeded")
        value = json.loads(raw.decode("utf-8-sig"))
        state = value.get("state") if isinstance(value, dict) else None
        if state not in STATES:
            raise ValueError("Unknown journal state")
        return str(state)  # Never serialize journal paths, PATH snapshots, or arbitrary fields.

    def transactions(self) -> list[dict[str, bool]]:
        parent = self.root / "transactions"
        if not safe_path(self.root, parent):
            return []
        result = []
        with os.scandir(parent) as entries:
            for count, entry in enumerate(entries):
                if count >= 32:
                    raise ValueError("Transaction observation cap exceeded")
                if not re.fullmatch(r"w18-install-[0-9]{17}-[a-f0-9]{12}", entry.name):
                    continue
                relative = "transactions/" + entry.name + "/candidate-stage"
                result.append({"stage": self.exists(relative), "stage_runtime": self.exists(relative + "/runtime"),
                               "stage_launcher": self.exists(relative + "/bin/llm-agent.cmd")})
        return result

    def sample(self) -> dict[str, Any]:
        return {"install_root_present": self.exists("."), "journal_state": self.journal(),
                "receipt_present": self.exists("current-install.json"), "stable_present": self.exists("bin/llm-agent.cmd"),
                "transactions": self.transactions(), "interpretation": "MEASURED namespace; phase UNKNOWN"}
