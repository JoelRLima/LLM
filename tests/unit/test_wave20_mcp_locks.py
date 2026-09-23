from __future__ import annotations

from pathlib import Path

from distribution.mcp_lockfiles import validate_mcp_union_lock

_HASH = "0" * 64


def _write_lock(path: Path, requirements: tuple[tuple[str, str], ...]) -> None:
    lines = ["--only-binary :all:", ""]
    for name, version in requirements:
        lines.extend((f"{name}=={version} \\", f"    --hash=sha256:{_HASH}"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_mcp_union_accepts_exact_integer_versions(tmp_path: Path) -> None:
    base = tmp_path / "base.lock"
    union = tmp_path / "union.lock"
    _write_lock(base, (("base-package", "1.0"),))
    _write_lock(
        union,
        (("base-package", "1.0"), ("mcp", "2.2.0"), ("pywin32", "312")),
    )

    summary = validate_mcp_union_lock(base, union)

    assert "pywin32" in summary.packages
