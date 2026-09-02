from pathlib import Path

from scripts import check_wave8_architecture as checker


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _rules(violations: list[checker.ArchitectureViolation]) -> set[str]:
    return {item.rule_id for item in violations}


def test_current_repository_passes_wave8_source_checks(monkeypatch) -> None:
    monkeypatch.setattr(checker, "_check_prior_gates", lambda root: [])

    assert checker.check_architecture(Path(__file__).resolve().parents[3]) == []


def test_provider_bypass_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "agent/rogue.py", "def call(gateway, request):\n    return gateway.complete(request)\n")

    violations = checker._check_s1(tmp_path)

    assert "W8-S1" in _rules(violations)


def test_plan_validator_sibling_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "agent/rogue.py", "def admit(plan):\n    return PlanValidator(plan)\n")

    violations = checker._check_s2(tmp_path)

    assert "W8-S2" in _rules(violations)


def test_text_error_policy_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        "def decide(result):\n"
        "    return 'retry' in str(result.get('error')).lower()\n",
    )

    violations = checker._check_s3(tmp_path)

    assert "W8-S3" in _rules(violations)


def test_historical_attribute_text_classifier_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/diagnostics.py",
        "def classify(result):\n"
        "    text = ' '.join([\n"
        "        result.error or '',\n"
        "        result.summary,\n"
        "        *(item.get('message', '') for item in result.diagnostics),\n"
        "    ]).casefold()\n"
        "    if 'timeout' in text:\n"
        "        return 'retry'\n"
        "    return 'unknown'\n",
    )

    violations = checker._check_s3(tmp_path)

    assert "W8-S3" in _rules(violations)


def test_rendering_error_text_without_policy_is_allowed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rendering.py",
        "def render_error(result):\n"
        "    message = result.error or 'unknown'\n"
        "    logger.warning('%s', message)\n"
        "    return message\n",
    )

    assert checker._check_s3(tmp_path) == []


def test_alternate_event_surface_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        "def emit(event_emitter, event):\n"
        "    event_emitter('tool_end', event)\n",
    )

    violations = checker._check_s4(tmp_path)

    assert "W8-S4" in _rules(violations)


def test_unapproved_atomic_publication_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        "import os\n"
        "def save(path, value):\n"
        "    os.replace(value, path)\n",
    )

    violations = checker._check_s5(tmp_path)

    assert "W8-S5" in _rules(violations)


def test_unapproved_process_lifecycle_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        "import subprocess\n"
        "def run(command):\n"
        "    return subprocess.Popen(command)\n",
    )

    violations = checker._check_s6(tmp_path)

    assert "W8-S6" in _rules(violations)


def test_manual_confinement_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        "from pathlib import Path\n"
        "def resolve(root, value):\n"
        "    candidate = (Path(root) / value).resolve()\n"
        "    candidate.relative_to(Path(root))\n"
        "    return candidate\n",
    )

    violations = checker._check_s7(tmp_path)

    assert "W8-S7" in _rules(violations)


def test_raw_profile_selection_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "agent/rogue.py", "def model(profile):\n    return profile.get('model')\n")

    violations = checker._check_s8(tmp_path)

    assert "W8-S8" in _rules(violations)


def test_w7_retired_surface_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "agent/rogue.py", "class LegacyEventSinkAdapter: pass\n")

    violations = checker._check_s9(tmp_path)

    assert "W8-S9" in _rules(violations)


def test_removed_w8_fallback_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "agent/rogue.py", "def _call_extension(*args):\n    return args\n")

    violations = checker._check_s10(tmp_path)

    assert "W8-S10" in _rules(violations)


def test_productive_workspace_paths_fallback_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/orchestrator.py",
        "from pathlib import Path\n"
        "class WorkspacePaths: pass\n"
        "class Orchestrator:\n"
        "    def __init__(self, workspace_paths=None):\n"
        "        self.paths = workspace_paths or WorkspacePaths()\n",
    )

    violations = checker._check_s13(tmp_path)

    assert "W8-S13" in _rules(violations)


def test_productive_storage_cwd_fallback_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/interfaces/cli/workspace_entry.py",
        "from pathlib import Path\n"
        "def workspace_storage_path(ctx, attribute, filename):\n"
        "    root = Path.cwd()\n"
        "    return root / '.test_runtime' / filename\n",
    )

    violations = checker._check_s13(tmp_path)

    assert "W8-S13" in _rules(violations)


def test_explicit_cli_workspace_selection_may_use_cwd(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/interfaces/cli/workspace_entry.py",
        "from pathlib import Path\n"
        "def argument_workspace(args):\n"
        "    return Path.cwd()\n"
        "def choose_workspace():\n"
        "    return Path.cwd()\n",
    )

    assert checker._check_s13(tmp_path) == []


def test_report_reducer_is_rejected_outside_owner(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/rogue.py",
        "from agent.reporting.metrics import project_run_metrics\n"
        "def evaluate(entries):\n"
        "    return project_run_metrics(entries, 1)\n",
    )

    violations = checker._check_s11(tmp_path)

    assert "W8-S11" in _rules(violations)


def test_toolresult_mapping_access_is_rejected_in_core(tmp_path: Path) -> None:
    _write(tmp_path, "agent/rogue.py", "def inspect(result):\n    return result.get('status')\n")

    violations = checker._check_s12(tmp_path)

    assert "W8-S12" in _rules(violations)


def test_legitimate_provider_and_process_owners_are_allowed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/runtime/model_call.py",
        "class ModelCallService:\n"
        "    def complete(self, gateway, request):\n"
        "        return gateway.complete(request)\n",
    )
    _write(
        tmp_path,
        "agent/code/validation_process.py",
        "import subprocess\n"
        "def run(command):\n"
        "    return subprocess.Popen(command)\n",
    )

    assert checker._check_s1(tmp_path) == []
    assert checker._check_s6(tmp_path) == []


def test_legitimate_persistence_path_and_profile_boundaries_are_allowed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/memory/json_persistence.py",
        "import os\n"
        "def save(source, destination):\n"
        "    os.replace(source, destination)\n",
    )
    _write(
        tmp_path,
        "agent/runtime/path_safety.py",
        "from pathlib import Path\n"
        "def resolve_workspace_path(root, value):\n"
        "    base = Path(root).resolve()\n"
        "    return (base / value).resolve().relative_to(base)\n",
    )
    _write(
        tmp_path,
        "agent/code/path_safety.py",
        "from agent.runtime.path_safety import resolve_workspace_path\n",
    )
    _write(
        tmp_path,
        "agent/llm/model_profile.py",
        "def resolve_model_profile(values):\n"
        "    return values.get('model')\n",
    )
    _write(
        tmp_path,
        "agent/tools/result_adapter.py",
        "def from_legacy_result(result):\n"
        "    return result.get('status')\n",
    )

    assert checker._check_s5(tmp_path) == []
    assert checker._check_s7(tmp_path) == []
    assert checker._check_s8(tmp_path) == []
    assert checker._check_s12(tmp_path) == []


def test_compatibility_path_facade_reimplementation_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/code/path_safety.py",
        "from pathlib import Path\n"
        "def resolve_workspace_path(root, value):\n"
        "    candidate = (Path(root) / value).resolve()\n"
        "    candidate.relative_to(Path(root))\n"
        "    return candidate\n",
    )

    violations = checker._check_s7(tmp_path)

    assert "W8-S7" in _rules(violations)


def test_compatibility_path_facade_reexports_canonical_owner(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/code/path_safety.py",
        "from agent.runtime.path_safety import (\n"
        "    resolve_workspace_path,\n"
        "    workspace_relative_path,\n"
        ")\n",
    )

    assert checker._check_s7(tmp_path) == []


def test_historical_mentions_are_not_source_compatibility_findings(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/notes.py",
        '"""Historical LegacyEventSinkAdapter and ModelClient names only."""\n'
        "def explain():\n"
        "    return 'historical format'\n",
    )

    assert checker._check_s9(tmp_path) == []
