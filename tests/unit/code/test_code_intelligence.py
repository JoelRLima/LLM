from pathlib import Path

import pytest

from agent.code import CodeIntelligenceService, ProjectDiscovery
from agent.code.contracts import AnalysisLevel, DiagnosticSeverity


def _symlink_or_skip(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symlinks indisponíveis neste ambiente: {exc}")


def test_project_discovery_detects_languages_manifests_and_ignores_dependencies(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def run(): pass\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_run(): pass\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("x", encoding="utf-8")

    profile = ProjectDiscovery(tmp_path).discover()

    assert profile.languages == {"python": 2}
    assert profile.manifests == ("pyproject.toml",)
    assert profile.test_roots == ("tests",)


def test_python_adapter_indexes_symbols_imports_and_security_diagnostics(tmp_path: Path):
    source = (
        "import os\n\n"
        "class Service:\n"
        "    def load(self, expression):\n"
        "        return eval(expression)\n"
    )
    (tmp_path / "service.py").write_text(source, encoding="utf-8")
    service = CodeIntelligenceService(tmp_path)

    analysis = service.analyze_file("service.py")

    assert analysis.level == AnalysisLevel.SEMANTIC
    assert {symbol.qualified_name for symbol in analysis.symbols} == {"Service", "Service.load"}
    assert analysis.imports[0].target == "os"
    assert analysis.diagnostics[0].severity == DiagnosticSeverity.SECURITY
    assert analysis.diagnostics[0].line == 5


def test_invalid_python_is_reported_without_breaking_repository_index(tmp_path: Path):
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "valid.py").write_text("def valid():\n    return 1\n", encoding="utf-8")
    service = CodeIntelligenceService(tmp_path)

    index = service.index_repository()

    assert len(index.analyses) == 2
    assert index.find_symbols("valid")[0].file_path == "valid.py"
    assert any(diagnostic.code == "PYSYNTAX" for diagnostic in index.diagnostics)


def test_unknown_language_uses_honest_textual_fallback_and_cache(tmp_path: Path):
    (tmp_path / "query.sql").write_text("select 1;", encoding="utf-8")
    service = CodeIntelligenceService(tmp_path)

    first = service.analyze_file("query.sql")
    second = service.analyze_file("query.sql")

    assert first is second
    assert first.level == AnalysisLevel.TEXTUAL
    assert first.confidence < 0.5


def test_discovery_and_analysis_never_follow_external_symlinks(
    tmp_path: Path,
) -> None:
    outside_file = tmp_path.parent / f"{tmp_path.name}-sentinel.py"
    outside_manifest = tmp_path.parent / f"{tmp_path.name}-requirements.txt"
    outside_tests = tmp_path.parent / f"{tmp_path.name}-external-tests"
    outside_file.write_text("SENTINEL = 'outside'\n", encoding="utf-8")
    outside_manifest.write_text("sentinel-package\n", encoding="utf-8")
    outside_tests.mkdir()
    (outside_tests / "test_external.py").write_text(
        "SENTINEL_TEST = True\n",
        encoding="utf-8",
    )
    _symlink_or_skip(tmp_path / "leak.py", outside_file)
    _symlink_or_skip(tmp_path / "requirements.txt", outside_manifest)
    _symlink_or_skip(
        tmp_path / "tests",
        outside_tests,
        target_is_directory=True,
    )

    discovery = ProjectDiscovery(tmp_path)
    profile = discovery.discover()
    index = CodeIntelligenceService(tmp_path).index_repository()

    assert tuple(discovery.iter_files()) == ()
    assert profile.languages == {}
    assert profile.manifests == ()
    assert profile.test_roots == ()
    assert index.analyses == ()
    with pytest.raises(ValueError, match="fora da raiz"):
        CodeIntelligenceService(tmp_path).analyze_file("leak.py")
