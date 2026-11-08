"""Frozen live demo acceptance scenarios and their version identity."""

from __future__ import annotations

from llm_agent.agent.evaluation.contracts import CapabilityScenario, FileExpectation, ScenarioExpectation

DEMO_ACCEPTANCE_VERSION = "DEMO-ACCEPTANCE-V1"

DEMO_SCENARIOS: tuple[CapabilityScenario, ...] = (
    CapabilityScenario(
        "DEMO-CALC", "calculation",
        "Calcule 1234 * 5678 com a ferramenta calculator e responda com o resultado.",
        expectation=ScenarioExpectation(answer_contains=("7006652",), max_steps=8),
        metadata={"task_authority_capabilities": ("analyze",), "oracle": {
            "expected_answer": 7006652, "required_tools": ("calculator",),
        }},
    ),
    CapabilityScenario(
        "DEMO-READ", "file-read",
        "Leia `facts.txt` e informe o valor de `MODE`.",
        {"facts.txt": 'MODE = "amber"\n'},
        ScenarioExpectation(
            unchanged_files=("facts.txt",), answer_contains=("amber",), max_steps=8,
        ),
        {"task_authority_capabilities": ("read", "vcs_read", "analyze"),
         "oracle": {"expected_answer": "amber", "required_tools": ("file_reader",)}},
    ),
    CapabilityScenario(
        "DEMO-SEARCH", "file-search",
        "Procure `ZX-419` nos arquivos de `records/` e informe o caminho do arquivo e a linha correspondente.",
        {
            "records/alpha.txt": "status=ready\nowner=team-a\n",
            "records/beta.txt": "status=pending\ncase=ZX-419\n",
            "records/gamma.txt": "status=closed\ncase=ZX-418\n",
        },
        ScenarioExpectation(
            unchanged_files=("records/alpha.txt", "records/beta.txt", "records/gamma.txt"),
            answer_contains=("records/beta.txt", "ZX-419"), max_steps=8,
        ),
        {"task_authority_capabilities": ("read", "vcs_read", "analyze"),
         "oracle": {"expected_path": "records/beta.txt", "expected_value": "ZX-419",
                    "required_tools": ("grep",)}},
    ),
    CapabilityScenario(
        "DEMO-WRITE", "file-write",
        "Crie `result.txt` com exatamente esta linha: ready for demonstration",
        expectation=ScenarioExpectation(
            files=(FileExpectation("result.txt", exact_content="ready for demonstration\n"),),
            allowed_changed_files=("result.txt",), max_steps=10,
        ),
        metadata={"oracle": {
            "expected_file": "result.txt", "expected_content": "ready for demonstration\n",
            "required_tools": ("code_task",),
        }},
    ),
    CapabilityScenario(
        "DEMO-COMPOSED", "read-calculate-write",
        "Leia `source.json`, some os números de `values` usando a ferramenta calculator e grave somente o total em `total.txt`.",
        {"source.json": '{"values":[19,23,41]}\n'},
        ScenarioExpectation(
            files=(FileExpectation("total.txt", exact_content="83\n"),),
            unchanged_files=("source.json",), allowed_changed_files=("total.txt",),
            max_steps=14,
        ),
        {"oracle": {
            "source_file": "source.json", "values": (19, 23, 41), "expected_total": 83,
            "expected_file": "total.txt", "expected_content": "83\n",
            "required_tools": ("file_reader", "calculator", "code_task"),
        }},
    ),
)

COMPACT_ELIGIBLE_SCENARIO_IDS = frozenset({"DEMO-READ", "DEMO-SEARCH"})
