from pathlib import Path
from types import SimpleNamespace

from agent.capabilities import Capability
from agent.code.contracts import ProjectProfile
from agent.code.discovery import ProjectDiscovery
from agent.code.validation import (
    CommandResult,
    ProjectValidator,
    ValidationImpactPlanner,
    ValidationScope,
    ValidationStatus,
)
from agent.code.validation_impact import TestCoverage as Coverage
from agent.tools.invocation_semantics import resolve_invocation_components


def _profile(root: Path) -> ProjectProfile:
    return ProjectDiscovery(root).discover()


def test_p3_mapping_uses_convention_and_imports_but_excludes_unrelated_tests(
    tmp_path: Path,
) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg" / "foo.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "bar.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / "tests" / "test_foo.py").write_text(
        "def test_foo():\n    assert True\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_import.py").write_text(
        "from pkg import bar\n\ndef test_bar():\n    assert bar.VALUE == 2\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_unrelated.py").write_text(
        "def test_unrelated():\n    assert True\n",
        encoding="utf-8",
    )

    plan = ValidationImpactPlanner(tmp_path).plan(
        _profile(tmp_path),
        ("pkg/foo.py", "pkg/bar.py"),
        include_tests=True,
    )

    selection = plan.pytest_selection
    assert selection is not None
    assert selection.scope is ValidationScope.TARGETED_TESTS
    assert selection.targets == ("tests/test_foo.py", "tests/test_import.py")
    assert "test_unrelated.py" not in selection.targets
    assert plan.test_coverage is Coverage.TARGETED_COMPLETE


def test_p3_changed_test_file_validates_itself(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    changed = tmp_path / "tests" / "test_self.py"
    changed.write_text("def test_self():\n    assert True\n", encoding="utf-8")

    plan = ValidationImpactPlanner(tmp_path).plan(
        _profile(tmp_path),
        ("tests/test_self.py",),
        include_tests=True,
    )

    assert plan.test_targets == ("tests/test_self.py",)


def test_p3_targeted_cap_expands_only_to_one_bounded_subsystem(tmp_path: Path) -> None:
    subsystem = tmp_path / "tests" / "unit" / "code"
    source = tmp_path / "agent" / "code"
    subsystem.mkdir(parents=True)
    source.mkdir(parents=True)
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    for index in range(21):
        (subsystem / f"test_module_{index}.py").write_text(
            "from agent.code import module\n"
            f"def test_{index}():\n    assert module.VALUE == 1\n",
            encoding="utf-8",
        )

    plan = ValidationImpactPlanner(tmp_path).plan(
        _profile(tmp_path),
        ("agent/code/module.py",),
        include_tests=True,
    )

    selection = plan.pytest_selection
    assert selection is not None
    assert selection.scope is ValidationScope.SUBSYSTEM
    assert len(selection.targets) == 21
    assert selection.expanded_because == (
        "one deterministic subsystem boundary was derived: tests/unit/code"
    )
    assert plan.test_coverage is Coverage.SUBSYSTEM_COMPLETE


def test_p3_over_cap_without_bounded_subsystem_is_unavailable(tmp_path: Path) -> None:
    subsystem = tmp_path / "tests" / "unit" / "code"
    source = tmp_path / "agent" / "code"
    subsystem.mkdir(parents=True)
    source.mkdir(parents=True)
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    for index in range(41):
        (subsystem / f"test_module_{index}.py").write_text(
            "from agent.code import module\n"
            f"def test_{index}():\n    assert module.VALUE == 1\n",
            encoding="utf-8",
        )

    plan = ValidationImpactPlanner(tmp_path).plan(
        _profile(tmp_path),
        ("agent/code/module.py",),
        include_tests=True,
    )

    selection = plan.pytest_selection
    assert selection is not None
    assert selection.targets == ()
    assert plan.test_coverage is Coverage.UNAVAILABLE


def test_p3_requested_tests_without_mapping_become_effectively_unavailable(
    tmp_path: Path,
) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg" / "unrelated.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_other.py").write_text(
        "def test_other():\n    assert True\n",
        encoding="utf-8",
    )

    validator = ProjectValidator(tmp_path)
    validator.runner.run = lambda command: CommandResult(
        command.name,
        ValidationStatus.PASSED,
        0,
        "",
        "",
        0.0,
    )
    report = validator.validate(
        _profile(tmp_path),
        ("pkg/unrelated.py",),
        include_tests=True,
        model_actionable=True,
    )

    assert report.status is ValidationStatus.PASSED
    assert report.effective_status is ValidationStatus.UNAVAILABLE
    assert report.test_coverage is Coverage.UNAVAILABLE
    assert [item.name for item in report.checks] == ["python-syntax"]


def test_p3_configured_pytest_does_not_spawn_for_model_task_without_request(
    tmp_path: Path,
) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_module.py").write_text(
        "def test_module():\n    assert True\n",
        encoding="utf-8",
    )
    commands: list[str] = []
    validator = ProjectValidator(
        tmp_path,
        validation_config={"enabled": True, "pytest": True},
    )

    def run(command):
        commands.append(command.name)
        return CommandResult(command.name, ValidationStatus.PASSED, 0, "", "", 0.0)

    validator.runner.run = run
    report = validator.validate(
        _profile(tmp_path),
        ("pkg/module.py",),
        include_tests=False,
        model_actionable=True,
    )

    assert "pytest" not in commands
    assert report.test_coverage is Coverage.NOT_REQUESTED
    assert report.effective_status is ValidationStatus.PASSED


def test_p3_include_tests_is_process_authority_and_never_selects_full(
    tmp_path: Path,
) -> None:
    descriptor = SimpleNamespace(name="code_task", capabilities=())
    without_tests = resolve_invocation_components(
        descriptor,
        {"action": "generate", "include_tests": False},
    )
    with_tests = resolve_invocation_components(
        descriptor,
        {"action": "generate", "include_tests": True},
    )

    assert set(without_tests[2]) == {
        Capability.READ.value,
        Capability.WRITE.value,
        Capability.VALIDATE.value,
    }
    assert "process" not in without_tests[2]
    assert "process" in with_tests[2]
    assert with_tests[2] - without_tests[2] == {Capability.PROCESS.value}

    plan = ValidationImpactPlanner(tmp_path).plan(
        ProjectProfile(
            str(tmp_path),
            None,
            {"python": 1},
            (),
            ("src",),
            ("tests",),
            0,
        ),
        ("src/module.py",),
        include_tests=True,
    )
    assert plan.full_scope_authorized is False
    assert plan.pytest_selection is not None
    assert plan.pytest_selection.scope is not ValidationScope.FULL
