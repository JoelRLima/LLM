"""Remote-safe tests for generic W21 graph and target-policy mechanics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.check_wave21_architecture import FROZEN_GRAPH, FROZEN_TRANSITION_IDS, check_architecture
from scripts.w21_architecture import RepositorySource, build_graph
from scripts.w21_architecture.policy import stable_violation_id

ROOT = Path(__file__).resolve().parents[3]


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


def test_baseline_graph_signature_is_frozen_only_when_explicitly_requested() -> None:
    assert build_graph(RepositorySource(ROOT)).signatures() == FROZEN_GRAPH


def test_transition_mode_accepts_the_exact_frozen_set() -> None:
    result = check_architecture(ROOT, mode="transition", require_baseline_signature=True)
    assert result.passed
    assert {item.violation_id for item in result.violations} == FROZEN_TRANSITION_IDS
    assert not result.new_ids


def test_strict_mode_is_red_until_later_lanes_close_the_baseline() -> None:
    result = check_architecture(ROOT, mode="strict")
    assert not result.passed
    assert len(result.violations) == 27
    assert {item.violation_id for item in result.violations} == FROZEN_TRANSITION_IDS


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
