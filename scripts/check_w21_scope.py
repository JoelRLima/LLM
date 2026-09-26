"""Generic Git-tree-based W21 lane scope and authority checker."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.w21_architecture.authority import (  # noqa: E402
    ActiveAuthority,
    AuthorityError,
    authority_hash_targets,
    load_active_amendment,
    load_active_authority,
    sha256_bytes,
)


def _norm(path: str | Path) -> str:
    value = str(path).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _prefix(path: str, prefix: str) -> bool:
    value = _norm(path)
    owner = _norm(prefix).rstrip("/")
    if value == owner or value.startswith(owner + "/"):
        return True
    return owner.endswith(("_", "-")) and value.startswith(owner)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo: Path, *arguments: str) -> list[str]:
    command = ("git", "-c", f"safe.directory={repo}", *arguments)
    result = subprocess.run(command, cwd=repo, check=True, capture_output=True, text=True)
    return [_norm(line) for line in result.stdout.splitlines() if line.strip()]


def changed_paths(repo: Path, base_sha: str) -> set[str]:
    """Return the union of committed, staged, unstaged, and untracked paths."""

    paths: set[str] = set()
    commands = (
        ("diff", "--name-only", f"{base_sha}..HEAD"),
        ("diff", "--cached", "--name-only"),
        ("diff", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    )
    for command in commands:
        paths.update(_git(repo, *command))
    return paths


def base_tree_paths(repo: Path, base_sha: str) -> set[str]:
    return set(_git(repo, "ls-tree", "-r", "--name-only", base_sha))


def classify_paths(repo: Path, base_sha: str, paths: Iterable[str]) -> tuple[set[str], set[str]]:
    existing = base_tree_paths(repo, base_sha)
    current = {_norm(path) for path in paths}
    return current & existing, current - existing


def _canonical_lf_bytes(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _canonical_hash_matches(path: Path, expected: str) -> bool:
    try:
        raw = _canonical_lf_bytes(path.read_bytes())
    except OSError:
        return False
    return sha256_bytes(raw).lower() == expected.lower()


def _tracked(repo: Path, relative: str) -> bool:
    try:
        _git(repo, "ls-files", "--error-unmatch", "--", relative)
    except subprocess.CalledProcessError:
        return False
    return True


def _higher_authority_findings(authority: ActiveAuthority) -> list[str]:
    findings: list[str] = []
    for expected_key, path in authority_hash_targets(authority).items():
        expected = authority.higher_authority.get(expected_key)
        if not isinstance(expected, str):
            continue
        if not path.is_file() or _sha(path).lower() != expected.lower():
            findings.append(f"frozen authority hash mismatch: {path}")
    return findings


def _repo_identity_findings(authority: ActiveAuthority, repo: Path) -> list[str]:
    findings: list[str] = []
    if authority.worktree.resolve() != repo.resolve():
        findings.append("authority worktree does not match --repo")
    try:
        branch = _git(repo, "branch", "--show-current")
    except subprocess.CalledProcessError as exc:
        findings.append(f"cannot inspect branch: {exc}")
    else:
        if not branch or branch[0] != authority.branch:
            findings.append(f"branch mismatch: expected {authority.branch}")
    try:
        _git(repo, "merge-base", "--is-ancestor", authority.base_sha, "HEAD")
    except subprocess.CalledProcessError:
        findings.append("HEAD does not descend from authority base SHA")
    return findings


def _legacy_protected_sources(authority: ActiveAuthority) -> list[tuple[str, str]]:
    spawn_path_value = authority.higher_authority.get("spawn_policy_absolute_path")
    if not isinstance(spawn_path_value, str):
        return []
    path = Path(spawn_path_value)
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    values = document.get("characterization_source_protection", {}).get("sources", [])
    return [(_norm(item["path"]), str(item["sha256"])) for item in values if isinstance(item, Mapping) and "path" in item and "sha256" in item]


def _amendment_protected_sources(amendment: Mapping[str, object]) -> tuple[list[tuple[str, str]], list[str]]:
    overlay = amendment.get("overlay_changes")
    protection = overlay.get("characterization_source_protection") if isinstance(overlay, Mapping) else None
    if not isinstance(protection, Mapping):
        return [], ["active amendment characterization source protection is missing"]
    values = protection.get("canonical_sources")
    if not isinstance(values, list):
        return [], ["active amendment canonical_sources must be a list"]
    declared_count = protection.get("source_count")
    findings = []
    if not isinstance(declared_count, int) or isinstance(declared_count, bool):
        findings.append("active amendment source_count must be an integer")
    elif declared_count != len(values):
        findings.append(f"active amendment source count mismatch: declared {declared_count}, actual {len(values)}")
    sources: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, Mapping):
            findings.append("active amendment canonical source entry is not an object")
            continue
        relative = item.get("path")
        expected = item.get("canonical_lf_sha256")
        if not isinstance(relative, str) or not relative or not isinstance(expected, str) or not expected:
            findings.append("active amendment canonical source entry is incomplete")
            continue
        relative = _norm(relative)
        if relative in seen:
            findings.append(f"active amendment canonical source is duplicated: {relative}")
            continue
        seen.add(relative)
        sources.append((relative, expected))
    return sources, findings


def _protected_findings(
    repo: Path,
    authority: ActiveAuthority,
    amendment: Mapping[str, object] | None,
) -> list[str]:
    findings: list[str] = []
    source_map = _legacy_protected_sources(authority) if amendment is None else _amendment_protected_sources(amendment)[0]
    if amendment is not None:
        _, map_findings = _amendment_protected_sources(amendment)
        findings.extend(map_findings)
    for relative, expected in source_map:
        path = repo / relative
        if not _tracked(repo, relative):
            findings.append(f"protected characterization source is untracked: {relative}")
        if not path.is_file() or not _canonical_hash_matches(path, expected):
            findings.append(f"protected characterization source changed: {relative}")
    return findings


def _load_authenticated(path: Path, sha256: str | None) -> tuple[ActiveAuthority | None, list[str]]:
    if not sha256:
        return None, ["externally supplied authority SHA-256 is mandatory"]
    try:
        authority = load_active_authority(path, expected_sha256=sha256)
    except AuthorityError as exc:
        return None, [str(exc)]
    return authority, []


def lane_findings(repo: Path, authority_path: Path, authority_sha256: str | None = None) -> list[str]:
    """Return scope findings for dirty and clean post-commit worktrees."""

    repo = Path(repo).resolve()
    authority, findings = _load_authenticated(Path(authority_path), authority_sha256)
    if authority is None:
        return sorted(set(findings))
    findings.extend(_repo_identity_findings(authority, repo))
    findings.extend(_higher_authority_findings(authority))
    amendment = None
    try:
        amendment = load_active_amendment(authority)
    except AuthorityError as exc:
        findings.append(str(exc))
    paths = changed_paths(repo, authority.base_sha)
    existing, new = classify_paths(repo, authority.base_sha, paths)
    for path in sorted(existing):
        if _norm(path) not in authority.write_paths:
            findings.append(f"changed existing path outside write_paths: {path}")
    for path in sorted(new):
        if not any(_prefix(path, prefix) for prefix in authority.new_path_prefixes):
            findings.append(f"new path outside new_path_prefixes: {path}")
    for path in sorted(paths):
        if _norm(path) in authority.forbidden_paths or any(_prefix(path, prefix) for prefix in authority.forbidden_path_prefixes):
            findings.append(f"forbidden path changed: {path}")
    findings.extend(_protected_findings(repo, authority, amendment))
    findings.extend(
        f"required new path missing: {path}"
        for path in authority.required_new_paths
        if not (repo / _norm(path)).is_file()
    )
    return sorted(set(findings))


def _overlap_prefix(left: str, right: str) -> bool:
    return _prefix(left, right) or _prefix(right, left)


def _fanout_scope(authority: ActiveAuthority) -> tuple[set[str], set[str]]:
    return set(authority.write_paths), set(authority.new_path_prefixes)


def _fanout_pair_findings(left: ActiveAuthority, right: ActiveAuthority, left_label: str, right_label: str) -> list[str]:
    left_writes, left_prefixes = _fanout_scope(left)
    right_writes, right_prefixes = _fanout_scope(right)
    findings = [f"exact write overlap {left_label}/{right_label}: {path}" for path in sorted(left_writes & right_writes)]
    findings.extend(
        f"prefix overlap {left_label}/{right_label}: {left_prefix} vs {right_prefix}"
        for left_prefix in sorted(left_prefixes)
        for right_prefix in sorted(right_prefixes)
        if _overlap_prefix(left_prefix, right_prefix)
    )
    findings.extend(
        f"exact-vs-prefix overlap {left_label}/{right_label}: {path} vs {prefix}"
        for path in sorted(left_writes)
        for prefix in sorted(right_prefixes)
        if _prefix(path, prefix)
    )
    findings.extend(
        f"exact-vs-prefix overlap {left_label}/{right_label}: {path} vs {prefix}"
        for path in sorted(right_writes)
        for prefix in sorted(left_prefixes)
        if _prefix(path, prefix)
    )
    return findings


def fanout_findings(
    authorities: Iterable[tuple[Path, str]] | Iterable[Path],
) -> list[str]:
    """Authenticate every authority and verify non-overlapping ownership."""

    specs = list(authorities)
    parsed: list[tuple[str, ActiveAuthority]] = []
    findings: list[str] = []
    for item in specs:
        path, sha256 = item if isinstance(item, tuple) else (item, None)
        authority, errors = _load_authenticated(Path(path), sha256)
        findings.extend(f"{Path(path).name}: {error}" for error in errors)
        if authority is not None:
            parsed.append((Path(path).name, authority))
    for index, (left_label, left) in enumerate(parsed):
        for right_label, right in parsed[index + 1 :]:
            findings.extend(_fanout_pair_findings(left, right, left_label, right_label))
    return sorted(set(findings))


def _main() -> int:
    parser = argparse.ArgumentParser(description="Check generic W21 scope ownership")
    subparsers = parser.add_subparsers(dest="command", required=True)
    lane = subparsers.add_parser("lane")
    lane.add_argument("--repo", type=Path, required=True)
    lane.add_argument("--authority", "--epoch-authority", dest="authority", type=Path, required=True)
    lane.add_argument("--authority-sha256", required=True)
    fanout = subparsers.add_parser("fanout")
    fanout.add_argument("--authority", dest="authority", action="append", nargs=2, metavar=("PATH", "SHA"), required=True)
    arguments = parser.parse_args()
    if arguments.command == "lane":
        findings = lane_findings(arguments.repo, arguments.authority, arguments.authority_sha256)
    else:
        findings = fanout_findings([(Path(path), sha) for path, sha in arguments.authority])
    if findings:
        print("W21 scope checker: FAIL")
        print("\n".join(findings))
        return 1
    print("W21 scope checker: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
