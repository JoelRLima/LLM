"""Remote-safe tests for authenticated generic scope ownership."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts.check_w21_scope import _canonical_hash_matches, _canonical_lf_bytes, fanout_findings, lane_findings
from scripts.w21_architecture.authority import AuthorityError, load_active_amendment, load_active_authority


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-c", f"safe.directory={repo}", *arguments),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository(
    tmp_path: Path,
    *,
    initial_files: dict[str, str] | None = None,
) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=lane")
    _git(repo, "config", "user.email", "w21-tests@example.invalid")
    _git(repo, "config", "user.name", "W21 tests")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    for relative, contents in (initial_files or {}).items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    _git(repo, "add", "--", "README.md", *(initial_files or {}))
    _git(repo, "commit", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _authority(
    location: Path,
    repo: Path,
    base_sha: str,
    *,
    write_paths: list[str],
    new_path_prefixes: list[str],
    forbidden_paths: list[str] | None = None,
    higher_authority: dict[str, object] | None = None,
    branch: str = "lane",
) -> tuple[Path, str]:
    document = {
        "artifact": "W21_CORRECTIVE_AUTHORITY",
        "status": "FROZEN",
        "corrective_id": "W99-X-C001",
        "base": {"sha": base_sha, "tree": "future-tree"},
        "worktree": {"absolute_path": str(repo), "branch": branch},
        "write_paths": write_paths,
        "new_path_prefixes": new_path_prefixes,
        "required_outputs": [],
        "forbidden_paths": forbidden_paths or [],
        "forbidden_path_prefixes": [],
        "required_transition_violation_removals": [],
        "accepted_removed_transition_violation_ids": [],
        "higher_authority": higher_authority or {},
    }
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    location.mkdir(parents=True, exist_ok=True)
    path = location / "authority.json"
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def _amendment(location: Path, base_sha: str, *, source_path: str = "protected.py") -> tuple[Path, str]:
    document = {
        "artifact": "W21_AUTHORITY_AMENDMENT",
        "status": "FROZEN",
        "amendment_id": "AMENDMENT-002",
        "effective_from_sha": base_sha,
        "overlay_changes": {
            "characterization_source_protection": {
                "source_count": 1,
                "canonical_sources": [
                    {
                        "path": source_path,
                        "canonical_lf_sha256": hashlib.sha256(b"base\n").hexdigest(),
                    }
                ],
            }
        },
    }
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path = location / "amendment.json"
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def test_lane_scope_accepts_allowed_existing_and_new_paths(tmp_path: Path) -> None:
    repo, base_sha = _repository(tmp_path)
    authority, sha = _authority(
        tmp_path,
        repo,
        base_sha,
        write_paths=["README.md"],
        new_path_prefixes=["scripts/check_w21_"],
    )
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts/check_w21_future.py").write_text("pass\n", encoding="utf-8")
    assert lane_findings(repo, authority, sha) == []


def test_lane_scope_includes_allowed_staged_modifications(tmp_path: Path) -> None:
    repo, base_sha = _repository(tmp_path)
    authority, sha = _authority(
        tmp_path,
        repo,
        base_sha,
        write_paths=["README.md"],
        new_path_prefixes=[],
    )
    (repo / "README.md").write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    assert lane_findings(repo, authority, sha) == []


def test_lane_scope_rejects_wrong_sha_and_forbidden_paths(tmp_path: Path) -> None:
    repo, base_sha = _repository(tmp_path)
    authority, sha = _authority(
        tmp_path,
        repo,
        base_sha,
        write_paths=["README.md"],
        new_path_prefixes=[],
        forbidden_paths=["quality/baseline.json"],
    )
    assert any("SHA-256 mismatch" in item for item in lane_findings(repo, authority, "0" * 64))
    (repo / "quality").mkdir()
    (repo / "quality/baseline.json").write_text("{}\n", encoding="utf-8")
    findings = lane_findings(repo, authority, sha)
    assert any("forbidden path changed: quality/baseline.json" in item for item in findings)


def test_lane_scope_rejects_protected_source_mutation(tmp_path: Path) -> None:
    repo, base_sha = _repository(tmp_path)
    protected_hash = hashlib.sha256((repo / "README.md").read_bytes()).hexdigest()
    spawn_policy = tmp_path / "spawn.json"
    spawn_policy.write_text(
        json.dumps(
            {
                "characterization_source_protection": {
                    "sources": [{"path": "README.md", "sha256": protected_hash}]
                }
            }
        ),
        encoding="utf-8",
    )
    authority, sha = _authority(
        tmp_path,
        repo,
        base_sha,
        write_paths=["README.md"],
        new_path_prefixes=[],
        higher_authority={"spawn_policy_absolute_path": str(spawn_policy)},
    )
    (repo / "README.md").write_text("mutated\n", encoding="utf-8")
    findings = lane_findings(repo, authority, sha)
    assert "protected characterization source changed: README.md" in findings


def test_amendment_canonicalization_accepts_lf_crlf_and_mixed_eol(tmp_path: Path) -> None:
    expected = hashlib.sha256(b"one\ntwo\n").hexdigest()
    for index, raw in enumerate((b"one\ntwo\n", b"one\r\ntwo\r\n", b"one\r\ntwo\n")):
        path = tmp_path / f"source-{index}.py"
        path.write_bytes(raw)
        assert _canonical_hash_matches(path, expected)
    changed = tmp_path / "changed.py"
    changed.write_bytes(b"one\r\ntres\n")
    assert _canonical_lf_bytes(changed.read_bytes()) != b"one\ntwo\n"
    assert not _canonical_hash_matches(changed, expected)


def test_active_amendment_sha_and_tampering_are_rejected(tmp_path: Path) -> None:
    repo, base_sha = _repository(tmp_path)
    amendment, amendment_sha = _amendment(tmp_path, base_sha)
    authority, authority_sha = _authority(
        tmp_path,
        repo,
        base_sha,
        write_paths=[],
        new_path_prefixes=[],
        higher_authority={
            "active_amendment_absolute_path": str(amendment),
            "active_amendment_sha256": amendment_sha,
        },
    )
    active = load_active_authority(authority, expected_sha256=authority_sha)
    assert load_active_amendment(active)["amendment_id"] == "AMENDMENT-002"
    amendment.write_bytes(amendment.read_bytes() + b"\n")
    with pytest.raises(AuthorityError, match="active amendment SHA-256 mismatch"):
        load_active_amendment(active)

    wrong_authority, wrong_sha = _authority(
        tmp_path / "wrong",
        repo,
        base_sha,
        write_paths=[],
        new_path_prefixes=[],
        higher_authority={
            "active_amendment_absolute_path": str(amendment),
            "active_amendment_sha256": "0" * 64,
        },
    )
    wrong_active = load_active_authority(wrong_authority, expected_sha256=wrong_sha)
    with pytest.raises(AuthorityError, match="active amendment SHA-256 mismatch"):
        load_active_amendment(wrong_active)


def test_missing_and_untracked_amendment_sources_fail_without_checkout_fallback(tmp_path: Path) -> None:
    repo, base_sha = _repository(tmp_path)
    amendment, amendment_sha = _amendment(tmp_path, base_sha)
    authority, authority_sha = _authority(
        tmp_path,
        repo,
        base_sha,
        write_paths=[],
        new_path_prefixes=["protected.py"],
        higher_authority={
            "active_amendment_absolute_path": str(amendment),
            "active_amendment_sha256": amendment_sha,
        },
    )
    missing = lane_findings(repo, authority, authority_sha)
    assert any("protected characterization source is untracked: protected.py" in item for item in missing)
    assert any("protected characterization source changed: protected.py" in item for item in missing)

    (repo / "protected.py").write_text("base\n", encoding="utf-8")
    untracked = lane_findings(repo, authority, authority_sha)
    assert any("protected characterization source is untracked: protected.py" in item for item in untracked)


def test_generic_amendment_source_count_accepts_authenticated_non_61_count(tmp_path: Path) -> None:
    repo, base_sha = _repository(tmp_path, initial_files={"protected.py": "base\n"})
    amendment, amendment_sha = _amendment(tmp_path, base_sha)
    authority, authority_sha = _authority(
        tmp_path,
        repo,
        base_sha,
        write_paths=[],
        new_path_prefixes=[],
        higher_authority={
            "active_amendment_absolute_path": str(amendment),
            "active_amendment_sha256": amendment_sha,
        },
    )
    findings = lane_findings(repo, authority, authority_sha)
    assert findings == []


def test_generic_amendment_source_count_mismatch_is_rejected(tmp_path: Path) -> None:
    repo, base_sha = _repository(tmp_path, initial_files={"protected.py": "base\n"})
    amendment, _ = _amendment(tmp_path, base_sha)
    document = json.loads(amendment.read_text(encoding="utf-8"))
    document["overlay_changes"]["characterization_source_protection"]["source_count"] = 2
    amendment.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    amendment_sha = hashlib.sha256(amendment.read_bytes()).hexdigest()
    authority, authority_sha = _authority(
        tmp_path,
        repo,
        base_sha,
        write_paths=[],
        new_path_prefixes=[],
        higher_authority={
            "active_amendment_absolute_path": str(amendment),
            "active_amendment_sha256": amendment_sha,
        },
    )
    findings = lane_findings(repo, authority, authority_sha)
    assert any("source count mismatch: declared 2, actual 1" in item for item in findings)


def test_fanout_detects_exact_prefix_and_exact_vs_prefix_overlap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    first, first_sha = _authority(
        tmp_path / "first",
        repo,
        "base-one",
        write_paths=["scripts/shared.py"],
        new_path_prefixes=["quality/first_"],
    )
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second, second_sha = _authority(
        second_dir,
        repo,
        "base-two",
        write_paths=["scripts/shared.py"],
        new_path_prefixes=["scripts/", "quality/first_extra/"],
    )
    findings = fanout_findings([(first, first_sha), (second, second_sha)])
    assert any("exact write overlap" in item for item in findings)
    assert any("prefix overlap" in item for item in findings)
    assert any("exact-vs-prefix overlap" in item for item in findings)


def test_fanout_accepts_disjoint_generic_authorities(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    first, first_sha = _authority(
        first_dir,
        repo,
        "base-one",
        write_paths=["scripts/one.py"],
        new_path_prefixes=["quality/one_"],
    )
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second, second_sha = _authority(
        second_dir,
        repo,
        "base-two",
        write_paths=["scripts/two.py"],
        new_path_prefixes=["quality/two_"],
    )
    assert fanout_findings([(first, first_sha), (second, second_sha)]) == []


def test_authority_hash_is_required(tmp_path: Path) -> None:
    repo, base_sha = _repository(tmp_path)
    authority, _ = _authority(
        tmp_path,
        repo,
        base_sha,
        write_paths=[],
        new_path_prefixes=[],
    )
    assert any("mandatory" in item for item in lane_findings(repo, authority))
