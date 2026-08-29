import pytest

from scripts.check_wave4_architecture import check_source, run_checks


def _gates(source: str, relative: str) -> set[str]:
    return {finding.split(":", 1)[0] for finding in check_source(source, relative)}


@pytest.mark.parametrize(
    "source",
    (
        "from uuid import uuid4 as make_id\ndef bad(): return make_id()",
        "from uuid import uuid4\nmake_id = uuid4\ndef bad(): return make_id()",
        "import uuid as ids\nmake_id = ids.uuid4\ndef bad(): return make_id()",
    ),
)
def test_s1_resolves_import_assignment_and_module_uuid_aliases(source: str) -> None:
    assert "W4-S1" in _gates(source, "agent/evaluation/adversarial.py")


def test_s1_leaves_unrelated_uuid_domain_clean() -> None:
    source = "from another_domain import uuid4 as make_id\ndef ok(): return make_id()"
    assert "W4-S1" not in _gates(source, "agent/evaluation/adversarial.py")


@pytest.mark.parametrize("relative", ("agent/reporting/task_report.py", "agent/tool_executor.py"))
def test_s1_rejects_correlation_identity_assignment_in_non_owner(relative: str) -> None:
    source = "from uuid import uuid4\ndef bad():\n    run_id = uuid4().hex\n    return run_id"
    assert "W4-S1" in _gates(source, relative)


def test_s1_rejects_uuid_alias_without_an_explicit_non_correlation_domain() -> None:
    source = "from uuid import uuid4 as make_id\ndef bad(): return make_id()"
    assert "W4-S1" in _gates(source, "agent/reporting/task_report.py")


def test_s4_flags_status_recomputation_inside_allowlisted_function_name() -> None:
    source = (
        "from agent.runtime.operational_outcome import normalize_terminal_status\n"
        "def _canonical_public_status(orchestrator, requested_status):\n"
        "    return normalize_terminal_status(explicit_status=requested_status)"
    )
    assert "W4-S4" in _gates(source, "agent/reporting/run_receipt.py")


def test_s5_flags_metric_reconstruction_inside_allowlisted_function_name() -> None:
    source = (
        "from agent.reporting.metrics import project_run_metrics\n"
        "def _aggregate_metrics(history):\n"
        "    return project_run_metrics(history)"
    )
    assert "W4-S5" in _gates(source, "agent/reporting/task_report.py")


@pytest.mark.parametrize(
    "expression",
    (
        "{'type': 'warning', 'data': {}}",
        "dict(type='warning', data={})",
    ),
)
def test_s2_detects_literal_and_dict_constructor_event_envelopes(expression: str) -> None:
    source = f"def bad(state):\n    event = {expression}\n    state.add_event(event)"
    assert "W4-S2" in _gates(source, "agent/orchestration/adversarial.py")


def test_s2_detects_assignment_alias_before_direct_event_append() -> None:
    source = (
        "def bad(state):\n"
        "    first = dict(type='warning', data={})\n"
        "    second = first\n"
        "    state.events.append(second)"
    )
    assert "W4-S2" in _gates(source, "agent/orchestration/adversarial.py")


def test_s2_leaves_unrelated_type_data_payload_clean() -> None:
    source = (
        "def ok(queue):\n"
        "    payload = dict(type='content', data={})\n"
        "    queue.append(payload)"
    )
    assert "W4-S2" not in _gates(source, "agent/orchestration/adversarial.py")


@pytest.mark.parametrize(
    "source",
    (
        "from agent.runtime.correlation import RunCorrelation as RC\ndef bad(): return RC.fresh()",
        "import agent.runtime.correlation as corr\ndef bad(): return corr.RunCorrelation.fresh()",
        "from agent.runtime.correlation import RunCorrelation as RC\nFactory = RC\nmake = Factory.fresh\ndef bad(): return make()",
    ),
)
def test_s3_resolves_correlation_factory_aliases(source: str) -> None:
    assert "W4-S3" in _gates(source, "agent/reporting/adversarial.py")


def test_s3_allows_explicit_snapshot_correlation_projection() -> None:
    source = "def ok(snapshot): return snapshot.correlation"
    assert "W4-S3" not in _gates(source, "agent/reporting/adversarial.py")


@pytest.mark.parametrize(
    ("gate", "import_line", "call"),
    (
        (
            "W4-S4",
            "from agent.runtime.operational_outcome import normalize_terminal_status as nts",
            "nts(explicit_status='failed')",
        ),
        (
            "W4-S5",
            "from agent.reporting.metrics import project_run_metrics as prm",
            "prm([])",
        ),
    ),
)
def test_s4_s5_allow_only_verified_snapshotless_branch(
    gate: str,
    import_line: str,
    call: str,
) -> None:
    source = (
        f"{import_line}\n"
        "def build(snapshot):\n"
        "    if snapshot is None:\n"
        f"        legacy = {call}\n"
        "    else:\n"
        f"        forbidden = {call}\n"
        "    return legacy if snapshot is None else forbidden"
    )
    findings = [item for item in check_source(source, "agent/reporting/run_receipt_builder.py") if item.startswith(gate)]
    assert len(findings) == 1


@pytest.mark.parametrize(
    ("gate", "source"),
    (
        (
            "W4-S4",
            "import agent.runtime.operational_outcome as outcome\n"
            "def bad(): return outcome.normalize_terminal_status(explicit_status='failed')",
        ),
        (
            "W4-S5",
            "import agent.reporting.metrics as metrics\n"
            "def bad(): return metrics.project_run_metrics([])",
        ),
    ),
)
def test_s4_s5_detect_module_qualified_recomputation(gate: str, source: str) -> None:
    assert gate in _gates(source, "agent/reporting/run_receipt_builder.py")


def test_s6_rejects_task_identity_generation_and_allows_report_identity() -> None:
    bad = "class Builder:\n    def generate_task_id(self): return 'task'"
    good = (
        "from uuid import uuid4 as make_id\n"
        "def generate_report_id(): return make_id().hex"
    )
    assert "W4-S6" in _gates(bad, "agent/reporting/adversarial.py")
    assert "W4-S6" not in _gates(good, "agent/reporting/task_report.py")


def test_hardened_repository_checker_passes() -> None:
    assert run_checks() == []
