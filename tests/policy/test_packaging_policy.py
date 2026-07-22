from pathlib import Path


def test_pyproject_owns_dependencies_and_console_entry_point() -> None:
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")

    assert "[project]" in content
    assert "dependencies = [" in content
    assert "[project.optional-dependencies]" in content
    assert 'llm-agent = "agent.interfaces.cli.app:main"' in content


def test_compatibility_requirements_delegate_to_pyproject() -> None:
    root = Path(__file__).parents[2]

    assert (root / "requirements-core.txt").read_text(encoding="utf-8").splitlines()[-1] == "."
    assert (root / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()[-1] == ".[dev]"
    assert (root / "requirements-ml.txt").read_text(encoding="utf-8").splitlines()[-1] == ".[ml]"
    assert (root / "requirements.txt").read_text(encoding="utf-8").splitlines()[-1] == ".[dev,ml]"
