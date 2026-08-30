"""Catálogo único das skills embutidas.

Adicionar uma skill interna requer um `SkillSpec` aqui e a implementação da
classe. Construção, custo, capacidade e timeout não vivem mais em mapas
independentes.
"""

from __future__ import annotations

from agent.skills.descriptor import SkillCapability as C
from agent.skills.descriptor import SkillSpec
from agent.tools.contracts import CancellationSafetyMode as S
from agent.tools.provenance import ArgumentOrigin

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
        usage_examples=(
            {"args": {"expression": "2 + 2"}, "purpose": "Evaluate a bounded arithmetic expression."},
        ),
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
        usage_examples=(
            {
                "args": {"target": "src/example.py", "mode": "file", "compact": True},
                "purpose": "Inspect the structure of one source file.",
            },
        ),
    ),
    SkillSpec(
        "agent.skills.code_task",
        "CodeTaskSkill",
        "code_task",
        kwargs={
            "base_dir": ".",
            "model_gateway": None,
            "config": {},
            "approval_policy": None,
        },
        capabilities=frozenset({C.READ, C.WRITE, C.VALIDATE, C.ANALYZE}),
        cost=8,
        category="WRITE",
        usage_examples=(
            {
                "args": {
                    "action": "modify",
                    "targets": ["src/example.py"],
                    "objective": "Apply the requested bounded change.",
                },
                "purpose": "Route a bounded code change through the code task contract.",
            },
        ),
        # Proposal generation calls a synchronous model gateway that cannot
        # be cancelled or process-killed. Mutating actions therefore fail
        # closed whenever timeout/cancellation is requested.
        cancellation_safety=S.UNSUPPORTED,
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
        public_invocation_fields=frozenset({"path"}),
        usage_examples=(
            {"args": {"path": "src"}, "purpose": "List the entries under a project directory."},
        ),
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
        usage_examples=(
            {"args": {"message": "hello"}, "purpose": "Return a small diagnostic message."},
        ),
    ),
    SkillSpec(
        "agent.skills.file_reader",
        "FileReaderSkill",
        "file_reader",
        kwargs={"base_dir": ".", "scratch_dir": None},
        capabilities=frozenset({C.READ}),
        cost=4,
        cacheable=True,
        idempotent=True,
        category="READ",
        public_invocation_fields=frozenset({"file_path"}),
        result_data_schema={"type": "string"},
        usage_examples=(
            {
                "args": {"file_path": "src/example.py", "start_line": 1, "end_line": 40},
                "purpose": "Read a bounded source excerpt for grounded analysis.",
            },
        ),
    ),
    SkillSpec(
        "agent.skills.file_writer",
        "FileWriterSkill",
        "file_writer",
        kwargs={
            "base_dir": ".",
            "scratch_dir": None,
            "config": {},
            "approval_policy": None,
            "orchestrator": None,
        },
        capabilities=frozenset({C.READ, C.WRITE}),
        cost=8,
        category="WRITE",
        # In-process filesystem writes have no bounded cancellation fence.
        cancellation_safety=S.UNSUPPORTED,
        usage_examples=(
            {
                "args": {
                    "action": "patch",
                    "file_path": "src/example.py",
                    "old_content": "old",
                    "new_content": "new",
                },
                "purpose": "Apply an exact patch after the normal approval and gateway checks.",
            },
        ),
    ),
    SkillSpec(
        "agent.skills.git",
        "GitSkill",
        "git_reader",
        kwargs={"base_dir": ".", "timeout": 20},
        capabilities=frozenset({C.READ, C.VCS_READ, C.PROCESS}),
        cost=5,
        idempotent=True,
        timeout_seconds=20,
        category="EXECUTE",
        # Commands run in the existing owned process-tree boundary.
        cancellation_safety=S.PROCESS_KILLABLE,
        usage_examples=(
            {"args": {"command": "log", "args": "-n 5"}, "purpose": "Read recent local Git history metadata."},
        ),
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
        public_invocation_fields=frozenset({"path", "pattern"}),
        argument_provenance={
            "pattern": frozenset(
                {
                    ArgumentOrigin.USER_LITERAL.value,
                    ArgumentOrigin.OBSERVATION_LITERAL.value,
                    ArgumentOrigin.RESULT_BINDING.value,
                }
            )
        },
        result_data_schema={
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "content": {"type": "string"},
                },
            },
        },
        usage_examples=(
            {
                "args": {"path": ".", "pattern": "TODO", "recursive": True, "max_results": 20},
                "purpose": "Find a literal project marker in bounded workspace files.",
            },
        ),
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
        # Python execution uses the existing owned process-tree boundary.
        cancellation_safety=S.PROCESS_KILLABLE,
        usage_examples=(
            {"args": {"code": "print(2 + 2)"}, "purpose": "Run a small sandboxed calculation."},
        ),
    ),
    SkillSpec(
        "agent.skills.session_memory",
        "SessionMemorySkill",
        "session_memory",
        kwargs={"orchestrator": None},
        capabilities=frozenset({C.MEMORY}),
        cost=2,
        category="MEMORY",
        # SQLite lock waits and statements poll invocation cancellation,
        # roll back before commit, and terminate within gateway grace.
        cancellation_safety=S.BOUNDED_COOPERATIVE,
        usage_examples=(
            {
                "args": {"action": "set", "key": "project_goal", "value": "Review the parser."},
                "purpose": "Store a short session note through the memory contract.",
            },
        ),
    ),
    SkillSpec(
        "agent.skills.shell",
        "ShellSkill",
        "shell",
        kwargs={"base_dir": ".", "timeout": 30, "approval_policy": None},
        capabilities=frozenset({C.READ, C.PROCESS, C.VCS_READ}),
        cost=7,
        timeout_seconds=30,
        category="EXECUTE",
        # The read-only shell surface still owns and kills its process tree.
        cancellation_safety=S.PROCESS_KILLABLE,
        usage_examples=(
            {"args": {"command": "git log -n 5"}, "purpose": "Run an allowlisted read-only history command."},
        ),
    ),
    SkillSpec(
        "agent.skills.summarize",
        "SummarizeSkill",
        "summarize",
        kwargs={"orchestrator": None},
        capabilities=frozenset({C.ANALYZE}),
        cost=5,
        category="ANALYZE",
        usage_examples=(
            {
                "args": {"text": "A short technical note.", "context": "Python code"},
                "purpose": "Summarize bounded text while preserving technical context.",
            },
        ),
    ),
    SkillSpec(
        "agent.skills.web_search",
        "WebSearchSkill",
        "web_search",
        capabilities=frozenset({C.NETWORK}),
        cost=5,
        cacheable=True,
        category="NETWORK",
        public_invocation_fields=frozenset({"query"}),
        usage_examples=(
            {
                "args": {"query": "latest Python release"},
                "purpose": "Search for current information when network access is authorized.",
            },
        ),
    ),
)


BUILTIN_SPEC_BY_NAME = {spec.name: spec for spec in BUILTIN_SKILL_SPECS}
