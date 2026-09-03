from __future__ import annotations

from pathlib import Path

from scripts import check_wave9_architecture as checker


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _rules(violations: list[checker.ArchitectureViolation]) -> set[str]:
    return {item.rule_id for item in violations}


def test_checker_rejects_neutral_ui_and_cli_leaks(tmp_path: Path) -> None:
    _write(tmp_path, "agent/presentation/bad.py", "from rich.console import Console\n")
    _write(tmp_path, "agent/observability/bad.py", "from agent.interfaces.cli import app\n")
    violations = checker.check_architecture(tmp_path)
    assert {"W9-S1", "W9-S2"} <= _rules(violations)


def test_checker_rejects_domain_mode_branch_and_direct_trace_store(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/orchestration/bad.py",
        "from agent.observability.modes import ObservabilityMode\n"
        "from agent.observability.trace_store import TraceStore\n"
        "def emit(mode, paths):\n"
        "    if mode is ObservabilityMode.TRACE:\n"
        "        TraceStore(paths, 'run')\n",
    )
    violations = checker.check_architecture(tmp_path)
    assert {"W9-S3", "W9-S4"} <= _rules(violations)


def test_checker_rejects_presentation_mutation_and_cli_type(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/presentation/bad.py",
        "def mutate(model):\n"
        "    model.run()\n"
        "class InspectorSnapshot:\n"
        "    table: Table\n",
    )
    violations = checker.check_architecture(tmp_path)
    assert {"W9-S5", "W9-S10"} <= _rules(violations)


def test_checker_rejects_diagnostic_state_and_new_semantic_kind(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/state/bad.py",
        "from agent.observability.diagnostics import DiagnosticRecord\n"
        "def save(state, record: DiagnosticRecord):\n"
        "    state.add_event(record)\n",
    )
    _write(
        tmp_path,
        "agent/presentation/bad.py",
        "from agent.runtime.event_kinds import RuntimeEventKind\n"
        "kind = RuntimeEventKind('made_up')\n",
    )
    violations = checker.check_architecture(tmp_path)
    assert "W9-S6" in _rules(violations)
    assert "W9-S7" in _rules(violations)


def test_checker_rejects_global_trace_path_and_credential_serializer(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/observability/bad.py",
        "from pathlib import Path\n"
        "def bad():\n"
        "    path = Path('trace.jsonl')\n"
        "    return {'password': 'value'}\n",
    )
    violations = checker.check_architecture(tmp_path)
    assert {"W9-S8", "W9-S9"} <= _rules(violations)


def test_checker_rejects_manual_path_confinement_in_observability_and_presentation(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent/observability/bad.py",
        "import os\n"
        "def bad(path):\n"
        "    current = path\n"
        "    while current != current.parent:\n"
        "        os.lstat(current)\n"
        "        current = current.parent\n"
        "    return os.path.abspath(path)\n",
    )
    _write(
        tmp_path,
        "agent/presentation/bad.py",
        "from pathlib import Path\n"
        "def bad(path):\n"
        "    selected = Path(path).resolve()\n"
        "    selected.relative_to(Path.cwd())\n"
        "    return selected.is_symlink()\n",
    )
    violations = checker.check_architecture(tmp_path)
    assert "W9-S11" in _rules(violations)
