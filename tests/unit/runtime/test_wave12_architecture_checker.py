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
