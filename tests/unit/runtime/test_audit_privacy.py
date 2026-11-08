"""Credential leakage checks cover formatted arguments, nested JSON and exceptions."""
import logging
import sys

import pytest

from llm_agent.agent.observability.log_formatter import RedactingFormatter
from llm_agent.agent.observability.redaction import redact_observation_value, redact_text


@pytest.mark.parametrize("text,secret", [
    ('{"nested": {"api_key": "a secret with spaces"}}', "a secret with spaces"),
    ("https://user:private-password@example.org/api", "private-password"),
    ("Bearer opaque-value", "opaque-value"),
    ("sk-proj-abcdefghijklmnopqrstuvwxyz123456", "abcdefghijklmnopqrstuvwxyz123456"),
    ("-----BEGIN PRIVATE KEY-----\nprivate material\n-----END PRIVATE KEY-----", "private material"),
    ("password=super-secret", "super-secret"),
    ("password='my private passphrase'", "my private passphrase"),
])
def test_credential_text_is_redacted_before_truncation(text, secret):
    assert secret not in redact_text(text, limit=10000)
    assert "[REDACTED]" in redact_text(text, limit=10000)


def test_nested_observations_keep_metrics_but_omit_secrets():
    value = redact_observation_value({"nested": [{"api_key": "private", "prompt": "sensitive"}], "input_tokens": 7})
    assert value["nested"][0] == {"api_key": "[REDACTED]", "prompt": "[OMITTED]"}
    assert value["input_tokens"] == 7


def test_formatter_redacts_exception_and_arguments_without_mutating_record():
    try:
        raise RuntimeError("password=exception-secret")
    except RuntimeError:
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "authorization: Bearer %s", ("argument-secret",), sys.exc_info())
    result = RedactingFormatter("%(message)s").format(record)
    assert "exception-secret" not in result
    assert "argument-secret" not in result
    assert "RuntimeError" in result
    assert record.args == ("argument-secret",)
    assert record.exc_text is None
