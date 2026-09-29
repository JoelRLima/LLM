"""Secret-safe projections for provider identity and configuration evidence."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_ATOMIC_KEYS = frozenset(
    {
        "authorization",
        "apikey",
        "auth",
        "key",
        "password",
        "passwd",
        "passphrase",
        "token",
        "secret",
        "private",
        "sig",
        "signature",
    }
)

_SECRET_VALUE_WORDS = frozenset(
    {
        "authorization",
        "auth",
        "bearer",
        "credential",
        "key",
        "password",
        "passwd",
        "passphrase",
        "private",
        "secret",
        "sig",
        "signature",
        "token",
    }
)

_CREDENTIAL_KEY_PREFIXES = frozenset(
    {
        "access",
        "api",
        "client",
        "hmac",
        "private",
        "signing",
        "subscription",
    }
)

_CREDENTIAL_HEADER_ROOTS = frozenset(
    {
        "access",
        "api",
        "authorization",
        "auth",
        "bearer",
        "client",
        "credential",
        "hmac",
        "key",
        "private",
        "secret",
        "sig",
        "signature",
        "signing",
        "subscription",
        "token",
    }
)

# These suffixes describe the option itself, rather than a credential value.
# They must be considered before the credential roots: ``signature_version``
# and ``authorization_scheme`` are public behavior metadata, whereas
# ``signature`` and ``authorization`` are secret-bearing values.  ``header``
# is handled separately because ``authorization_header`` contains a credential
# value while ``authorization_header_name`` is descriptive metadata.
_METADATA_SUFFIXES = frozenset(
    {
        "algorithm",
        "field",
        "format",
        "mode",
        "name",
        "path",
        "scheme",
        "type",
        "url",
        "version",
    }
)


def _normalize_key(value: Any) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def _key_words(value: Any) -> tuple[str, ...]:
    return tuple(
        word.casefold()
        for word in re.findall(
            r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|[0-9]+",
            str(value),
        )
    )


def canonicalize_identity_value(value: Any) -> Any:
    """Make nested identity values deterministic, including unordered sets."""

    if isinstance(value, Mapping):
        return {key: canonicalize_identity_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        normalized = [canonicalize_identity_value(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [canonicalize_identity_value(item) for item in value]
    return value


def _is_secret_key(value: Any) -> bool:
    normalized = _normalize_key(value)
    if normalized in _SECRET_ATOMIC_KEYS:
        return True
    words = _key_words(value)
    word_set = set(words)

    # A metadata suffix makes the field name descriptive, not credential
    # bearing.  Apply this to the normalized spelling too, because providers
    # commonly use compact forms such as ``signatureversion``.
    if words and (
        words[-1] in _METADATA_SUFFIXES
        or any(
            normalized.endswith(suffix) and normalized != suffix
            for suffix in _METADATA_SUFFIXES
        )
    ):
        return False

    # A terminal ``header`` names the header value.  It is secret only when
    # the stem identifies a credential family.  A further descriptive suffix
    # was already accepted above, so ``authorization_header_name`` remains
    # public while ``authorization_header`` is redacted.  The normalized check
    # also covers compact and camel-case provider spellings.
    if normalized.endswith("header") and normalized != "header":
        stem_words = set(words[:-1]) if words and words[-1] == "header" else set()
        normalized_stem = normalized[: -len("header")]
        return bool(
            stem_words & _CREDENTIAL_HEADER_ROOTS
            or any(
                normalized_stem == root or normalized_stem.endswith(root)
                for root in _CREDENTIAL_HEADER_ROOTS
            )
        )

    # Explicit value words are credential fields regardless of their prefix,
    # e.g. ``hmac_key``, ``client_secret`` or ``apiCredential``.  Word
    # matching handles separators and camel case; normalized suffix matching
    # covers compact provider spellings such as ``accessToken``/``apikey``.
    if word_set & _SECRET_VALUE_WORDS:
        return True
    if any(
        normalized.endswith(suffix)
        for suffix in (
            "apikey",
            "accesstoken",
            "authtoken",
            "bearer",
            "clientsecret",
            "credential",
            "password",
            "passwd",
            "passphrase",
            "privatekey",
            "secret",
            "signature",
            "authorization",
            "accesskey",
            "signingkey",
        )
    ):
        return True
    if "key" in word_set and word_set & _CREDENTIAL_KEY_PREFIXES:
        return True
    return False


def _redact_endpoint(value: Any) -> Any:
    if not isinstance(value, str) or "://" not in value:
        return value
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = parsed.port
    except ValueError:
        return "<redacted-endpoint>"
    netloc = hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    query = sorted(
        (
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not _is_secret_key(key)
        ),
        key=lambda item: (item[0], item[1]),
    )
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            netloc,
            parsed.path,
            urlencode(query, doseq=True),
            "",
        )
    )


def _redact_identity(value: Any, *, key: Any = None) -> Any:
    if isinstance(value, str):
        if key is not None and _is_secret_key(key):
            return "<redacted>"
        if "://" in value:
            return _redact_endpoint(value)
        return value
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact_identity(item, key=item_key)
            for item_key, item in value.items()
            if not _is_secret_key(item_key)
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_identity(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return {_redact_identity(item) for item in value}
    if key is not None and str(key).casefold() in {
        "api_url",
        "base_url",
        "endpoint",
        "endpoint_identity",
    }:
        return _redact_endpoint(value)
    return value


def redact_identity(value: Any) -> Any:
    """Return a deep non-secret projection for public identity evidence."""

    return _redact_identity(value)


__all__ = ["canonicalize_identity_value", "redact_identity"]
