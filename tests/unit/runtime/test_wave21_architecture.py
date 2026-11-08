"""Remote-safe tests for generic W21 graph and target-policy mechanics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.check_wave21_architecture import FROZEN_GRAPH, check_architecture
from scripts.w21_architecture import RepositorySource, SourceLayout, build_graph
from scripts.w21_architecture.policy import classify_edge, stable_violation_id, w22_owner_violations

ROOT = Path(__file__).resolve().parents[3]

PREVIOUS_EXACT_ROUTES = (
    ("agent.reporting.run_snapshot", "agent.planning.outcome_planning"),
    ("agent.planning.outcome_verification", "agent.task_definition.errors"),
    ("agent.planning.plan_coverage", "agent.task_definition.resolver"),
    ("agent.planning.plan_coverage", "agent.task_definition.models"),
    ("agent.planning.outcome_verification", "agent.task_definition.models"),
    ("agent.planning.task_completion_constraints", "agent.runtime.validation_evidence"),
    ("agent.reporting.run_projection_facts", "agent.runtime.validation_evidence"),
    ("agent.reporting.run_receipt_builder", "agent.runtime.validation_evidence"),
    ("agent.planning.outcome_definition", "agent.task_definition.resolver"),
    ("agent.planning.outcome_definition", "agent.task_definition.models"),
    ("agent.planning.outcome_verification", "agent.task_definition.verification_model"),
    ("agent.planning.requirement_continuation", "agent.runtime.budget"),
    ("agent.planning.plan_coverage", "agent.task_definition.requirement_refs"),
    ("agent.planning.plan_coverage", "agent.execution_state"),
    ("agent.state_checkpointing", "agent.state_replan"),
    ("agent.planning.requirement_verification", "agent.task_definition.verification_model"),
    ("agent.planning.outcome_planning", "agent.task_definition.models"),
    ("agent.planning.plan_coverage", "agent.contracts"),
    ("agent.planning.requirement_continuation", "agent.runtime.recovery"),
    ("agent.planning.outcome_planning", "agent.task_definition.errors"),
)

NEW_EXACT_ROUTES = (
    ("agent.evaluation.trace_request_summary", "agent.runtime.budget_estimation"),
    ("agent.code.workflow_proposal_runtime", "agent.llm.contracts"),
    ("agent.planning.tool_disclosure_selection", "agent.runtime.recovery"),
    ("agent.llm.stage_context", "agent.runtime.execution_profile"),
    ("agent.task_definition.compiler_prompts", "agent.llm.admitted_decisions"),
    ("agent.runtime.logging", "agent.observability.log_formatter"),
    ("agent.evaluation.trace_diagnostics", "agent.llm.decision_contract"),
    ("agent.orchestration.compact_route", "agent.planning.task_semantics"),
    ("agent.evaluation.weak_model_execution", "agent.variants.models"),
    ("agent.planning.tool_disclosure_selection", "agent.llm.decision_contract"),
    ("agent.llm.providers.openai_compatible", "agent.runtime.request_measurement_reuse"),
    ("agent.evaluation.weak_model_execution", "agent.approval"),
    ("agent.code.workflow_proposal_runtime", "agent.runtime.model_call"),
    ("agent.planning.boundary_discovery", "agent.llm.admitted_decisions"),
    ("agent.evaluation.weak_model_fixtures", "agent.llm.contracts"),
    ("agent.planning.tool_disclosure_selection", "agent.llm.admitted_decisions"),
    ("agent.evaluation.trace_request_summary", "agent.llm.decision_contract"),
    ("agent.evaluation.weak_model_execution", "agent.llm.contracts"),
    ("agent.planning.validation_repair_contracts", "agent.llm.plan_association_contract"),
    ("agent.orchestration.compact_route", "agent.runtime.execution_profile"),
    ("agent.planning.tool_disclosure_selection", "agent.llm.tool_discovery_contract"),
    ("agent.evaluation.weak_model_execution", "agent.runtime.config_repository"),
    ("agent.evaluation.weak_model_execution", "agent.tools.authority"),
    ("agent.planning.disclosure_cache", "agent.runtime.correlation"),
    ("agent.evaluation.weak_model_fixtures", "agent.llm.decision_contract"),
    ("agent.evaluation.trace_diagnostics", "agent.llm.structured_output"),
    ("agent.orchestration.compact_route", "agent.runtime.task_directives"),
    ("agent.planning.boundary_discovery", "agent.llm.decision_contract"),
)

BASE_DIRECTION_IDS = frozenset(
    {
        "W21-DIR-SKILLS-TOOLS",
        "W21-DIR-PLANNING-TOOLS",
        "W21-DIR-INTERACTION-PLANNING",
        "W21-DIR-TOOLS-SKILLS-ADAPTER",
        "W21-DIR-SKILLS-TOOLS-CONSTRUCTION-ADAPTER",
    }
)

ADDED_DIRECTION_IDS = frozenset(
    {
        "W21-DIR-REPORT-SNAPSHOT-OUTCOME-PLANNING",
        "W21-DIR-OUTCOME-VERIFICATION-TASK-ERRORS",
        "W21-DIR-PLAN-COVERAGE-TASK-RESOLVER",
        "W21-DIR-PLAN-COVERAGE-TASK-MODELS",
        "W21-DIR-OUTCOME-VERIFICATION-TASK-MODELS",
        "W21-DIR-COMPLETION-CONSTRAINTS-VALIDATION-EVIDENCE",
        "W21-DIR-REPORT-PROJECTION-VALIDATION-EVIDENCE",
        "W21-DIR-REPORT-RECEIPT-VALIDATION-EVIDENCE",
        "W21-DIR-OUTCOME-DEFINITION-TASK-RESOLVER",
        "W21-DIR-OUTCOME-DEFINITION-TASK-MODELS",
        "W21-DIR-OUTCOME-VERIFICATION-TASK-VERIFICATION",
        "W21-DIR-REQUIREMENT-CONTINUATION-BUDGET",
        "W21-DIR-PLAN-COVERAGE-REQUIREMENT-REFS",
        "W21-DIR-PLAN-COVERAGE-EXECUTION-STATE",
        "W21-DIR-STATE-CHECKPOINT-REPLAN",
        "W21-DIR-REQUIREMENT-VERIFICATION-TASK-VERIFICATION",
        "W21-DIR-OUTCOME-PLANNING-TASK-MODELS",
        "W21-DIR-PLAN-COVERAGE-CONTRACTS",
        "W21-DIR-REQUIREMENT-CONTINUATION-RECOVERY",
        "W21-DIR-OUTCOME-PLANNING-TASK-ERRORS",
        "W21-DIR-TRACE-BUDGET-ESTIMATION",
        "W21-DIR-WORKFLOW-PROPOSAL-LLM-CONTRACTS",
        "W21-DIR-TOOL-DISCLOSURE-RECOVERY",
        "W21-DIR-STAGE-EXECUTION-PROFILE",
        "W21-DIR-COMPILER-PROMPTS-ADMITTED-DECISION",
        "W21-DIR-RUNTIME-LOG-FORMATTER",
        "W21-DIR-TRACE-DECISION-CONTRACT",
        "W21-DIR-COMPACT-PLANNING-SEMANTICS",
        "W21-DIR-WEAK-MODEL-VARIANTS",
        "W21-DIR-TOOL-DISCLOSURE-DECISION-CONTRACT",
        "W21-DIR-OPENAI-MEASUREMENT-REUSE",
        "W21-DIR-WEAK-MODEL-APPROVAL",
        "W21-DIR-WORKFLOW-PROPOSAL-MODEL-CALL",
        "W21-DIR-BOUNDARY-DISCOVERY-ADMITTED",
        "W21-DIR-WEAK-MODEL-RESPONSE-CONTRACTS",
        "W21-DIR-TOOL-DISCLOSURE-ADMITTED",
        "W21-DIR-TRACE-REQUEST-CONTRACT",
        "W21-DIR-WEAK-MODEL-GATEWAY-CONTRACT",
        "W21-DIR-VALIDATION-PLAN-ASSOCIATION",
        "W21-DIR-COMPACT-EXECUTION-PROFILE",
        "W21-DIR-TOOL-DISCLOSURE-LIMIT",
        "W21-DIR-WEAK-MODEL-CONFIG-REPOSITORY",
        "W21-DIR-WEAK-MODEL-AUTHORITY",
        "W21-DIR-DISCLOSURE-RUN-CORRELATION",
        "W21-DIR-WEAK-MODEL-DECISION-CONTRACT",
        "W21-DIR-TRACE-STRUCTURED-OUTPUT",
        "W21-DIR-COMPACT-TASK-DIRECTIVES",
        "W21-DIR-BOUNDARY-DECISION-CONTRACT",
    }
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _authority(root: Path, *, artifact: str = "W21_EPOCH_AUTHORITY") -> tuple[Path, str]:
    if artifact == "W21_EPOCH_AUTHORITY":
        document = {
            "artifact": artifact,
            "status": "FROZEN",
            "epoch_id": "W99-X-001",
            "baseline": {"epoch_base_sha": "future-base", "epoch_base_tree": "future-tree"},
            "worktree": {"absolute_path": str(root), "local_branch": "future/lane"},
            "write_scope": {"write_paths": [], "new_path_prefixes": [], "required_new_paths": []},
            "forbidden_paths": [],
            "forbidden_path_prefixes": [],
            "required_transition_violation_removals": [],
            "accepted_removed_transition_violation_ids": [],
            "higher_authority": {},
        }
    else:
        document = {
            "artifact": artifact,
            "status": "FROZEN",
            "corrective_id": "W99-X-C001",
            "base": {"sha": "future-base", "tree": "future-tree"},
            "worktree": {"absolute_path": str(root), "branch": "future/lane"},
            "write_paths": [],
            "new_path_prefixes": [],
            "required_outputs": [],
            "forbidden_paths": [],
            "forbidden_path_prefixes": [],
            "required_transition_violation_removals": [],
            "accepted_removed_transition_violation_ids": [],
            "higher_authority": {},
        }
    path = root / "authority.json"
    payload = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def test_completed_graph_signature_is_distinct_from_the_pre_migration_baseline() -> None:
    assert build_graph(RepositorySource(ROOT)).signatures() != FROZEN_GRAPH


def test_transition_mode_accepts_the_completed_migration() -> None:
    result = check_architecture(ROOT, mode="transition")
    assert result.passed
    assert not result.violations
    assert not result.new_ids


def test_strict_mode_is_green_after_all_lanes_close_the_baseline() -> None:
    result = check_architecture(ROOT, mode="strict")
    assert result.passed
    assert not result.violations


def test_exact_canonical_directions_admit_only_the_named_module_pairs() -> None:
    policy = json.loads((ROOT / "quality/architecture_policy.json").read_text(encoding="utf-8"))
    routes = (*PREVIOUS_EXACT_ROUTES, *NEW_EXACT_ROUTES)
    directions = policy["explicit_canonical_directions"]
    by_id = {row["direction_id"]: row for row in directions}
    assert len(by_id) == len(directions)
    assert set(by_id) == BASE_DIRECTION_IDS | ADDED_DIRECTION_IDS
    added = [by_id[direction_id] for direction_id in ADDED_DIRECTION_IDS]
    assert {(row["source"], row["destination"]) for row in added} == set(routes)
    assert all(
        row.get("exact") is True
        and row.get("class") == "canonical"
        and isinstance(row.get("constraint"), str)
        and row["constraint"].strip()
        for row in added
    )

    for source, destination in routes:
        decision = classify_edge(source, destination, "normal_import", policy=policy)
        assert decision.edge_class == "canonical"
        assert decision.authority_id.startswith("W21-DIR-")


def test_exact_canonical_directions_do_not_authorize_neighbors_or_higher_owners() -> None:
    policy = json.loads((ROOT / "quality/architecture_policy.json").read_text(encoding="utf-8"))
    forbidden_routes = (
        ("agent.task_definition.models", "agent.planning.outcome_planning"),
        ("agent.planning.outcome_planning", "agent.task_definition.resolver"),
        ("agent.planning.plan_coverage.extensions", "agent.task_definition.models"),
        ("agent.planning.plan_coverage", "agent.task_definition.models.values"),
        ("agent.planning.outcome_planning", "agent.application.task_execution"),
        ("agent.planning.outcome_planning", "agent.interfaces.cli.controller"),
        ("agent.runtime.validation_evidence", "agent.tools.result_adapter"),
        ("agent.tools.result_adapter", "agent.runtime.validation_evidence"),
        ("agent.runtime.validation_evidence.extensions", "agent.tools.result_adapter"),
        ("agent.runtime.validation_evidence", "agent.tools.result_adapter.experimental"),
    )

    for source, destination in forbidden_routes:
        decision = classify_edge(source, destination, "normal_import", policy=policy)
        assert decision.edge_class == "forbidden"
        assert decision.authority_id == "W21-POLICY-NEW-EDGE"

    for source, destination in (*PREVIOUS_EXACT_ROUTES, *NEW_EXACT_ROUTES):
        for neighbor_source, neighbor_destination in (
            (source + ".unapproved_neighbor", destination),
            (source, destination + ".unapproved_neighbor"),
        ):
            decision = classify_edge(
                neighbor_source, neighbor_destination, "normal_import", policy=policy
            )
            assert decision.edge_class == "forbidden"
            assert decision.authority_id == "W21-POLICY-NEW-EDGE"


def test_stable_violation_id_uses_only_the_contract_payload() -> None:
    assert stable_violation_id("agent.tools.tool_registry", "agent.planning.tool_metadata", "normal_import", "W21-R-META-001") == "W21-V-3edd5d685e7ef437"


def test_empty_module_type_checking_else_and_declarative_edges(tmp_path: Path) -> None:
    _write(tmp_path, "agent/__init__.py", "")
    _write(tmp_path, "agent/planning/__init__.py", "")
    _write(tmp_path, "agent/planning/core.py", "")
    _write(tmp_path, "agent/planning/other.py", "")
    _write(tmp_path, "agent/tools/__init__.py", "from .edge import value\n")
    _write(
        tmp_path,
        "agent/tools/edge.py",
        """import importlib
from typing import TYPE_CHECKING
from agent.planning.core import value

if TYPE_CHECKING:
    from agent.planning.core import Value
else:
    from agent.planning.other import Other

def load():
    from agent.planning.core import value as local_value
    return importlib.import_module('agent.planning.core'), local_value
""",
    )
    _write(tmp_path, "agent/skills/__init__.py", "")
    _write(tmp_path, "agent/skills/impl.py", "")
    _write(tmp_path, "agent/skills/registry.py", "")
    _write(tmp_path, "agent/skills/catalog.py", "SkillSpec(module='agent.skills.impl')\n")
    _write(tmp_path, "agent/interfaces/cli/__init__.py", "")
    _write(tmp_path, "agent/interfaces/cli/handler.py", "")
    _write(tmp_path, "agent/interfaces/cli/action_registry.py", "CliActionBinding(handler_owner='agent.interfaces.cli.handler.run')\n")
    graph = build_graph(RepositorySource(tmp_path))
    edge = next(item for item in graph.architecture_union_edges if item["source_module"] == "agent.tools.edge" and item["destination_module"] == "agent.planning.other")
    assert "type_checking" not in edge["edge_kinds"]
    edge = next(item for item in graph.architecture_union_edges if item["source_module"] == "agent.tools.edge" and item["destination_module"] == "agent.planning.core")
    assert {"normal_import", "type_checking", "local_import", "literal_dynamic_import"}.issubset(edge["edge_kinds"])
    assert any(item["source_module"] == "agent.skills.registry" and item["destination_module"] == "agent.skills.impl" for item in graph.declarative_edges)
    assert any(item["destination_module"] == "agent.interfaces.cli.handler" for item in graph.declarative_edges)


def test_source_root_layout_keeps_repository_source_and_import_roots_distinct(tmp_path: Path) -> None:
    _write(tmp_path, "src/agent/__init__.py", "")
    _write(tmp_path, "src/agent/foo.py", "")
    source = RepositorySource(tmp_path, "src-agent")

    assert source.root == tmp_path.resolve()
    assert source.layout is not None
    assert source.layout.python_source_root == (tmp_path / "src").resolve()
    assert source.layout.package_directory == (tmp_path / "src/agent").resolve()
    assert source.module_paths()["agent.foo"] == (tmp_path / "src/agent/foo.py").resolve()


def test_product_source_path_maps_to_full_import_namespace(tmp_path: Path) -> None:
    _write(tmp_path, "src/llm_agent/__init__.py", "")
    _write(tmp_path, "src/llm_agent/agent/__init__.py", "")
    _write(tmp_path, "src/llm_agent/agent/foo.py", "")
    _write(tmp_path, "src/llm_agent/application/__init__.py", "")
    _write(tmp_path, "src/llm_agent/application/core.py", "")
    source = RepositorySource(tmp_path, "final-w22")
    graph = build_graph(source)

    assert source.layout is not None
    assert source.layout.import_module_root == "llm_agent"
    assert source.layout.agent_module_root == "llm_agent.agent"
    assert "llm_agent.agent.foo" in source.module_paths()
    assert "llm_agent.application.core" in source.module_paths()
    assert {item["module"] for item in graph.modules} == {
        "llm_agent", "llm_agent.agent", "llm_agent.agent.foo",
        "llm_agent.application", "llm_agent.application.core",
    }


def test_w22_checker_rejects_agent_to_application_owner_edge(tmp_path: Path) -> None:
    _write(tmp_path, "src/llm_agent/__init__.py", "")
    _write(tmp_path, "src/llm_agent/agent/__init__.py", "")
    _write(tmp_path, "src/llm_agent/agent/interaction/__init__.py", "")
    _write(
        tmp_path,
        "src/llm_agent/agent/interaction/service.py",
        "from llm_agent.application.task_directives import parse_task_request\n",
    )
    _write(tmp_path, "src/llm_agent/application/__init__.py", "")
    _write(tmp_path, "src/llm_agent/application/task_directives.py", "def parse_task_request(): pass\n")
    source = RepositorySource(tmp_path, "final-w22")
    graph = build_graph(source)
    assert source.layout is not None
    policy = json.loads((ROOT / "quality/architecture_w22_policy.json").read_text(encoding="utf-8"))

    violations = w22_owner_violations(
        list(graph.architecture_union_edges), source.layout, policy
    )

    assert any(
        item.target_rule_id == "W22-OWNER-AGENT-APPLICATION-001"
        and item.source_module == "llm_agent.agent.interaction.service"
        and item.destination_module == "llm_agent.application.task_directives"
        for item in violations
    )


def test_w22_checker_preserves_application_to_agent_direction(tmp_path: Path) -> None:
    layout = SourceLayout.for_profile(tmp_path, "final-w22")
    policy = json.loads((ROOT / "quality/architecture_w22_policy.json").read_text(encoding="utf-8"))
    edge = {
        "source_module": "llm_agent.application.agent_boundary",
        "destination_module": "llm_agent.agent.application",
        "edge_kinds": ["normal_import"],
    }

    assert w22_owner_violations([edge], layout, policy) == []


def test_source_layout_rejects_unbounded_profile_names(tmp_path: Path) -> None:
    try:
        SourceLayout.for_profile(tmp_path, "arbitrary-plugin-root")
    except ValueError as exc:
        assert "unsupported W22 source layout profile" in str(exc)
    else:
        raise AssertionError("unbounded layout profile was accepted")


def test_generic_future_authority_does_not_infer_the_frozen_graph_rule(tmp_path: Path) -> None:
    for relative in ("agent/__init__.py", "agent/capabilities.py", "agent/operation/__init__.py", "agent/operation/contracts.py"):
        _write(tmp_path, relative, "")
    _write(tmp_path, "agent/operation/contracts.py", "from agent.capabilities import Capability\n")
    authority, sha = _authority(tmp_path)
    policy = json.loads((ROOT / "quality/architecture_policy.json").read_text(encoding="utf-8"))
    compatibility = json.loads((ROOT / "quality/architecture_compatibility.json").read_text(encoding="utf-8"))
    result = check_architecture(tmp_path, policy=policy, compatibility=compatibility, baseline={"graph_signatures": {}, "baseline_cross_package_module_pairs": [], "frozen_transition_violations": []}, authority_path=authority, authority_sha256=sha)
    assert result.passed
    assert not result.authority_errors


def test_wrong_authority_sha_is_rejected_before_semantic_trust(tmp_path: Path) -> None:
    authority, _ = _authority(tmp_path)
    result = check_architecture(tmp_path, policy={}, compatibility={}, baseline={}, authority_path=authority, authority_sha256="0" * 64)
    assert not result.passed
    assert any("SHA-256 mismatch" in error for error in result.authority_errors)


def test_new_cross_package_pair_uses_closed_world_identity(tmp_path: Path) -> None:
    for relative in ("agent/__init__.py", "agent/memory/__init__.py", "agent/memory/new.py", "agent/reporting/__init__.py", "agent/reporting/new.py"):
        _write(tmp_path, relative, "")
    _write(tmp_path, "agent/memory/new.py", "from agent.reporting import new\n")
    result = check_architecture(tmp_path, policy={}, compatibility={}, baseline={"graph_signatures": {}, "baseline_cross_package_module_pairs": [], "frozen_transition_violations": []})
    assert not result.passed
    assert any(item.target_rule_id == "W21-POLICY-NEW-EDGE" for item in result.violations)


def test_required_removal_and_reintroduction_sets_are_generic() -> None:
    from scripts.check_wave21_architecture import _transition_sets

    assert _transition_sets({"kept"}, {"kept", "removed"}, {"removed"}, {"reintroduced"}) == (set(), set(), set())
    assert _transition_sets({"reintroduced"}, {"reintroduced"}, set(), {"reintroduced"})[2] == {"reintroduced"}


def test_required_removal_and_reintroduction_are_checked_end_to_end(tmp_path: Path) -> None:
    violation_id = stable_violation_id(
        "agent.memory.new",
        "agent.reporting.new",
        "normal_import",
        "W21-POLICY-NEW-EDGE",
    )
    baseline = {
        "graph_signatures": {},
        "baseline_cross_package_module_pairs": [],
        "frozen_transition_violations": [{"violation_id": violation_id}],
    }
    policy: dict[str, object] = {}
    compatibility: dict[str, object] = {}
    removal_root = tmp_path / "removal"
    removal_root.mkdir()
    authority_path, _ = _authority(removal_root, artifact="W21_CORRECTIVE_AUTHORITY")
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority["required_transition_violation_removals"] = [violation_id]
    removal_payload = json.dumps(authority, separators=(",", ":"), sort_keys=True).encode("utf-8")
    authority_path.write_bytes(removal_payload)
    removal_sha = hashlib.sha256(removal_payload).hexdigest()
    for relative in ("agent/__init__.py", "agent/memory/__init__.py", "agent/reporting/__init__.py"):
        _write(tmp_path / "removal", relative, "")
    removed = check_architecture(
        removal_root,
        policy=policy,
        compatibility=compatibility,
        baseline=baseline,
        authority_path=authority_path,
        authority_sha256=removal_sha,
    )
    assert removed.passed
    assert not removed.required_removal_ids

    reintroduced_root = tmp_path / "reintroduced"
    for relative in ("agent/__init__.py", "agent/memory/__init__.py", "agent/reporting/__init__.py", "agent/reporting/new.py"):
        _write(reintroduced_root, relative, "")
    _write(reintroduced_root, "agent/memory/new.py", "from agent.reporting import new\n")
    reintroduced_authority_path, _ = _authority(reintroduced_root, artifact="W21_CORRECTIVE_AUTHORITY")
    reintroduced_authority = json.loads(reintroduced_authority_path.read_text(encoding="utf-8"))
    reintroduced_authority["accepted_removed_transition_violation_ids"] = [violation_id]
    reintroduced_payload = json.dumps(reintroduced_authority, separators=(",", ":"), sort_keys=True).encode("utf-8")
    reintroduced_authority_path.write_bytes(reintroduced_payload)
    reintroduced_sha = hashlib.sha256(reintroduced_payload).hexdigest()
    result = check_architecture(
        reintroduced_root,
        policy=policy,
        compatibility=compatibility,
        baseline=baseline,
        authority_path=reintroduced_authority_path,
        authority_sha256=reintroduced_sha,
    )
    assert not result.passed
    assert result.reintroduced_ids == {violation_id}
