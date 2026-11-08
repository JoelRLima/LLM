from pathlib import Path


def test_pyproject_owns_dependencies_and_console_entry_point() -> None:
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")

    assert "[project]" in content
    assert "dependencies = [" in content
    assert "[project.optional-dependencies]" in content
    assert 'llm-agent = "llm_agent.interfaces.cli.app:main"' in content


def test_dependency_declarations_have_one_source_and_two_environment_locks() -> None:
    root = Path(__file__).parents[2]

    requirement_files = {path.name for path in root.glob("requirements*")}
    assert requirement_files == {"requirements-ci.lock", "requirements.lock"}

    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert 'python -m pip install --constraint requirements-ci.lock ".[dev]"' in workflow


def test_ci_and_full_locks_pin_prompt_toolkit_runtime_tree_consistently() -> None:
    root = Path(__file__).parents[2]
    package_names = {"prompt-toolkit", "wcwidth"}
    observed: list[dict[str, str]] = []
    for lock_name in ("requirements-ci.lock", "requirements.lock"):
        pins = {
            line.split("==", 1)[0].lower().replace("_", "-"): line.split("==", 1)[1]
            for line in (root / lock_name).read_text(encoding="utf-8").splitlines()
            if "==" in line
        }
        observed.append(pins)

    ci_pins, full_pins = observed
    assert package_names <= ci_pins.keys()
    assert package_names <= full_pins.keys()
    assert all(ci_pins[name] for name in package_names)
    assert all(full_pins[name] for name in package_names)
    assert {name: ci_pins[name] for name in package_names} == {
        name: full_pins[name] for name in package_names
    }
