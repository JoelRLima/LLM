from scripts.check_wave2_architecture import check_source, run_checks


def test_current_wave2_architecture_is_clean() -> None:
    assert run_checks() == []


def test_checker_rejects_text_classifier_and_direct_legacy_name() -> None:
    findings = check_source(
        """
def should_retry(error_message):
    return 'timeout' in error_message.lower()

def classify_error(text):
    return text
""",
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("S2-1") for item in findings)
    assert any("classify_error" in item for item in findings)


def test_checker_propagates_one_and_two_failure_text_aliases() -> None:
    findings = check_source(
        """
def should_retry(error_message):
    first = error_message
    second = first
    return "timeout" in second.lower()
""",
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("S2-1") for item in findings)


def test_checker_rejects_typed_mapping_alias_and_getattr_bypass() -> None:
    findings = check_source(
        """
from agent.tools.contracts import ToolResult as CanonicalResult

def policy(result: CanonicalResult):
    get = getattr(result, 'get')
    return get('status')
""",
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("S2-2") for item in findings)


def test_checker_propagates_legacy_result_and_dict_aliases() -> None:
    findings = check_source(
        """
from agent.tools.contracts import ToolResult

def should_recover(result: ToolResult):
    legacy = result.to_legacy_dict(include_details=True)
    legacy_alias = legacy
    first = dict(result)
    second = first
    legacy_failure = legacy_alias.get("status") == "failed"
    return legacy_failure or second["error"]
""",
        "agent/planning/adversarial.py",
    )

    assert sum(item.startswith("S2-2") for item in findings) >= 2


def test_checker_rejects_inline_legacy_result_conversions() -> None:
    findings = check_source(
        """
from agent.tools.contracts import ToolResult

def legacy_subscript(result: ToolResult):
    return result.to_legacy_dict()["status"] == "failed"

def legacy_get(result: ToolResult):
    return result.to_legacy_dict().get("status") == "failed"

def dict_subscript(result: ToolResult):
    return dict(result)["status"] == "failed"

def dict_get(result: ToolResult):
    return dict(result).get("status") == "failed"
""",
        "agent/planning/adversarial.py",
    )

    assert sum(item.startswith("S2-2") for item in findings) == 4


def test_checker_propagates_literal_getattr_result_conversion_and_access() -> None:
    findings = check_source(
        """
from agent.tools.contracts import ToolResult

def should_recover(result: ToolResult):
    convert = getattr(result, "to_legacy_dict")
    legacy = convert()
    get = getattr(legacy, "get")
    return get("status") == "failed"
""",
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("S2-2") for item in findings)


def test_checker_keeps_ordinary_string_and_dict_negative_controls() -> None:
    assert check_source(
        """
def should_retry(value):
    text = value
    return "timeout" in text.lower()
""",
        "agent/planning/adversarial.py",
    ) == []
    assert check_source(
        """
def should_recover(result: dict):
    legacy = dict(result)
    return legacy.get("status") == "failed"
""",
        "agent/planning/adversarial.py",
    ) == []


def test_checker_rejects_legacy_counter_and_local_recovery_budget_mutation() -> None:
    findings = check_source(
        """
        MAX_RETRIES = 3

        def recover(state, recovery_budget):
            state.continuation_attempts += 1
            recovery_budget['remaining'] -= 1
            return state.replan_counts
        """,
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("S2-3") for item in findings)


def test_checker_resists_aliased_legacy_and_repair_budget_mutation() -> None:
    findings = check_source(
        """
        def recover(state, repair_budget):
            counters = getattr(state, "replan_counts")
            counters_alias = counters
            counters_alias.update({"llm": 1})
            budget_alias = repair_budget
            budget = budget_alias
            budget.setdefault("remaining", 0)
            return counters
        """,
        "agent/planning/adversarial.py",
    )

    assert sum(item.startswith("S2-3") for item in findings) >= 2


def test_checker_rejects_policy_defaults_tables_and_keyword_raw_scope() -> None:
    findings = check_source(
        """
        class LocalRetryPolicy:
            pass

        RETRY_LIMITS = {"llm_replans": 1}

        def recover(max_retries=2, recovery_limit=1):
            return budget.try_consume(scope="llm_replans")
        """,
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("S2-3") for item in findings)
    assert any(item.startswith("S2-4") for item in findings)


def test_checker_rejects_alternate_owner_and_raw_scope_key() -> None:
    findings = check_source(
        """
        from agent.runtime.recovery import RecoveryBudgetState

        class RetryPolicy:
            pass

        def consume(state):
            return state.recovery_budget.try_consume('llm_replans')
        """,
        "agent/planning/adversarial.py",
    )

    assert any(item.startswith("S2-4") for item in findings)


def test_checker_allows_rendering_and_explicit_mapping_edges() -> None:
    assert check_source(
        """
import re

def sanitize_error(error_message):
    return re.sub(r'\\s+', ' ', error_message.lower())
""",
        "agent/error_handler.py",
    ) == []
    assert check_source(
        """
from collections.abc import Mapping
from agent.tools.contracts import ToolResult

def project(result: ToolResult | Mapping):
    return result.get('status')
""",
        "agent/planning/deferred_execution.py",
    ) == []
