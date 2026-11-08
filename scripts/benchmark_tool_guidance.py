"""Offline measurement of tool-disclosure rendering, with no model or network."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from statistics import median
from time import perf_counter
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from llm_agent.agent.planning import tool_disclosure as disclosure_module  # noqa: E402
from llm_agent.agent.planning.planning_context import PlanningContextSnapshot, PlanningTool  # noqa: E402
from llm_agent.agent.planning.presentation import PlanningPresentationSnapshot  # noqa: E402
from llm_agent.agent.planning.tool_disclosure import disclose_tools, render_tool_guidance  # noqa: E402
from llm_agent.agent.tools.runtime_identity import RuntimeSnapshotIdentity  # noqa: E402


def benchmark(repetitions: int, tool_count: int = 8, *, reuse_enabled: bool = True) -> dict[str, Any]:
    tools = tuple(PlanningTool(
        name=f"tool_{number}", description=f"Read the requested fixture {number}.",
        input_schema={"type": "object", "properties": {
            f"field_{field}": {"type": "string", "description": "A user-supplied value."}
            for field in range(6)
        }},
    ) for number in range(tool_count))
    context = PlanningContextSnapshot(
        snapshot_id="fixture", registry_identity="registry", authority_identity="authority",
        tools=tools, eligible_names=frozenset(tool.name for tool in tools),
        runtime_identity=RuntimeSnapshotIdentity("registry", "workspace"), allowed_capabilities=frozenset({"read"}),
    )
    calls: Counter[str] = Counter()
    original_index = PlanningPresentationSnapshot.render_index
    original_detail = PlanningPresentationSnapshot.render_detailed
    original_result = disclosure_module._result

    def result_builder(*args: Any, **kwargs: Any) -> Any:
        result = original_result(*args, **kwargs)
        # replace resets init=False presentation strings, recreating the
        # previous repeated-rendering path without changing product settings.
        return result if reuse_enabled else replace(result)

    def index(view: Any, **kwargs: Any) -> str:
        calls["index_renders"] += 1
        return cast(str, original_index(view, **kwargs))

    def detail(view: Any, **kwargs: Any) -> str:
        calls["detail_renders"] += 1
        return cast(str, original_detail(view, **kwargs))

    def choose(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls["selector_requests_simulated"] += 1
        return {"tools": [tool.name for tool in tools[:4]]}

    digest = hashlib.sha256()
    events: list[Any] = []
    started = perf_counter()
    with patch.object(PlanningPresentationSnapshot, "render_index", index), patch.object(PlanningPresentationSnapshot, "render_detailed", detail), patch.object(disclosure_module, "_result", result_builder):
        for _ in range(repetitions):
            owner = SimpleNamespace(
                planning_context=context, get_planning_view=lambda kind: context.present(kind),
                context_manager=SimpleNamespace(ask_model=choose),
                session=SimpleNamespace(hardware_profile=SimpleNamespace(context_limit=8_192), config={}),
                _emit=lambda event, data: events.append((event, data)),
            )
            result = disclose_tools(owner, planner_kind="linear", objective="Read the fixture.")
            assert result is not None and result.selection_valid
            guidance = render_tool_guidance(owner, result)
            digest.update(guidance.encode("utf-8"))
    return {
        "repetitions": repetitions, "eligible_tools": tool_count, "calls": dict(calls),
        "reuse_enabled": reuse_enabled,
        "guidance_sha256": digest.hexdigest(),
        "events_sha256": hashlib.sha256(json.dumps(events, sort_keys=True).encode("utf-8")).hexdigest(),
        "elapsed_ms_local": round((perf_counter() - started) * 1_000, 3),
        "model_used": False, "network_used": False, "timing_predicts_inference_latency": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", action="store_true", help="Compare repeated rendering and reuse with the same fixture.")
    parser.add_argument("--samples", type=int, default=1, help="Repeat samples, alternating comparison order.")
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 1_000:
        parser.error("repetitions must be between 1 and 1000")
    if not 1 <= args.samples <= 10:
        parser.error("samples must be between 1 and 10")
    report = run_samples(args.repetitions, args.samples, args.compare)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"]))


def run_samples(repetitions: int, samples: int, compare: bool) -> dict[str, Any]:
    results = []
    for sample in range(samples):
        order = ((False, True) if sample % 2 == 0 else (True, False)) if compare else (True,)
        for enabled in order:
            for size in (4, 8):
                results.append({"sample": sample + 1, **benchmark(repetitions, size, reuse_enabled=enabled)})
    summary = []
    for size in (4, 8):
        for enabled in ((False, True) if compare else (True,)):
            rows = [row for row in results if row["eligible_tools"] == size and row["reuse_enabled"] is enabled]
            summary.append({
                "eligible_tools": size, "reuse_enabled": enabled, "samples": len(rows),
                "repetitions_per_sample": repetitions,
                "median_elapsed_ms_local": round(median(row["elapsed_ms_local"] for row in rows), 3),
                "calls_per_sample": rows[0]["calls"],
                "guidance_sha256": rows[0]["guidance_sha256"], "events_sha256": rows[0]["events_sha256"],
            })
    return {"schema_version": 1, "results": results, "summary": summary}


if __name__ == "__main__":
    main()
