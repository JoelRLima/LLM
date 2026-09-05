from __future__ import annotations

import shutil
from pathlib import Path

from scripts import check_wave13_architecture as checker

ROOT = Path(__file__).parents[3]


def _copy(root: Path, destination: Path, *relatives: str) -> None:
    for relative in relatives:
        source = root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _read(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _write(root: Path, relative: str, source: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


def _rules(findings: list[checker.ArchitectureViolation]) -> set[str]:
    return {finding.rule_id for finding in findings}


def test_real_repository_passes_wave13_checker() -> None:
    assert checker.check_architecture(ROOT) == []


def test_s1_rejects_w13_roadmap_name_in_production_owner(tmp_path: Path) -> None:
    relative = "agent/code/validation.py"
    _copy(ROOT, tmp_path, relative)
    _write(tmp_path, relative, _read(tmp_path, relative) + "\ndef w13_owner() -> None:\n    pass\n")
    assert "W13-S1" in _rules(checker._check_s1(tmp_path))


def test_s2_rejects_trusted_system_field_on_projection(tmp_path: Path) -> None:
    relative = "agent/llm/context_projection.py"
    _copy(ROOT, tmp_path, relative)
    source = _read(tmp_path, relative).replace(
        "class ModelContextProjection:\n",
        "class ModelContextProjection:\n    trusted_system_addition: str | None = None\n",
        1,
    )
    _write(tmp_path, relative, source)
    assert "W13-S2" in _rules(checker._check_s2(tmp_path))


def test_s3_rejects_auxiliary_projector_subprocess_import(tmp_path: Path) -> None:
    relative = "agent/llm/context_projection.py"
    _copy(ROOT, tmp_path, relative)
    _write(tmp_path, relative, "import subprocess\n" + _read(tmp_path, relative))
    assert "W13-S3" in _rules(checker._check_s3(tmp_path))


def test_s4_rejects_raw_context_delimiter_in_proposal_builder(tmp_path: Path) -> None:
    relatives = ("agent/code/workflow_proposal.py", "agent/code/outcome_verifier.py")
    _copy(ROOT, tmp_path, *relatives)
    relative = relatives[0]
    source = _read(tmp_path, relative).replace(
        "    system = (\n",
        '    raw_context = f"<untrusted_workspace_context>{prompt}</untrusted_workspace_context>"\n'
        "    system = (\n",
        1,
    )
    _write(tmp_path, relative, source)
    assert "W13-S4" in _rules(checker._check_s4(tmp_path))


def test_s5_rejects_verifier_approval_import(tmp_path: Path) -> None:
    relative = "agent/code/outcome_verifier.py"
    _copy(ROOT, tmp_path, relative)
    _write(tmp_path, relative, "from agent.approval import ApprovalPort\n" + _read(tmp_path, relative))
    assert "W13-S5" in _rules(checker._check_s5(tmp_path))


def test_s6_rejects_model_facing_full_validation_enum(tmp_path: Path) -> None:
    relative = "agent/skills/code_task.py"
    _copy(ROOT, tmp_path, relative)
    source = _read(tmp_path, relative).replace(
        '                "include_tests": {\n',
        '                "validation_scope": {"type": "string", "enum": ["full"]},\n'
        '                "include_tests": {\n',
        1,
    )
    _write(tmp_path, relative, source)
    assert "W13-S6" in _rules(checker._check_s6(tmp_path))


def test_s7_rejects_repository_state_model_argument(tmp_path: Path) -> None:
    relatives = ("agent/skills/catalog.py", "agent/skills/repository_state.py")
    _copy(ROOT, tmp_path, *relatives)
    relative = relatives[1]
    source = _read(tmp_path, relative).replace(
        '            "properties": {},\n',
        '            "properties": {"command": {"type": "string"}},\n',
        1,
    )
    _write(tmp_path, relative, source)
    assert "W13-S7" in _rules(checker._check_s7(tmp_path))


def test_s8_rejects_git_status_from_model_allowlist(tmp_path: Path) -> None:
    relatives = ("agent/skills/process_safety.py", "agent/skills/git.py", "agent/skills/shell.py")
    _copy(ROOT, tmp_path, *relatives)
    relative = relatives[0]
    source = _read(tmp_path, relative).replace('    "git log",\n', '    "git status",\n', 1)
    _write(tmp_path, relative, source)
    assert "W13-S8" in _rules(checker._check_s8(tmp_path))


def test_s9_rejects_raw_diff_snapshot_field(tmp_path: Path) -> None:
    relative = "agent/skills/repository_state.py"
    _copy(ROOT, tmp_path, relative)
    source = _read(tmp_path, relative).replace(
        "class RepositoryStateSnapshot:\n",
        "class RepositoryStateSnapshot:\n    diff: str | None\n",
        1,
    )
    _write(tmp_path, relative, source)
    assert "W13-S9" in _rules(checker._check_s9(tmp_path))


def test_s10_rejects_dynamic_repository_status_argv(tmp_path: Path) -> None:
    relative = "agent/skills/repository_state.py"
    _copy(ROOT, tmp_path, relative)
    source = _read(tmp_path, relative).replace(
        "    return [\n        \"git\",\n",
        "    return [*user_args,\n        \"git\",\n",
        1,
    )
    _write(tmp_path, relative, source)
    assert "W13-S10" in _rules(checker._check_s10(tmp_path))


def test_s11_rejects_task_runtime_import_from_online_health(tmp_path: Path) -> None:
    relative = "agent/health/online_model.py"
    _copy(ROOT, tmp_path, relative)
    _write(tmp_path, relative, "from agent.application import AgentApplication\n" + _read(tmp_path, relative))
    assert "W13-S11" in _rules(checker._check_s11(tmp_path))


def test_s12_rejects_unconditional_plain_doctor_online_probe(tmp_path: Path) -> None:
    relatives = (
        "agent/health/standalone.py",
        "agent/health_check.py",
        "agent/interfaces/cli/maintenance.py",
    )
    _copy(ROOT, tmp_path, *relatives)
    relative = relatives[0]
    source = _read(tmp_path, relative).replace("    if online:\n", "    if True:\n", 1)
    _write(tmp_path, relative, source)
    assert "W13-S12" in _rules(checker._check_s12(tmp_path))


def test_s13_rejects_pv1_fixture_id_in_planning_code(tmp_path: Path) -> None:
    relative = "agent/planning/task_semantics_effects.py"
    _copy(ROOT, tmp_path, relative)
    _write(tmp_path, relative, _read(tmp_path, relative) + '\nPV1_BAD = "PV1-01"\n')
    assert "W13-S13" in _rules(checker._check_s13(tmp_path))


def test_s14_rejects_local_candidate_fingerprint_owner(tmp_path: Path) -> None:
    relatives = (
        "agent/evaluation/evaluation_identity.py",
        "agent/evaluation/practical.py",
        "agent/evaluation/practical_gateway_logic.py",
        "agent/evaluation/practical_scenarios.py",
        "agent/evaluation/scripted_gateway.py",
        "agent/evaluation/runner.py",
    )
    _copy(ROOT, tmp_path, *relatives)
    relative = relatives[1]
    _write(
        tmp_path,
        relative,
        _read(tmp_path, relative) + '\n\ndef candidate_fingerprint(root: Path) -> str:\n    return "local"\n',
    )
    assert "W13-S14" in _rules(checker._check_s14(tmp_path))


def test_s15_rejects_w13_full_pytest_phase_requirement(tmp_path: Path) -> None:
    relative = "agent/code/validation_impact.py"
    _copy(ROOT, tmp_path, relative)
    _write(tmp_path, relative, _read(tmp_path, relative) + '\nW13_P6_REQUIREMENT = "full pytest"\n')
    assert "W13-S15" in _rules(checker._check_s15(tmp_path))
