from __future__ import annotations

from pathlib import Path

from scripts import check_wave12_architecture as checker


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _rules(violations: list[checker.ArchitectureViolation]) -> set[str]:
    return {item.rule_id for item in violations}


def test_real_repository_passes_wave12_checker() -> None:
    root = Path(__file__).parents[3]
    assert checker.check_architecture(root) == []


def test_s44_accepts_short_circuit_task_cancel_guard(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/application_lifecycle.py",
        "class ApplicationLifecycleMixin:\n"
        "    def cancel(self):\n"
        "        if not self._closed and not self.interaction_service().cancel_active_model_call():\n"
        "            self.orchestrator.cancel_task()\n",
    )

    assert checker._check_s44(tmp_path) == []


def test_s44_preserves_explicit_return_task_cancel_guard(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/application_lifecycle.py",
        "class ApplicationLifecycleMixin:\n"
        "    def cancel(self):\n"
        "        if not self._closed:\n"
        "            if self.interaction_service().cancel_active_model_call():\n"
        "                return\n"
        "            self.orchestrator.cancel_task()\n",
    )

    assert checker._check_s44(tmp_path) == []


def test_s44_rejects_task_cancel_in_active_interaction_branch(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/application_lifecycle.py",
        "class ApplicationLifecycleMixin:\n"
        "    def cancel(self):\n"
        "        if self.interaction_service().cancel_active_model_call():\n"
        "            self.orchestrator.cancel_task()\n",
    )

    violations = checker._check_s44(tmp_path)
    assert [item.rule_id for item in violations] == ["W12-S44"]


def test_checker_catches_resolver_authority_and_strict_parser_bypasses(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/interaction/resolver.py",
        "from agent.tools.invocation_gateway import ToolInvocationGateway\n"
        "def resolve():\n"
        "    return resolve_model_decision()\n",
    )
    _write(
        tmp_path,
        "agent/interaction/model_contract.py",
        "import json\n"
        "def parse(value):\n"
        "    return json.loads(value)\n",
    )
    rules = _rules(checker._check_s1(tmp_path) + checker._check_s3(tmp_path) + checker._check_s7(tmp_path) + checker._check_s23(tmp_path))
    assert {"W12-S1", "W12-S3", "W12-S7", "W12-S23"} <= rules


def test_checker_catches_admission_without_target_or_local_conflict_proof(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/interaction/admission.py",
        "def admit():\n"
        "    return TaskDirective.DO\n",
    )
    assert {"W12-S31", "W12-S39", "W12-S50", "W12-S52"} <= _rules(
        checker._check_s31(tmp_path)
        + checker._check_s39(tmp_path)
        + checker._check_s50(tmp_path)
        + checker._check_s52(tmp_path)
    )


def test_checker_catches_cli_mode_routing_and_checkpoint_drift(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/interfaces/cli/app.py",
        "def _handle_input(text, ctx):\n"
        "    if ctx.modo_agente:\n"
        "        return True\n"
        "def _run_once(args):\n"
        "    application = _create_application(args)\n"
        "    if request.action is TaskRequestAction.CONTINUE:\n"
        "        return 2\n",
    )
    _write(
        tmp_path,
        "agent/runtime/task_directives.py",
        "class X:\n"
        "    def to_checkpoint_dict(self):\n"
        "        return {'schema_version': 1, 'directive': 'read', 'subject': 'x', 'extra': 1}\n",
    )
    rules = _rules(checker._check_s9(tmp_path) + checker._check_s27(tmp_path) + checker._check_s8(tmp_path))
    assert {"W12-S9", "W12-S27", "W12-S8"} <= rules


def test_extracted_guard_checks_follow_implementation_owners(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/interaction/guard_operational.py",
        "OperationalClassification.HYPOTHETICAL\n"
        "_standalone_plain_conditional\nNEGATIVE_INFINITIVE_FORMS\nnormalize_clause_for_guard\n",
    )
    _write(
        tmp_path,
        "agent/interaction/guard_cross.py",
        '"TARGET_SCOPED"\n"FAMILY_ALL"\n'
        'def _global_restriction(value, core):\n    return value.startswith(core + " ")\n'
        "def _compare_claim_pair(restriction, positive):\n"
        '    if restriction.scope == "FAMILY_ALL":\n        return None\n'
        "    left = _target_for_relation(restriction)\n"
        "    right = _target_for_relation(positive)\n"
        "def _target_for_relation(value):\n"
        "    return normalize_target_anchor_identity(value)\n",
    )
    _write(tmp_path, "agent/interaction/guard_intent.py", "normalize_clause_for_guard\n")
    _write(
        tmp_path,
        "agent/interaction/guard_targets.py",
        "normalize_clause_for_guard\n_target_fragment\n__unproven__\n"
        "def normalize_target_anchor_identity(value):\n    return value\n",
    )
    _write(
        tmp_path,
        "agent/interaction/admission.py",
        "guard is not OperationalClassification.DIRECT\n",
    )

    assert checker._check_s29(tmp_path) == []
    assert checker._check_s34(tmp_path) == []
    assert checker._check_s35(tmp_path) == []
    assert checker._check_s36(tmp_path) == []
    assert checker._check_s38(tmp_path) == []
    assert checker._check_s40(tmp_path) == []
    assert checker._check_s45(tmp_path) == []
    assert checker._check_s48(tmp_path) == []
    assert checker._check_s51(tmp_path) == []

    _write(tmp_path, "agent/interaction/guard_operational.py", "_standalone_plain_conditional\nnormalize_clause_for_guard\n")
    _write(
        tmp_path,
        "agent/interaction/guard_cross.py",
        'def _global_restriction(value, core):\n    return value == core\n'
        "def _compare_claim_pair(restriction, positive):\n"
        "    left = _target_for_relation(restriction)\n"
        '    if restriction.scope == "FAMILY_ALL":\n        return None\n'
        "def _target_for_relation(value):\n"
        "    return value\n",
    )
    _write(tmp_path, "agent/interaction/guard_intent.py", "")
    _write(tmp_path, "agent/interaction/guard_targets.py", "normalize_clause_for_guard\n")

    failures = (
        checker._check_s29(tmp_path)
        + checker._check_s34(tmp_path)
        + checker._check_s35(tmp_path)
        + checker._check_s36(tmp_path)
        + checker._check_s38(tmp_path)
        + checker._check_s40(tmp_path)
        + checker._check_s45(tmp_path)
        + checker._check_s48(tmp_path)
        + checker._check_s51(tmp_path)
    )
    assert {"W12-S29", "W12-S34", "W12-S35", "W12-S36", "W12-S38", "W12-S40", "W12-S45", "W12-S48", "W12-S51"} <= _rules(failures)
