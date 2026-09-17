"""Bind exact native-ConPTY evidence files into one authority artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.conpty_authority import (  # noqa: E402
    ConPtyAuthorityError,
    build_conpty_authority_evidence,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-product-summary", type=Path, required=True)
    parser.add_argument("--installed-interactive", type=Path, required=True)
    parser.add_argument("--layer-matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        authority = build_conpty_authority_evidence(
            args.installed_product_summary,
            args.installed_interactive,
            args.layer_matrix,
            args.output,
        )
    except ConPtyAuthorityError as exc:
        print(f"CONPTY_AUTHORITY=FAIL ({exc})", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "passed",
                "authority": str(args.output),
                "authority_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                "identity": authority["evidence_identity"],
                "source_evidence": authority["source_evidence"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
