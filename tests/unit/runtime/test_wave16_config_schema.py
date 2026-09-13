from __future__ import annotations

import copy
import json

import pytest

from agent.llm.model_compatibility import StructuredReasoningPolicy
from agent.llm.model_profile import resolve_model_profile
from agent.runtime.config import carregar_config
from agent.runtime.config_errors import ConfigError
from agent.runtime.config_repository import packaged_config_defaults
from agent.runtime.config_schema import validate_config_document


def _document() -> dict[str, object]:
    return copy.deepcopy(packaged_config_defaults())


def _profile(document: dict[str, object]) -> dict[str, object]:
    profiles = document["model_profiles"]
    assert isinstance(profiles, dict)
    profile = profiles["local_8gb"]
    assert isinstance(profile, dict)
    return profile


def test_packaged_default_and_valid_compatibility_pass_strict_schema() -> None:
    document = _document()

    validate_config_document(
        document,
        require_version=True,
        require_complete=True,
    )

    assert _profile(document)["compatibility"] == {
        "structured_reasoning": "disable_reasoning"
    }


@pytest.mark.parametrize(
    "compatibility",
    [
        None,
        ["disable_reasoning"],
        {"structured_reasoning": "unknown"},
        {"structured_reasoning": "allow", "unexpected": True},
    ],
)
def test_invalid_compatibility_values_fail_closed(compatibility: object) -> None:
    document = _document()
    _profile(document)["compatibility"] = compatibility

    with pytest.raises(ConfigError):
        validate_config_document(
            document,
            require_version=True,
            require_complete=True,
        )


@pytest.mark.parametrize("value", ["allow", "disable_reasoning"])
def test_supported_compatibility_values_are_accepted(value: str) -> None:
    document = _document()
    _profile(document)["compatibility"] = {"structured_reasoning": value}

    validate_config_document(
        document,
        require_version=True,
        require_complete=True,
    )


def test_missing_compatibility_and_missing_nested_field_preserve_allow_default() -> None:
    without_section = _document()
    _profile(without_section).pop("compatibility")
    validate_config_document(
        without_section,
        require_version=True,
        require_complete=True,
    )
    assert resolve_model_profile(without_section, profile_name="local_8gb").compatibility.structured_reasoning is StructuredReasoningPolicy.ALLOW

    empty_section = _document()
    _profile(empty_section)["compatibility"] = {}
    validate_config_document(
        empty_section,
        require_version=True,
        require_complete=True,
    )
    assert resolve_model_profile(empty_section, profile_name="local_8gb").compatibility.structured_reasoning is StructuredReasoningPolicy.ALLOW


def test_schema_acceptance_does_not_depend_on_identity_fields() -> None:
    for model, provider, endpoint, hardware in (
        ("model-a", "provider-a", "http://a/v1", "low_vram_8gb"),
        ("model-b", "provider-b", "http://b/v1", "balanced"),
    ):
        document = _document()
        document["model"] = model
        document["api_url"] = f"{endpoint}/chat/completions"
        document["hardware_profile"] = hardware
        _profile(document)["provider"] = provider
        validate_config_document(
            document,
            require_version=True,
            require_complete=True,
        )


def test_legacy_carregar_config_preserves_compatibility_subsection(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "default_model_profile": "local",
                "model_profiles": {
                    "local": {
                        "provider": "openai_compatible",
                        "model": "legacy-compatible",
                        "compatibility": {
                            "structured_reasoning": "disable_reasoning"
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = carregar_config(str(path))

    assert loaded["model_profiles"]["local"]["compatibility"] == {
        "structured_reasoning": "disable_reasoning"
    }
