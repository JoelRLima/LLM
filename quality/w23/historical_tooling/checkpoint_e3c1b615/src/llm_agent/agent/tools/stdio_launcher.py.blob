"""Direct compatibility facade for the Platform stdio session launcher."""

from __future__ import annotations

import sys
from pathlib import Path

# Preserve the historical direct-file bootstrap contract.  When this facade is
# invoked by absolute path, Python starts with ``.../agent/tools`` on sys.path,
# not the source/install directory that contains the ``llm_agent`` package.
if __name__ == "__main__" and not __package__:
    package_parent = Path(__file__).resolve().parents[3]
    if str(package_parent) not in sys.path:
        sys.path.insert(0, str(package_parent))

from llm_agent.extensions.stdio_launcher import (
    LAUNCHER_PROTOCOL,
    MAX_ENVELOPE_BYTES,
    MAX_STATUS_BYTES,
    MAX_STATUS_MESSAGE_BYTES,
    _validate_envelope,
    build_launcher_envelope,
    create_status_file,
    launcher_status_error,
    launcher_status_failure,
    main,
    prepare_launcher,
    read_launcher_status,
    remove_status_file,
)

__all__ = [
    "_validate_envelope",
    "LAUNCHER_PROTOCOL",
    "MAX_ENVELOPE_BYTES",
    "MAX_STATUS_BYTES",
    "MAX_STATUS_MESSAGE_BYTES",
    "build_launcher_envelope",
    "create_status_file",
    "launcher_status_error",
    "launcher_status_failure",
    "main",
    "prepare_launcher",
    "read_launcher_status",
    "remove_status_file",
]

if __name__ == "__main__":
    raise SystemExit(main())
