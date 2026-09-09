from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from agent.health.standalone import run_standalone_health_check
from agent.llm.model_profile import resolve_model_profile
from agent.runtime.config_errors import ConfigError
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.config_schema import validate_config_document
from agent.runtime.paths import AppPaths
from agent.runtime.secret_reference import (
    SecretReferenceError,
    SecretReferenceResolutionError,
    SecretReferenceV1,
    resolve_secret_reference,
)

_SENTINEL = "PV155_SECRET_SENTINEL_8fd77e"


def _profile_document(reference: object) -> dict[str, object]:
    return {
        "model_profiles": {
            "local": {
                "provider": "openai_compatible",
                "model": "default",
                "credential_ref": reference,
            }
        }
    }


def test_valid_reference_is_closed_immutable_metadata() -> None:
    reference = SecretReferenceV1.from_mapping(
        {"source": "env", "name": "PV155_SYNTHETIC_BEARER_REF", "kind": "bearer"}
    )

    assert reference.to_dict() == {
        "source": "env",
        "name": "PV155_SYNTHETIC_BEARER_REF",
        "kind": "bearer",
    }
    with pytest.raises(FrozenInstanceError):
        reference.name = "OTHER"  # type: ignore[misc]


@pytest.mark.parametrize(
    "value",
    [
        None,
        "raw-secret",
        {"source": "file", "name": "PV155_SYNTHETIC_BEARER_REF", "kind": "bearer"},
        {"source": "env", "name": "PV155_SYNTHETIC_BEARER_REF", "kind": "basic"},
        {"source": "env", "name": "", "kind": "bearer"},
        {"source": "env", "name": "OPENAI-API-KEY", "kind": "bearer"},
        {"source": "env", "name": "PV155_SYNTHETIC_BEARER_REF", "kind": "bearer", "extra": 1},
    ],
)
def test_reference_parser_rejects_unsupported_shapes(value: object) -> None:
    with pytest.raises(SecretReferenceError):
        SecretReferenceV1.from_mapping(value)

    with pytest.raises(ConfigError, match="credential_ref"):
        validate_config_document(_profile_document(value), require_version=False, require_complete=False)


def test_reference_resolution_is_late_and_does_not_cache_value() -> None:
    reference = SecretReferenceV1("env", "PV155_SECRET_SENTINEL_8fd77e", "bearer")
    environment = {reference.name: _SENTINEL}

    assert resolve_secret_reference(reference, environment) == _SENTINEL
    environment[reference.name] = "PV155_SECRET_SENTINEL_REPLACED"
    assert resolve_secret_reference(reference, environment) == "PV155_SECRET_SENTINEL_REPLACED"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_empty_reference_fails_without_exposing_value(value: object) -> None:
    reference = SecretReferenceV1("env", "PV155_SYNTHETIC_BEARER_REF", "bearer")
    environment = {} if value is None else {reference.name: value}  # type: ignore[dict-item]

    with pytest.raises(SecretReferenceResolutionError) as caught:
        resolve_secret_reference(reference, environment)
    assert _SENTINEL not in str(caught.value)


def test_profile_metadata_and_fingerprint_do_not_depend_on_resolved_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = {
        "provider": "openai_compatible",
        "model": "default",
        "credential_ref": {
            "source": "env",
            "name": "PV155_SYNTHETIC_BEARER_REF",
            "kind": "bearer",
        },
    }
    monkeypatch.setenv("PV155_SYNTHETIC_BEARER_REF", _SENTINEL)
    first = resolve_model_profile(base)
    monkeypatch.setenv("PV155_SYNTHETIC_BEARER_REF", "PV155_SECRET_SENTINEL_REPLACED")
    second = resolve_model_profile(base)
    first_public = first.to_dict()
    first_runtime = first.to_runtime_dict()

    assert first.credential_ref == SecretReferenceV1("env", "PV155_SYNTHETIC_BEARER_REF", "bearer")
    assert first.fingerprint == second.fingerprint
    assert first_runtime["credential_ref"] == {
        "source": "env",
        "name": "PV155_SYNTHETIC_BEARER_REF",
        "kind": "bearer",
    }
    assert _SENTINEL not in repr(first_public)
    assert _SENTINEL not in repr(first_runtime)


def test_resolved_profile_projection_without_reference_is_compatible() -> None:
    profile = resolve_model_profile(
        {
            "provider": "openai_compatible",
            "model": "default",
        }
    )

    assert resolve_model_profile(profile) == profile


def test_offline_config_validation_and_doctor_never_resolve_reference(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    repository = ConfigRepository(paths)
    repository.initialize()
    document = json.loads(paths.config_file.read_text(encoding="utf-8"))
    selected = document["default_model_profile"]
    document["model_profiles"][selected]["credential_ref"] = {
        "source": "env",
        "name": "PV155_SYNTHETIC_BEARER_REF",
        "kind": "bearer",
    }
    paths.config_file.write_text(json.dumps(document), encoding="utf-8")

    def fail_if_resolved(*_args, **_kwargs):
        raise AssertionError("offline validation resolved a credential")

    monkeypatch.setattr(
        "agent.runtime.secret_reference.resolve_secret_reference",
        fail_if_resolved,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    report = run_standalone_health_check(app_paths=paths, workspace=workspace)

    assert report["readiness"]["offline_ready"] is True
    assert report["readiness"]["backend_connectivity"] == "not_checked"
