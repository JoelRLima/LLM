"""Pinned build inputs for the disposable runner, never for the developer host."""
from __future__ import annotations

import argparse
import os
import urllib.request
from pathlib import Path

from scripts.w23_diagnostics.evidence import digest, write_json

ASSETS = {
    "python": (
        "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/"
        "cpython-3.12.14+20260901-x86_64-pc-windows-msvc-install_only.tar.gz",
        "e90c1b6419da3bd812dd73bb3de40287a21abf153438147639ec5e20375ea93f",
    ),
    "uv": (
        "https://github.com/astral-sh/uv/releases/download/0.12.13/uv-x86_64-pc-windows-msvc.zip",
        "a86c9dc7bad9b03f388583b7187c05fe9951c2e0d392217e8fd43d97787f6ec2",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset", choices=ASSETS)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted" or os.name != "nt":
        raise RuntimeError("disposable Windows runner required")
    url, expected = ASSETS[args.asset]
    with urllib.request.urlopen(url, timeout=20) as response, args.output.open("xb") as stream:
        size = 0
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            if size > 512 * 1024 * 1024:
                raise ValueError("pinned input exceeds size cap")
            stream.write(block)
    actual = digest(args.output)
    write_json(args.output.with_suffix(".identity.json"), {"sha256": actual, "expected": expected, "bytes": size})
    return 0 if actual == expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
