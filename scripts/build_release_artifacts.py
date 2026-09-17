"""Compatibility entry point for the W18 v003 release builder.

The implementation lives in ``build_windows_payload.py`` so there is one
release-build owner for the epoch, wheel, payload, RECORD, and ZIP surfaces.
This historical path remains callable by existing workflow and operator
commands.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_windows_payload import BuildError, BuildResult, build_release, main  # noqa: E402

__all__ = ["BuildError", "BuildResult", "build_release", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
