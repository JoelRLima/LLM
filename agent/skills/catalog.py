"""Catálogo único das skills embutidas.

Adicionar uma skill interna requer um `SkillSpec` aqui e a implementação da
classe. Construção, custo, capacidade e timeout não vivem mais em mapas
independentes.
"""

from __future__ import annotations

from agent.skills.descriptor import SkillCapability as C
from agent.skills.descriptor import SkillSpec

BUILTIN_SKILL_SPECS: tuple[SkillSpec, ...] = (
    SkillSpec(
        "agent.skills.calculator",
        "CalculatorSkill",
        "calculator",
        capabilities=frozenset({C.ANALYZE}),
        cost=1,
        cacheable=True,
        idempotent=True,
        category="EXECUTE",
    ),
    SkillSpec(
        "agent.skills.code_analyzer",
        "CodeAnalyzerSkill",
        "code_analyzer",
        kwargs={"base_dir": "."},
        capabilities=frozenset({C.READ, C.ANALYZE}),
        cost=2,
        cacheable=True,
        idempotent=True,
        category="ANALYZE",
    ),
    SkillSpec(
        "agent.skills.code_task",
        "CodeTaskSkill",
        "code_task",
        kwargs={"base_dir": ".", "model_gateway": None, "config": {}},
        capabilities=frozenset({C.READ, C.WRITE, C.PROCESS, C.ANALYZE}),
        cost=8,
        timeout_seconds=120,
        category="WRITE",
    ),
    SkillSpec(
        "agent.skills.directory_reader",
        "DirectoryListerSkill",
        "directory_lister",
        kwargs={"base_dir": "."},
        capabilities=frozenset({C.READ}),
        cost=1,
        cacheable=True,
        idempotent=True,
        category="SEARCH",
    ),
    SkillSpec(
        "agent.skills.echo",
        "EchoSkill",
        "echo",
        capabilities=frozenset(),
        cost=1,
        cacheable=True,
        idempotent=True,
        category="EXECUTE",
    ),
    SkillSpec(
        "agent.skills.file_reader",
        "FileReaderSkill",
        "file_reader",
        kwargs={"base_dir": "."},
        capabilities=frozenset({C.READ}),
        cost=4,
        cacheable=True,
        idempotent=True,
        category="READ",
    ),
    SkillSpec(
        "agent.skills.file_writer",
        "FileWriterSkill",
        "file_writer",
        kwargs={"base_dir": "."},
        capabilities=frozenset({C.READ, C.WRITE}),
        cost=8,
        category="WRITE",
    ),
    SkillSpec(
        "agent.skills.git",
        "GitSkill",
        "git_reader",
        capabilities=frozenset({C.READ, C.VCS_READ, C.PROCESS}),
        cost=5,
        idempotent=True,
        timeout_seconds=20,
        category="EXECUTE",
    ),
    SkillSpec(
        "agent.skills.grep",
        "GrepSkill",
        "grep",
        kwargs={"base_dir": "."},
        capabilities=frozenset({C.READ}),
        cost=1,
        cacheable=True,
        idempotent=True,
        category="SEARCH",
    ),
    SkillSpec(
        "agent.skills.python_executor",
        "PythonExecutorSkill",
        "python_executor",
        kwargs={"timeout_seconds": 10},
        capabilities=frozenset({C.PROCESS}),
        cost=6,
        timeout_seconds=10,
        category="EXECUTE",
    ),
    SkillSpec(
        "agent.skills.session_memory",
        "SessionMemorySkill",
        "session_memory",
        kwargs={"orchestrator": None},
        capabilities=frozenset({C.MEMORY}),
        cost=2,
        category="MEMORY",
    ),
    SkillSpec(
        "agent.skills.shell",
        "ShellSkill",
        "shell",
        kwargs={"base_dir": ".", "timeout": 30},
        capabilities=frozenset({C.READ, C.WRITE, C.PROCESS, C.VCS_READ}),
        cost=7,
        timeout_seconds=30,
        category="EXECUTE",
    ),
    SkillSpec(
        "agent.skills.summarize",
        "SummarizeSkill",
        "summarize",
        kwargs={"orchestrator": None},
        capabilities=frozenset({C.ANALYZE}),
        cost=5,
        category="ANALYZE",
    ),
    SkillSpec(
        "agent.skills.web_search",
        "WebSearchSkill",
        "web_search",
        capabilities=frozenset({C.NETWORK}),
        cost=5,
        cacheable=True,
        category="NETWORK",
    ),
)


BUILTIN_SPEC_BY_NAME = {spec.name: spec for spec in BUILTIN_SKILL_SPECS}
