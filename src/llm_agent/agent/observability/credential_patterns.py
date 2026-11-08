"""Shared text credential patterns for observations and formatted diagnostics."""
from __future__ import annotations

import re

REDACTED_VALUE = "[REDACTED]"

_BEARER_RE = re.compile(
    r"(?i)(\bbearer\s+)(?!\[REDACTED\])([^\s,;\]}\"']+)"
)
_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[ _-]?key|api[ _-]?token|access[ _-]?token|refresh[ _-]?token|token|"
    r"authorization|cookie|session[ _-]?secret|password|passphrase|secret|credentials?|"
    r"task[ _-]?authority(?:[ _-]?token)?|(?:capability|authority)[ _-]?token)\b"
    r"\s*[:=]\s*)(?P<quote>[\"']?)(?P<value>[^\s,;}\]\"']+)(?P=quote)"
)
_AUTHORIZATION_RE = re.compile(
    r"(?i)(\bauthorization\s*[:=]\s*)(?P<scheme>[A-Za-z][A-Za-z0-9_-]*\s+)?"
    r"(?P<quote>[\"']?)(?P<value>[^\s,;}\]\"']+)(?P=quote)"
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----", re.DOTALL)
_URL_PASSWORD_RE = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@:]+:)[^\s/@]+(@)")
_PROVIDER_KEY_RE = re.compile(r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
_QUOTED_CREDENTIAL_RE = re.compile(
    r'''(?i)(["'](?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|cookie|password|passphrase|secret|token)["']\s*:\s*)(?:"[^"\n]*"|'[^'\n]*')'''
)
_QUOTED_ASSIGNMENT_RE = re.compile(
    r'''(?i)(\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|cookie|password|passphrase|secret|token)\b\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*')'''
)
_QUOTED_ASSIGNMENT_RE = re.compile(
    r'''(?i)(\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|cookie|password|passphrase|secret|token)\b\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*')'''
)


def redact_credentials(value: str) -> str:
    def assignment(match: re.Match[str]) -> str:
        quote = match.group("quote")
        return f"{match.group(1)}{quote}{REDACTED_VALUE}{quote}"

    sanitized = _PRIVATE_KEY_RE.sub(REDACTED_VALUE, value)
    sanitized = _URL_PASSWORD_RE.sub(rf"\1{REDACTED_VALUE}\2", sanitized)
    sanitized = _PROVIDER_KEY_RE.sub(REDACTED_VALUE, sanitized)
    sanitized = _QUOTED_CREDENTIAL_RE.sub(rf'\1"{REDACTED_VALUE}"', sanitized)
    sanitized = _QUOTED_ASSIGNMENT_RE.sub(rf"\1{REDACTED_VALUE}", sanitized)
    sanitized = _QUOTED_ASSIGNMENT_RE.sub(rf"\1{REDACTED_VALUE}", sanitized)
    sanitized = _AUTHORIZATION_RE.sub(assignment, sanitized)
    sanitized = _ASSIGNMENT_RE.sub(assignment, sanitized)
    return _BEARER_RE.sub(rf"\1{REDACTED_VALUE}", sanitized)
