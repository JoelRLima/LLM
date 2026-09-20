from __future__ import annotations

from pathlib import Path

from agent.interfaces.cli.thinking_presets import (
    DEFAULT_THINKING_BUDGET,
    THINKING_LABEL_BY_BUDGET,
    THINKING_PRESET_BY_KEY,
)
from scripts import check_wave19_architecture as checker
from scripts.compatibility_ledger import LEDGER, validate_ledger

ROOT = Path(__file__).resolve().parents[3]


def test_s07_retired_modules_are_absent_and_no_retired_imports_remain() -> None:
    assert checker._check_cleanup_retired_modules(ROOT) == []


def test_s07_ledger_contains_exact_cleanup_edges_and_is_closed() -> None:
    assert validate_ledger() == []
    by_id = {edge.edge_id: edge for edge in LEDGER}
    assert {
        "W19-S07-R01",
        "W19-S07-R02",
        "W19-S07-R03",
        "W19-S07-R04",
        "W19-S07-R05",
        "W19-S07-R06",
        "W19-S07-R07",
        "W19-S07-R08",
        "W19-S07-R09",
        "W19-S07-P01",
        "W19-S07-P02",
    } <= by_id.keys()
    assert {by_id[key].disposition for key in by_id if key.startswith("W19-S07-R")} == {"REMOVE"}
    assert by_id["W19-S07-P01"].disposition == "RETAIN_SUPPORTED_BOUNDARY"
    assert by_id["W19-S07-P02"].disposition == "RETAIN_SUPPORTED_BOUNDARY"
    assert checker._check_ledger_closure(ROOT) == []


def test_s07_converged_single_owners_and_exact_thinking_presets() -> None:
    assert checker._check_config_convergence(ROOT) == []
    assert checker._check_path_compatibility_allowlist(ROOT) == []
    assert checker._check_single_owner_shapes(ROOT) == []
    assert checker._check_cleanup_dag(ROOT) == []
    assert THINKING_PRESET_BY_KEY == {"B": 512, "M": 1024, "A": 2048}
    assert THINKING_LABEL_BY_BUDGET == {512: "BAIXO", 1024: "MÉDIO", 2048: "ALTO"}
    assert DEFAULT_THINKING_BUDGET == 1024
    app_source = (ROOT / "agent/interfaces/cli/app.py").read_text(encoding="utf-8")
    assert "NIVEIS_THINKING" not in app_source
    assert "obter_status_think" not in app_source


def test_s07_007_rejects_reintroduced_query_modules_and_semantic_shadows(tmp_path: Path) -> None:
    application_services = tmp_path / "agent" / "application_services"
    application_services.mkdir(parents=True)
    retired = application_services / "query_validation.py"
    retired.write_text("def validate_arguments(request, kind):\n    return {}\n", encoding="utf-8")
    shadow = application_services / "query_shadow.py"
    shadow.write_text("class WorkspaceQueryResult:\n    pass\n", encoding="utf-8")

    findings = checker._check_single_owner_shapes(tmp_path)

    assert {finding.path for finding in findings} == {
        "agent/application_services/query_shadow.py",
        "agent/application_services/query_validation.py",
    }
    assert all(finding.rule_id == "W19-S07-007" for finding in findings)
