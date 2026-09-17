"""Focused tests for the v003 release manifest and candidate identity."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import pytest

from distribution import release_manifest as release_manifest_module
from distribution.release_identity import APPLICATION_NAMESPACE
from distribution.release_manifest import (
    ManifestValidationError,
    bundle_relative_path,
    candidate_id,
    make_manifest,
    validate_manifest,
)

BASE_COMMIT = "a" * 40
CANDIDATE_TREE = "b" * 40


def _manifest() -> dict[str, Any]:
    return make_manifest(
        source_base_commit=BASE_COMMIT,
        source_tree=CANDIDATE_TREE,
        wheel_sha256="1" * 64,
        runtime_lock_sha256="2" * 64,
        bootstrap_pip_lock_sha256="3" * 64,
    )


def test_uncommitted_candidate_provenance_is_explicit() -> None:
    document = _manifest()

    assert document["source"] == {
        "status": "uncommitted_candidate",
        "base_commit": BASE_COMMIT,
        "commit": None,
        "tree": CANDIDATE_TREE,
    }
    validate_manifest(document, expected_source_tree=CANDIDATE_TREE, expected_base_commit=BASE_COMMIT)


def test_manifest_application_namespace_is_independent_of_distribution_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(release_manifest_module, "DISTRIBUTION_NAME", "future-distribution")

    document = release_manifest_module.make_manifest(
        source_base_commit=BASE_COMMIT,
        source_tree=CANDIDATE_TREE,
        wheel_sha256="1" * 64,
        runtime_lock_sha256="2" * 64,
        bootstrap_pip_lock_sha256="3" * 64,
    )

    identity = cast(dict[str, Any], document["product_identity"])
    assert identity["distribution_name"] == "future-distribution"
    assert identity["application_namespace"] == APPLICATION_NAMESPACE


def test_candidate_id_is_invariant_to_commit_sealing_fields() -> None:
    candidate = _manifest()
    sealed = deepcopy(candidate)
    sealed_source = cast(dict[str, Any], sealed["source"])
    sealed_source["status"] = "committed_release"
    sealed_source["commit"] = "c" * 40

    assert candidate_id(candidate) == candidate_id(sealed)
    validate_manifest(sealed)


def test_uncommitted_candidate_rejects_fabricated_commit() -> None:
    document = _manifest()
    source = cast(dict[str, Any], document["source"])
    source["commit"] = f"uncommitted:{BASE_COMMIT}"
    document["candidate_id"] = candidate_id(document)

    with pytest.raises(ManifestValidationError, match="commit = null"):
        validate_manifest(document)


def test_manifest_rejects_head_as_candidate_tree() -> None:
    document = _manifest()
    source = cast(dict[str, Any], document["source"])
    source["tree"] = BASE_COMMIT
    document["candidate_id"] = candidate_id(document)

    with pytest.raises(ManifestValidationError, match="must not substitute"):
        validate_manifest(document)


def test_manifest_rejects_candidate_tree_equal_to_base_commit() -> None:
    document = _manifest()
    source = cast(dict[str, Any], document["source"])
    source["tree"] = BASE_COMMIT

    with pytest.raises(ManifestValidationError, match="must not substitute"):
        validate_manifest(document)


def test_manifest_rejects_tampered_uv_signature_evidence() -> None:
    document = _manifest()
    build_tools = cast(dict[str, Any], document["build_tools"])
    uv = cast(dict[str, Any], build_tools["uv"])
    signature = cast(dict[str, Any], uv["expected_signature"])
    signature["timestamp_thumbprint"] = "0" * 40

    with pytest.raises(ManifestValidationError, match="signature evidence"):
        validate_manifest(document)


def test_manifest_rejects_unknown_schema_version() -> None:
    document = _manifest()
    document["schema_version"] = "W18-RELEASE-MANIFEST-V2"

    with pytest.raises(ManifestValidationError, match="unknown schema"):
        validate_manifest(document)


def test_manifest_rejects_malformed_hash() -> None:
    document = _manifest()
    application = cast(dict[str, Any], document["application"])
    application["wheel_sha256"] = "not-a-sha256"

    with pytest.raises(ManifestValidationError, match="wheel_sha256"):
        validate_manifest(document)


@pytest.mark.parametrize("field", ["platform", "install_scope"])
def test_manifest_rejects_wrong_platform_or_scope(field: str) -> None:
    document = _manifest()
    document[field] = "wrong"

    with pytest.raises(ManifestValidationError, match="wrong"):
        validate_manifest(document)


@pytest.mark.parametrize("value", ["../wheel.whl", "/wheel.whl", r"C:\\wheel.whl", "a//wheel.whl"])
def test_bundle_paths_remain_strictly_relative(value: str) -> None:
    with pytest.raises(ManifestValidationError):
        bundle_relative_path(value, "artifact.path")
