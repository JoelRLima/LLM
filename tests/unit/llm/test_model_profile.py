from __future__ import annotations

import gc
from types import MappingProxyType

import pytest

from agent.code.application import build_code_context
from agent.llm.contracts import ModelMessage, ModelRequest, ProviderCapabilities, StructuredOutputMode
from agent.llm.identity import declared_provider_identity, redact_identity
from agent.llm.model_profile import (
    ResolvedModelProfile,
    resolve_gateway_model_profile,
    resolve_model_profile,
)
from agent.llm.model_profile_binding import cached_gateway_model_profile
from agent.llm.providers.openai_compatible import OpenAICompatibleGateway
from agent.llm.session import ChatSession


def test_named_profile_is_the_single_effective_precedence_result() -> None:
    profile = resolve_model_profile(
        {
            "model": "legacy-model",
            "api_url": "http://legacy/v1/chat/completions",
            "temperature": 0.9,
            "max_tokens": 99,
            "default_model_profile": "named",
            "model_profiles": {
                "named": {
                    "provider": "openai_compatible",
                    "base_url": "http://named/v1",
                    "model": "named-model",
                    "temperature": 0.2,
                    "max_tokens": 512,
                    "timeout": 42,
                    "capabilities": {
                        "streaming": False,
                        "structured_output": "json_schema",
                    },
                    "provider_options": {"top_p": 0.3},
                }
            },
        },
        overrides={"temperature": 0.4},
    )

    assert isinstance(profile, ResolvedModelProfile)
    assert profile.name == "named"
    assert profile.model == "named-model"
    assert profile.api_url == "http://named/v1/chat/completions"
    assert profile.temperature == 0.4
    assert profile.max_output_tokens == 512
    assert profile.timeout == 42
    assert profile.capabilities.streaming is False
    assert StructuredOutputMode.JSON_SCHEMA in profile.capabilities.structured_output_modes
    assert isinstance(profile.capabilities, ProviderCapabilities)


def test_legacy_profile_capabilities_are_typed_and_honor_gbnf_override() -> None:
    profile = resolve_model_profile(
        {
            "model": "legacy-model",
            "api_url": "http://legacy/v1/chat/completions",
            "ENABLE_GBNF": False,
        }
    )

    assert profile.name == "legacy"
    assert isinstance(profile.capabilities, ProviderCapabilities)
    assert profile.capabilities.supports(StructuredOutputMode.JSON_PROMPT)
    assert not profile.capabilities.supports(StructuredOutputMode.GBNF)
    assert profile.provider_options["tokenize_path"] == "/tokenize"


def test_profile_options_are_copied_and_public_identity_is_secret_safe() -> None:
    options = {"top_p": 0.2, "nested": {"mode": "safe"}, "api_key": "TOPSECRET"}
    config = {
        "provider": "openai_compatible",
        "model": "stable-model",
        "api_url": "https://backend/v1/chat/completions?token=TOPSECRET&region=sa",
        "provider_options": options,
    }
    profile = resolve_model_profile(config)
    options["nested"]["mode"] = "changed"
    options["top_p"] = 0.9

    reordered = resolve_model_profile(
        {
            "provider_options": {"api_key": "OTHER", "nested": {"mode": "safe"}, "top_p": 0.2},
            "api_url": config["api_url"],
            "model": "stable-model",
            "provider": "openai_compatible",
        }
    )
    public = profile.to_dict()

    assert profile.provider_options["top_p"] == 0.2
    assert profile.provider_options["nested"] == {"mode": "safe"}
    assert profile.fingerprint == reordered.fingerprint
    assert "TOPSECRET" not in str(public)
    assert "api_key" not in str(public)
    assert "token=TOPSECRET" not in str(public)
    assert profile.to_runtime_dict()["provider_options"]["api_key"] == "TOPSECRET"


def test_profile_options_are_deeply_immutable_but_runtime_projection_is_mutable() -> None:
    profile = resolve_model_profile(
        {
            "model": "deep-model",
            "provider_options": {
                "nested": {
                    "items": ["one"],
                    "flags": {"safe"},
                }
            },
        }
    )
    fingerprint = profile.fingerprint

    with pytest.raises(TypeError):
        profile.provider_options["nested"]["mode"] = "blocked"  # type: ignore[index]
    with pytest.raises(AttributeError):
        profile.provider_options["nested"]["items"].append("blocked")  # type: ignore[union-attr]
    with pytest.raises(AttributeError):
        profile.provider_options["nested"]["flags"].add("blocked")  # type: ignore[union-attr]

    runtime = profile.to_runtime_dict()
    runtime["provider_options"]["nested"]["items"].append("runtime-only")
    runtime["provider_options"]["nested"]["flags"].add("runtime-only")

    assert profile.provider_options["nested"]["items"] == ("one",)
    assert profile.provider_options["nested"]["flags"] == frozenset({"safe"})
    assert profile.fingerprint == fingerprint


def test_real_openai_gateway_consumes_a_fresh_mutable_provider_options_projection() -> None:
    source_options = {
        "reasoning_mode": "chat_template_kwargs",
        "nested": {"items": ["one", {"mode": "safe"}]},
    }
    profile = resolve_model_profile(
        {
            "provider": "openai_compatible",
            "model": "gateway-model",
            "provider_options": source_options,
        }
    )

    gateway = OpenAICompatibleGateway(profile)

    assert gateway.provider_options == source_options
    assert not isinstance(gateway.provider_options, MappingProxyType)
    assert not isinstance(gateway.provider_options["nested"], MappingProxyType)
    gateway.provider_options["nested"]["items"].append("runtime-only")
    gateway.provider_options["nested"]["items"][1]["mode"] = "runtime-changed"
    source_options["nested"]["items"].append("source-only")

    assert profile.provider_options["nested"]["items"] == ("one", {"mode": "safe"})
    assert profile.to_runtime_dict()["provider_options"]["nested"]["items"] == [
        "one",
        {"mode": "safe"},
    ]
    request = ModelRequest(
        messages=(ModelMessage(role="user", content="hello"),),
        model="gateway-model",
        temperature=0.2,
        max_output_tokens=32,
    )
    assert gateway.build_payload(request)["model"] == "gateway-model"


def test_endpoint_identity_and_fingerprint_are_secret_safe_and_behavior_sensitive() -> None:
    profile = resolve_model_profile(
        {
            "provider": "openai_compatible",
            "model": "endpoint-model",
            "api_url": (
                "https://alice:TOPSECRET@example.com/v1/chat/completions"
                "?token=QUERYSECRET&region=sa"
            ),
        }
    )
    equivalent = resolve_model_profile(
        {
            "provider": "openai_compatible",
            "model": "endpoint-model",
            "api_url": (
                "https://other:OTHERSECRET@example.com/v1/chat/completions"
                "?region=sa&token=OTHERQUERYSECRET"
            ),
        }
    )
    behavior_change = resolve_model_profile(
        {
            "provider": "openai_compatible",
            "model": "endpoint-model",
            "api_url": "https://example.com/v1/chat/completions?region=eu",
        }
    )

    public = profile.to_dict()
    serialized = repr(public)
    assert profile.endpoint_identity == "https://example.com/v1/chat/completions"
    assert "alice" not in serialized
    assert "TOPSECRET" not in serialized
    assert "QUERYSECRET" not in serialized
    assert profile.fingerprint == equivalent.fingerprint
    assert profile.fingerprint != behavior_change.fingerprint


def test_equivalent_secret_key_spellings_are_redacted_in_maps_and_endpoint_queries() -> None:
    direct = redact_identity(
        {
            "apiKey": "CAMEL_SECRET",
            "apikey": "PLAIN_SECRET",
            "api-key": "DASH_SECRET",
            "api_key": "SNAKE_SECRET",
            "region": "sa",
        }
    )
    profile = resolve_model_profile(
        {
            "provider": "openai_compatible",
            "model": "spelling-model",
            "api_url": (
                "https://example.com/v1/chat/completions?"
                "apiKey=URL_CAMEL&apikey=URL_PLAIN&api-key=URL_DASH&region=sa&tenant=blue"
            ),
            "provider_options": {
                "apiKey": "CAMEL_SECRET",
                "apikey": "PLAIN_SECRET",
                "api-key": "DASH_SECRET",
                "api_key": "SNAKE_SECRET",
                "region": "sa",
            },
        }
    )
    equivalent = resolve_model_profile(
        {
            "provider": "openai_compatible",
            "model": "spelling-model",
            "api_url": (
                "https://example.com/v1/chat/completions?"
                "tenant=blue&region=sa&apikey=OTHER_URL_SECRET"
            ),
            "provider_options": {
                "region": "sa",
                "api_key": "OTHER_SECRET",
            },
        }
    )

    assert direct == {"region": "sa"}
    public = profile.to_dict()
    assert public["provider_options"] == {"region": "sa"}
    assert public["api_url"] == "https://example.com/v1/chat/completions?region=sa&tenant=blue"
    assert all(secret not in repr(public) for secret in ("CAMEL_SECRET", "URL_CAMEL", "SNAKE_SECRET"))
    assert profile.fingerprint == equivalent.fingerprint


def test_injected_gateway_compatibility_ingress_keeps_one_identity_everywhere() -> None:
    class InjectedGateway:
        provider_name = "injected-provider"
        model = "injected-model"
        profile = {"temperature": 0.15, "max_tokens": 64}
        capabilities = ProviderCapabilities(streaming=False)

    config = {
        "provider": "configured-provider",
        "model": "configured-model",
        "api_url": "http://configured/v1/chat/completions",
    }
    gateway = InjectedGateway()
    session = ChatSession("system", config, gateway=gateway)  # type: ignore[arg-type]
    request = session.build_request(stream=False)
    context = build_code_context(config, gateway)  # type: ignore[arg-type]
    declared = declared_provider_identity(gateway)

    assert request.model == "injected-model"
    assert session.model_profile.model == "injected-model"
    assert context.model_profile is not None
    assert context.model_profile.model == "injected-model"
    assert context.metadata["model"] == "injected-model"
    assert declared["model"] == "injected-model"
    assert declared["provider"] == "injected-provider"
    assert declared["profile"]["model"] == "injected-model"
    assert declared["model_config_fingerprint"] == session.model_profile.fingerprint


def test_read_only_slotted_gateway_keeps_canonical_profile_parity_without_monkey_patching() -> None:
    class ReadOnlyGateway:
        __slots__ = ("profile", "provider_name", "model", "capabilities")

        def __init__(self) -> None:
            object.__setattr__(
                self,
                "profile",
                {
                    "provider": "readonly-provider",
                    "model": "readonly-model",
                    "api_url": "https://readonly.example/v1/chat/completions?region=sa",
                    "temperature": 0.25,
                    "max_tokens": 72,
                    "capabilities": {"streaming": False},
                    "provider_options": {"nested": {"items": ["safe"]}},
                },
            )
            object.__setattr__(self, "provider_name", "readonly-provider")
            object.__setattr__(self, "model", "readonly-model")
            object.__setattr__(self, "capabilities", ProviderCapabilities(streaming=False))

        def __setattr__(self, name: str, value: object) -> None:
            raise AttributeError(f"read-only gateway: {name}")

    config = {
        "provider": "configured-provider",
        "model": "configured-model",
        "api_url": "https://configured.example/v1/chat/completions",
    }
    gateway = ReadOnlyGateway()
    session = ChatSession("system", config, gateway=gateway)  # type: ignore[arg-type]
    request = session.build_request(stream=False)
    context = build_code_context(config, gateway)  # type: ignore[arg-type]
    declared = declared_provider_identity(gateway, profile=session.model_profile)

    assert not hasattr(gateway, "resolved_profile")
    assert request.model == "readonly-model"
    assert request.temperature == 0.25
    assert session.model_profile == context.model_profile
    assert context.model_profile is not None
    assert context.metadata["model"] == "readonly-model"
    assert context.metadata["provider"] == "readonly-provider"
    assert context.metadata["model_config_fingerprint"] == session.model_profile.fingerprint
    assert declared["model"] == "readonly-model"
    assert declared["provider"] == "readonly-provider"
    assert declared["model_config_fingerprint"] == session.model_profile.fingerprint


def test_gateway_binding_never_overrides_a_later_current_config_resolution() -> None:
    class ReadOnlyGateway:
        __slots__ = ("profile", "provider_name", "model", "capabilities")

        def __init__(self) -> None:
            object.__setattr__(self, "profile", {"provider": "injected", "model": "fixed-model"})
            object.__setattr__(self, "provider_name", "injected")
            object.__setattr__(self, "model", "fixed-model")
            object.__setattr__(self, "capabilities", ProviderCapabilities(streaming=False))

        def __setattr__(self, name: str, value: object) -> None:
            raise AttributeError(f"read-only gateway: {name}")

    config_a = {
        "provider": "configured",
        "model": "configured-a",
        "api_url": "https://configured.example/v1/chat/completions",
        "temperature": 0.1,
        "max_tokens": 10,
    }
    config_b = {
        **config_a,
        "temperature": 0.9,
        "max_tokens": 99,
    }
    gateway = ReadOnlyGateway()

    profile_a = resolve_gateway_model_profile(config_a, gateway)
    session_a = ChatSession("system", config_a, gateway=gateway)  # type: ignore[arg-type]
    context_a = build_code_context(config_a, gateway)  # type: ignore[arg-type]
    profile_b = resolve_gateway_model_profile(config_b, gateway)
    session_b = ChatSession("system", config_b, gateway=gateway)  # type: ignore[arg-type]
    request_b = session_b.build_request(stream=False)
    context_b = build_code_context(config_b, gateway)  # type: ignore[arg-type]
    declared_a = declared_provider_identity(gateway, profile=profile_a)
    declared_b = declared_provider_identity(gateway, profile=profile_b)

    assert profile_a.temperature == 0.1
    assert profile_a.max_output_tokens == 10
    assert profile_b.temperature == 0.9
    assert profile_b.max_output_tokens == 99
    assert profile_a != profile_b
    assert profile_a.temperature == 0.1
    assert profile_a.max_output_tokens == 10
    assert session_a.model_profile == profile_a
    assert context_a.model_profile == profile_a
    assert context_a.metadata["model_config_fingerprint"] == profile_a.fingerprint
    assert request_b.temperature == 0.9
    assert request_b.max_output_tokens == 99
    assert session_b.model_profile == profile_b
    assert context_b.model_profile == profile_b
    assert context_b.metadata["temperature"] == 0.9
    assert context_b.metadata["max_output_tokens"] == 99
    assert context_b.metadata["model_config_fingerprint"] == profile_b.fingerprint
    assert declared_a["model_config_fingerprint"] == profile_a.fingerprint
    assert declared_b["model_config_fingerprint"] == profile_b.fingerprint
    assert declared_a["model_config_fingerprint"] != declared_b["model_config_fingerprint"]
    assert cached_gateway_model_profile(gateway) is None


def test_temporary_nonweak_gateways_are_not_permanently_retained_by_profile_binding() -> None:
    class NonWeakGateway:
        __slots__ = ("profile", "provider_name", "model", "capabilities")

        def __init__(self, model: str) -> None:
            object.__setattr__(self, "profile", {"provider": "temporary", "model": model})
            object.__setattr__(self, "provider_name", "temporary")
            object.__setattr__(self, "model", model)
            object.__setattr__(self, "capabilities", ProviderCapabilities(streaming=False))

    import agent.llm.model_profile_binding as binding

    gc.collect()
    initial_bindings = len(binding._GATEWAY_PROFILE_BINDINGS)
    for index in range(64):
        gateway = NonWeakGateway(f"temporary-{index}")
        resolve_gateway_model_profile({"temperature": 0.2, "max_tokens": 8}, gateway)
        assert cached_gateway_model_profile(gateway) is None
        del gateway
    gc.collect()

    assert len(binding._GATEWAY_PROFILE_BINDINGS) == initial_bindings


@pytest.mark.parametrize(
    "secret_key",
    [
        "apiKey",
        "apikey",
        "api-key",
        "api_key",
        "token",
        "auth",
        "password",
        "signature",
        "sig",
        "privateKey",
        "secretKey",
        "Ocp-Apim-Subscription-Key",
        "apiCredential",
        "hmac_key",
        "signing_key",
        "access_key",
        "client_secret",
        "private_key",
        "subscription_key",
        "passphrase",
        "authorization",
        "bearer_token",
        "authorization_header",
        "auth_header",
        "bearer_header",
        "token_header",
        "signature_header",
        "hmac_header",
    ],
)
def test_structural_secret_key_matrix_redacts_credential_families(secret_key: str) -> None:
    public = redact_identity(
        {"provider_options": {secret_key: "MATRIX_SECRET", "region": "sa"}}
    )
    profile = resolve_model_profile(
        {
            "provider": "openai_compatible",
            "model": "secret-matrix",
            "provider_options": {secret_key: "MATRIX_SECRET", "region": "sa"},
        }
    )

    assert "MATRIX_SECRET" not in repr(public)
    assert public["provider_options"] == {"region": "sa"}
    assert "MATRIX_SECRET" not in repr(profile.to_dict())
    assert profile.to_runtime_dict()["provider_options"][secret_key] == "MATRIX_SECRET"


@pytest.mark.parametrize(
    "metadata_key",
    [
        "signature_version",
        "signatureVersion",
        "signature_algorithm",
        "signing_algorithm",
        "authorization_scheme",
        "authorizationScheme",
        "authentication_mode",
        "authenticationMode",
        "token_type",
        "tokenize_path",
        "api_version",
        "api_mode",
        "api_format",
        "api_path",
        "api_type",
        "api_scheme",
        "hmac_algorithm",
        "hmac_mode",
        "private_key_path",
        "api_key_name",
        "authorization_header_name",
        "api_key_header_name",
        "token_header_name",
        "signature_header_name",
        "header_name",
        "header_field",
        "custom_header",
    ],
)
def test_structural_secret_classifier_preserves_behavior_metadata(metadata_key: str) -> None:
    public = redact_identity(
        {"provider_options": {metadata_key: "METADATA_VALUE", "region": "sa"}}
    )

    assert public["provider_options"] == {
        metadata_key: "METADATA_VALUE",
        "region": "sa",
    }


def test_behavior_metadata_changes_fingerprint_while_secret_values_do_not() -> None:
    def profile_for(signature_version: str, secret: str) -> ResolvedModelProfile:
        return resolve_model_profile(
            {
                "provider": "openai_compatible",
                "model": "classifier-model",
                "provider_options": {
                    "signature_version": signature_version,
                    "hmac_key": secret,
                    "region": "sa",
                },
            }
        )

    first = profile_for("v1", "FIRST_HMAC_SECRET")
    secret_change = profile_for("v1", "OTHER_HMAC_SECRET")
    behavior_change = profile_for("v2", "OTHER_HMAC_SECRET")

    assert first.fingerprint == secret_change.fingerprint
    assert first.fingerprint != behavior_change.fingerprint
    assert "FIRST_HMAC_SECRET" not in repr(first.to_dict())
    assert first.to_dict()["provider_options"] == {
        "region": "sa",
        "signature_version": "v1",
    }
    assert first.to_runtime_dict()["provider_options"]["hmac_key"] == "FIRST_HMAC_SECRET"


def test_credential_header_values_are_private_but_header_metadata_is_behavioral() -> None:
    def profile_for(secret: str, header_name: str) -> ResolvedModelProfile:
        return resolve_model_profile(
            {
                "provider": "openai_compatible",
                "model": "header-classifier-model",
                "provider_options": {
                    "authorization_header": secret,
                    "authorization_header_name": header_name,
                    "header_format": "bearer",
                },
            }
        )

    first = profile_for("Bearer FIRST_HEADER_SECRET", "Authorization")
    secret_change = profile_for("Bearer OTHER_HEADER_SECRET", "Authorization")
    behavior_change = profile_for("Bearer OTHER_HEADER_SECRET", "X-Authorization")

    assert first.fingerprint == secret_change.fingerprint
    assert first.fingerprint != behavior_change.fingerprint
    assert "FIRST_HEADER_SECRET" not in repr(
        redact_identity(
            {
                "provider_options": {
                    "authorization_header": "Bearer FIRST_HEADER_SECRET"
                }
            }
        )
    )
    assert "FIRST_HEADER_SECRET" not in repr(first.to_dict())
    assert first.to_dict()["provider_options"] == {
        "authorization_header_name": "Authorization",
        "header_format": "bearer",
    }
    assert (
        first.to_runtime_dict()["provider_options"]["authorization_header"]
        == "Bearer FIRST_HEADER_SECRET"
    )


def test_structural_url_redaction_sanitizes_nested_options_and_preserves_controls() -> None:
    secret_url = (
        "https://alice:URL_PASSWORD@proxy.example/forward?"
        "sig=SIG_SECRET&X-Amz-Signature=AWS_SECRET&"
        "Ocp-Apim-Subscription-Key=SUB_SECRET&region=sa&tenant=blue"
    )
    public = redact_identity({"provider_options": {"proxy_url": secret_url}})
    control = redact_identity(
        {
            "provider_options": {
                "proxy_url": "https://proxy.example/forward?mode=fast&region=sa&tenant=blue",
                "tokenize_path": "/tokenize",
                "api_version": "v1",
                "authentication_mode": "header",
            }
        }
    )

    sanitized = public["provider_options"]["proxy_url"]
    assert sanitized == "https://proxy.example/forward?region=sa&tenant=blue"
    assert all(secret not in repr(public) for secret in ("URL_PASSWORD", "SIG_SECRET", "AWS_SECRET", "SUB_SECRET"))
    assert control["provider_options"]["proxy_url"] == (
        "https://proxy.example/forward?mode=fast&region=sa&tenant=blue"
    )
    assert control["provider_options"]["tokenize_path"] == "/tokenize"
    assert control["provider_options"]["api_version"] == "v1"
    assert control["provider_options"]["authentication_mode"] == "header"


def test_nested_secret_urls_are_excluded_from_fingerprint_but_runtime_options_remain_real() -> None:
    def profile_for(secret: str, region: str) -> ResolvedModelProfile:
        return resolve_model_profile(
            {
                "provider": "openai_compatible",
                "model": "proxy-model",
                "provider_options": {
                    "proxy_url": f"https://proxy.example/forward?sig={secret}&region={region}",
                    "region": region,
                },
            }
        )

    first = profile_for("FIRST_SECRET", "sa")
    equivalent = profile_for("OTHER_SECRET", "sa")
    behavior_change = profile_for("OTHER_SECRET", "eu")

    assert first.fingerprint == equivalent.fingerprint
    assert first.fingerprint != behavior_change.fingerprint
    assert "FIRST_SECRET" not in repr(first.to_dict())
    assert first.to_runtime_dict()["provider_options"]["proxy_url"].endswith(
        "sig=FIRST_SECRET&region=sa"
    )
