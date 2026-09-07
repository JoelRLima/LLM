from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from scripts.check_wave14_architecture import REQUIRED_MUTATION_ARMS, check_architecture

ROOT = Path(__file__).resolve().parents[3]
CHECKED_FILES = (
    "agent/planning/intent_admission.py",
    "agent/planning/intent_admission_logic.py",
    "agent/runtime/context.py",
    "agent/interaction/admission.py",
    "agent/interaction/semantic_contract.py",
    "agent/interaction/prompt.py",
    "agent/interaction/service.py",
    "agent/interaction/resolver_runtime.py",
    "agent/planning/target_grounding.py",
    "agent/planning/target_grounding_workflow.py",
    "agent/code/mutation_binding.py",
    "agent/code/workflow_application_flow_support.py",
    "agent/planning/task_scheduler.py",
    "agent/planning/graph_authority.py",
    "agent/tools/invocation_semantics_support.py",
    "agent/code/multitask.py",
)


def _file(root: Path, relative: str) -> Path:
    return root / relative


def _replace_once(root: Path, relative: str, old: str, new: str) -> None:
    path = _file(root, relative)
    source = path.read_text(encoding="utf-8")
    assert old in source, f"mutation anchor missing: {relative}: {old!r}"
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def _copy_checked_sources(destination: Path) -> None:
    for relative in CHECKED_FILES:
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _mutate_m01(root: Path) -> None:
    _replace_once(
        root,
        "agent/planning/intent_admission_logic.py",
        "    missing = required - canonical_capabilities(envelope.parent_permissions)\n",
        "    missing = required - (canonical_capabilities(envelope.parent_permissions) | canonical_capabilities((\"write\",)))\n",
    )


def _mutate_m02(root: Path) -> None:
    _replace_once(
        root,
        "agent/runtime/context.py",
        "        requested = self.permissions if permissions is None else frozenset(permissions)\n",
        "        requested = self.permissions | frozenset({\"write\"}) if permissions is None else frozenset(permissions)\n",
    )


def _mutate_m03(root: Path) -> None:
    _replace_once(
        root,
        "agent/interaction/admission.py",
        '    """Route a parsed claim without positive lexical inference."""\n',
        '    """Route a parsed claim without positive lexical inference."""\n    from agent.planning.task_semantics_effect_inference import infer_effect_semantics\n    if infer_effect_semantics(context.subject):\n        raise InteractionAdmissionError(INTERACTION_INTENT_AMBIGUOUS)\n',
    )


def _mutate_m04(root: Path) -> None:
    _replace_once(
        root,
        "agent/interaction/admission.py",
        '    """Route a parsed claim without positive lexical inference."""\n',
        '    """Route a parsed claim without positive lexical inference."""\n    from agent.planning.task_semantics_authority import parse_objective_authority\n    if parse_objective_authority(context.subject):\n        raise InteractionAdmissionError(INTERACTION_INTENT_AMBIGUOUS)\n',
    )


def _mutate_m05(root: Path) -> None:
    _replace_once(
        root,
        "agent/interaction/semantic_contract.py",
        "    try:\n        value = json.loads(\n",
        "    try:\n        value = repair_json(raw)\n        value = json.loads(\n",
    )


def _mutate_m06(root: Path) -> None:
    _replace_once(
        root,
        "agent/planning/intent_admission.py",
        "        bind_current_subject_evidence(claim, current_subject)\n",
        "        pass\n",
    )


def _mutate_m07(root: Path) -> None:
    path = _file(root, "agent/interaction/prompt.py")
    source = path.read_text(encoding="utf-8")
    source = source.replace("UNTRUSTED", "TRUSTED").replace("untrusted", "trusted")
    path.write_text(source, encoding="utf-8")


def _mutate_m08(root: Path) -> None:
    _replace_once(
        root,
        "agent/planning/target_grounding.py",
        '    if selector.kind != "symbol":\n',
        '    if selector.kind == "symbol":\n',
    )


def _mutate_m09(root: Path) -> None:
    _replace_once(
        root,
        "agent/planning/target_grounding.py",
        '        raise GroundingError("GROUNDING_NOT_FOUND")\n',
        '        return (GroundedTarget(selector.selector_id, "*", selector.kind, selector.value, "workspace-fallback", (), None, None, None, root_identity, "workspace-fallback", True),)\n',
    )


def _mutate_m10(root: Path) -> None:
    _replace_once(
        root,
        "agent/planning/target_grounding.py",
        "    if len(candidates) != 1:\n",
        "    if not candidates:\n",
    )


def _mutate_m11(root: Path) -> None:
    _replace_once(
        root,
        "agent/code/mutation_binding.py",
        "                revalidate_grounded_targets(\n                    grounded,\n                    service.root,\n                    envelope=metadata.get(\"authority_envelope\"),\n                    admitted_intent=admitted_intent or metadata.get(\"admitted_intent\"),\n                    required_capabilities=metadata.get(\n                        \"invocation_required_capabilities\", ()\n                    ),\n                    required_effects=metadata.get(\n                        \"invocation_durable_effects\", ()\n                    ),\n                )\n",
        "                pass\n",
    )


def _mutate_m12(root: Path) -> None:
    _replace_once(
        root,
        "agent/planning/target_grounding.py",
        "            assert_path_safe(path, directory=False)\n",
        "            pass\n",
    )


def _mutate_m13(root: Path) -> None:
    path = _file(root, "agent/planning/target_grounding.py")
    source = path.read_text(encoding="utf-8")
    assert "envelope.allows_write(resource)" in source
    path.write_text(
        source.replace("envelope.allows_write(resource)", "True").replace(
            "envelope.allows_write(definition.resource)", "True"
        ),
        encoding="utf-8",
    )


def _mutate_m14(root: Path) -> None:
    _replace_once(
        root,
        "agent/planning/task_scheduler.py",
        "                    permissions=frozenset(node_requirement.required_capabilities),\n",
        "                    permissions=frozenset(node.capabilities),\n",
    )


def _mutate_m15(root: Path) -> None:
    _replace_once(
        root,
        "agent/planning/graph_authority.py",
        "            set(required) | set(nested_result.required_capabilities)\n",
        "            set(required)\n",
    )


def _mutate_m16(root: Path) -> None:
    _replace_once(
        root,
        "agent/planning/task_scheduler.py",
        "        requirements = self._validate(graph, parent_context)\n",
        "        requirements = GraphAuthorityRequirements(frozenset(), ())\n",
    )


def _mutate_m17(root: Path) -> None:
    path = _file(root, "agent/code/workflow_application_flow_support.py")
    source = path.read_text(encoding="utf-8")
    source = source.replace("    assert_changeset_admitted,\n", "")
    source = source.replace("        assert_changeset_admitted(service, change_set)\n", "        pass\n")
    source = source.replace("        assert_changeset_admitted(service, change_set, preview=preview)\n", "        pass\n")
    source = source.replace(
        "        assert_changeset_admitted(\n            service,\n            change_set,\n            preview=preview,\n            revalidate=True,\n        )\n",
        "        pass\n",
    )
    path.write_text(source, encoding="utf-8")


def _mutate_m18(root: Path) -> None:
    path = _file(root, "agent/code/mutation_binding.py")
    source = path.read_text(encoding="utf-8")
    source = source.replace("    assert_resources_subset(proposed, admitted)\n", "")
    source = source.replace("        assert_resources_subset(preview_resources(preview), admitted)\n", "")
    path.write_text(source, encoding="utf-8")


def _mutate_m19(root: Path) -> None:
    _replace_once(
        root,
        "agent/code/multitask.py",
        '        targets = [str(item) for item in raw_targets] if isinstance(raw_targets, list) else []\n',
        '        targets = [str(item) for item in raw_targets] if isinstance(raw_targets, list) else []\n        test_targets = node.metadata.get("test_targets", [])\n        if isinstance(test_targets, list):\n            targets.extend(str(item) for item in test_targets)\n',
    )


def _mutate_m20(root: Path) -> None:
    _replace_once(
        root,
        "agent/code/workflow_application_flow_support.py",
        "        assert_result_mutation_admitted(service, result)\n",
        "        pass\n",
    )


def _mutate_m21(root: Path) -> None:
    _replace_once(
        root,
        "agent/planning/intent_admission_logic.py",
        '    normalized = effect.effect.casefold()\n',
        '    confidence = 0.0\n    if confidence < 0.5:\n        raise IntentAdmissionError("low confidence")\n    normalized = effect.effect.casefold()\n',
    )


def _mutate_m22(root: Path) -> None:
    _replace_once(
        root,
        "agent/interaction/admission.py",
        '    """Route a parsed claim without positive lexical inference."""\n',
        '    """Route a parsed claim without positive lexical inference."""\n    from agent.planning.task_semantics_effect_inference import infer_effect_semantics\n    if not infer_effect_semantics(context.subject):\n        raise InteractionAdmissionError(INTERACTION_INTENT_AMBIGUOUS)\n',
    )


def _mutate_m11(root: Path) -> None:
    """Remove the entire pre-commit grounding revalidation call in the mutant."""
    path = _file(root, "agent/code/mutation_binding.py")
    source = path.read_text(encoding="utf-8")
    start = source.index("                revalidate_grounded_targets(\n")
    end = source.index("                revalidate_grounded_authority(\n", start)
    path.write_text(source[:start] + "                pass\n" + source[end:], encoding="utf-8")


MUTATIONS: tuple[tuple[str, Callable[[Path], None]], ...] = tuple(
    (f"W14-M{index:02d}", mutation)
    for index, mutation in enumerate(
        (
            _mutate_m01,
            _mutate_m02,
            _mutate_m03,
            _mutate_m04,
            _mutate_m05,
            _mutate_m06,
            _mutate_m07,
            _mutate_m08,
            _mutate_m09,
            _mutate_m10,
            _mutate_m11,
            _mutate_m12,
            _mutate_m13,
            _mutate_m14,
            _mutate_m15,
            _mutate_m16,
            _mutate_m17,
            _mutate_m18,
            _mutate_m19,
            _mutate_m20,
            _mutate_m21,
            _mutate_m22,
            ),
        start=1,
    )
)


def test_clean_candidate_passes_before_mutation_campaign() -> None:
    assert check_architecture(ROOT) == []


def test_each_wave14_mutation_is_injected_in_an_isolated_copy_and_detected(tmp_path: Path) -> None:
    assert tuple(arm for arm, _ in MUTATIONS) == REQUIRED_MUTATION_ARMS
    detected: set[str] = set()
    for arm, mutate in MUTATIONS:
        mutant_root = tmp_path / arm
        _copy_checked_sources(mutant_root)
        mutate(mutant_root)
        findings = check_architecture(mutant_root)
        assert any(finding.rule_id == arm for finding in findings), (
            f"{arm} mutation was not detected: "
            + "; ".join(finding.format() for finding in findings)
        )
        detected.add(arm)
    assert tuple(sorted(detected)) == REQUIRED_MUTATION_ARMS
