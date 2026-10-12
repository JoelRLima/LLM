"""Focused tests for the local W23 acceptance boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from distribution.candidate_manifest import CandidateManifestError, validate_repo_path
from scripts import w23_acceptance as acceptance

ROOT = Path(__file__).resolve().parents[2]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_explicit_authority_and_protected_bindings_are_verified() -> None:
    protected = {"workspace/spec.txt": b"protected"}
    authority = json.dumps({
        **_synthetic_authority_fields(),
        "protected_hashes_at_install": [
            {"path": "workspace/spec.txt", "sha256": _sha(b"protected")},
        ],
    }, sort_keys=True).encode()
    result = acceptance.verify_authority_bytes(
        authority, _sha(authority), protected,
        {"workspace/spec.txt": _sha(b"protected")},
    )
    assert result["schema_version"] == 1


def test_wrong_or_missing_authority_and_protected_mismatch_fail_closed() -> None:
    expected = b"protected"
    authority = json.dumps({
        **_synthetic_authority_fields(),
        "protected_hashes_at_install": [
            {"path": "workspace/spec.txt", "sha256": _sha(expected)},
        ],
    }, sort_keys=True).encode()
    protected = {"workspace/spec.txt": expected}
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.verify_authority_bytes(authority, "0" * 64, protected,
                                          {"workspace/spec.txt": _sha(expected)})
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.verify_authority_bytes(authority, _sha(authority),
                                          {"workspace/spec.txt": b"changed"},
                                          {"workspace/spec.txt": _sha(expected)})
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.verify_authority_bytes(authority, _sha(authority), protected, {})
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.run_acceptance(
            Path("."), authority_sha256="0" * 64,
            protected_sha256={}, authority_path="missing-authority.json",
        )


def _synthetic_authority(rows: list[dict[str, str]]) -> bytes:
    return json.dumps({
        **_synthetic_authority_fields(),
        "protected_hashes_at_install": rows,
    }, sort_keys=True).encode()


def _synthetic_authority_fields() -> dict[str, object]:
    # Assemble synthetic fixture paths; these values never open local authority files.
    return {
        "schema_version": 1,
        "sole_active_selector": ".agent" + "-local/AUTHORITY_MANIFEST.json",
        "canonical_contract": {
            "path": ".agent" + "-local/W23_TASK_CONTRACT_v007_RESUME.md",
            "sha256": "af8a9a8f09be60261a30120d5fe44bedc518c8e74ebf8d842858ca9bd8953965",
        },
        "canonical_spec": {
            "path": ".agent" + "-local/W23_TASK_SPEC_v007_RESUME.md",
            "sha256": "3cd008124d6379362cef3997c76a748a131dedd0b5f3e94b01900bd930746ed7",
        },
    }


def test_wrong_canonical_authority_reference_fails_closed() -> None:
    protected = {"workspace/spec.txt": b"protected"}
    fields = _synthetic_authority_fields()
    fields["canonical_contract"] = {
        "path": ".agent" + "-local/W23_TASK_CONTRACT_v007_RESUME.md",
        "sha256": "0" * 64,
    }
    authority = json.dumps({
        **fields,
        "protected_hashes_at_install": [
            {"path": "workspace/spec.txt", "sha256": _sha(b"protected")},
        ],
    }, sort_keys=True).encode()

    with pytest.raises(acceptance.AcceptanceError, match="canonical artifact reference"):
        acceptance.verify_authority_bytes(
            authority, _sha(authority), protected,
            {"workspace/spec.txt": _sha(b"protected")},
        )


@pytest.mark.parametrize("path", [".git/index", ".git/info/exclude"])
def test_exact_git_sentinels_are_local_context_not_candidate_paths(path: str) -> None:
    protected = {path: b"local metadata"}
    authority = _synthetic_authority([{"path": path, "sha256": _sha(protected[path])}])

    result = acceptance.verify_authority_bytes(
        authority, _sha(authority), protected, {path: _sha(protected[path])},
    )

    assert result["protected_hashes_at_install"][0]["path"] == path
    with pytest.raises(CandidateManifestError):
        validate_repo_path(path)


def test_non_string_repo_validator_result_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "workspace/spec.txt"
    protected = {path: b"protected"}
    authority = _synthetic_authority([{"path": path, "sha256": _sha(protected[path])}])
    monkeypatch.setattr(acceptance, "validate_repo_path", lambda _path: None)

    with pytest.raises(acceptance.AcceptanceError, match="unsafe protected path"):
        acceptance.verify_authority_bytes(
            authority, _sha(authority), protected, {path: _sha(protected[path])},
        )


@pytest.mark.parametrize("path", [
    ".git/config",
    ".git/HEAD",
    ".git/objects/x",
    ".git/refs/heads/main",
    ".git/../index",
    ".git//index",
    r".git\index",
    ".GIT/index",
    "/etc/passwd",
    "C:/repo/file",
    r"\\server\share\file",
])
def test_other_git_and_non_repo_paths_are_rejected(path: str) -> None:
    protected = {path: b"local metadata"}
    authority = _synthetic_authority([{"path": path, "sha256": _sha(protected[path])}])

    with pytest.raises(acceptance.AcceptanceError, match="unsafe protected path"):
        acceptance.verify_authority_bytes(
            authority, _sha(authority), protected, {path: _sha(protected[path])},
        )


@pytest.mark.parametrize("path", [".git/index", ".git/info/exclude", "workspace/spec.txt"])
def test_stale_hash_fails_closed_for_git_sentinels_and_repo_paths(path: str) -> None:
    protected = {path: b"actual bytes"}
    stale_hash = _sha(b"different bytes")
    authority = _synthetic_authority([{"path": path, "sha256": stale_hash}])

    with pytest.raises(acceptance.AcceptanceError, match="protected workspace binding changed"):
        acceptance.verify_authority_bytes(
            authority, _sha(authority), protected, {path: stale_hash},
        )


def test_portable_witness_verification_does_not_require_local_authority() -> None:
    from scripts.w23_architecture.evidence import verify_witness
    from tests.unit.test_w23_evidence import _inputs

    raw, predecessor, production, tooling, snapshots = _inputs()
    assert verify_witness(raw, predecessor, production, tooling, snapshots)["schema_version"] == 1


def test_local_adapter_fails_closed_when_portable_verification_rejects_witness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(*_args: object, **_kwargs: object) -> None:
        raise acceptance.EvidenceError("stale tooling")

    monkeypatch.setattr(acceptance, "verify_witness", reject)
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.verify_portable_files(ROOT)


def test_explicit_candidate_is_passed_to_k7_and_invalid_selection_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[bytes, str]] = []

    def accept(data: bytes, digest: str) -> dict[str, str]:
        seen.append((data, digest))
        return {"kind": "EXACT_BYTE_REPOSITORY_CANDIDATE"}

    monkeypatch.setattr(acceptance, "parse_manifest", accept)
    digest = _sha(b"selected")
    status, manifest = acceptance.explicit_candidate(b"selected", digest)
    assert (status, manifest, seen) == ("SELECTED", {"kind": "EXACT_BYTE_REPOSITORY_CANDIDATE"},
                                         [(b"selected", digest)])

    def reject(_data: bytes, _digest: str) -> dict[str, str]:
        raise CandidateManifestError("invalid selected candidate")

    monkeypatch.setattr(acceptance, "parse_manifest", reject)
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.explicit_candidate(b"invalid", _sha(b"invalid"))


def test_absent_future_manifest_stays_not_selected_without_latest_discovery() -> None:
    assert acceptance.explicit_candidate(None, None) == ("NOT_SELECTED", None)
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.explicit_candidate(None, _sha(b"future"))
