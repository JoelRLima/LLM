"""Compare selection-cache isolation against an independently installed package."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

FIXTURE = '''
import json
from types import SimpleNamespace
from llm_agent.agent.planning.planning_context import PlanningContextSnapshot, PlanningTool
from llm_agent.agent.planning.tool_disclosure import disclose_tools
from llm_agent.agent.tools.runtime_identity import RuntimeSnapshotIdentity
from llm_agent.agent.runtime.correlation import RunCorrelation
names=['tool_a','tool_b','tool_c','tool_d','tool_e']
context=PlanningContextSnapshot(snapshot_id='context',registry_identity='registry',authority_identity='authority',tools=tuple(PlanningTool(name=n,description='Purpose '+n,input_schema={'type':'object'}) for n in names),eligible_names=frozenset(names),runtime_identity=RuntimeSnapshotIdentity('registry','workspace'),allowed_capabilities=frozenset({'read'}))
def owner(responses):
    calls=[]
    def ask(*args,**kwargs):
        calls.append(1)
        return responses.pop(0)
    return SimpleNamespace(planning_context=context,get_planning_view=lambda kind:context.present(kind),context_manager=SimpleNamespace(ask_model=ask),session=SimpleNamespace(hardware_profile=SimpleNamespace(context_limit=8192),config={}),allowed_capabilities=frozenset({'read'}),run_correlation=RunCorrelation.fresh(),calls=calls)
def select(o, objective='inspect', **kw):
    return disclose_tools(o,planner_kind='linear',objective=objective,**kw)
o=owner([{'tools':['tool_a']},{'tools':['tool_b']}])
select(o)
assert select(o).cache_hit
same_task_calls=len(o.calls)
o.run_correlation=o.run_correlation.child('child')
changed=select(o)
changed_task={'selection':sorted(changed.selected_names),'model_calls_simulated':len(o.calls)}
o=owner([{'tools':['tool_a']},{'tools':['hidden']},{'tools':['hidden']},{'tools':['tool_b']}])
select(o)
assert not select(o,force_refresh=True).selection_valid
refreshed=select(o)
failed_refresh={'selection':sorted(refreshed.selected_names),'model_calls_simulated':len(o.calls)}
o=owner([{'tools':['tool_a']}] * 100)
for i in range(100):select(o,str(i))
print(json.dumps({'same_task_calls_simulated':same_task_calls,'changed_task':changed_task,'after_failed_refresh':failed_refresh,'retained_entries_after_100_objectives':len(o._tool_disclosure_cache),'model_used':False}))
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    reports = {}
    for label, executable, prefix in (
        ("previous_package", args.reference_python, ""),
        ("current_source", Path(sys.executable), f"import sys;sys.path.insert(0,{str(root / 'src')!r});"),
    ):
        result = subprocess.run([str(executable), "-I", "-c", prefix + FIXTURE],
                                capture_output=True, text=True, check=True)
        reports[label] = json.loads(result.stdout)
    before, after = reports["previous_package"], reports["current_source"]
    assert before["changed_task"]["selection"] == before["after_failed_refresh"]["selection"] == ["tool_a"]
    assert after["changed_task"]["selection"] == after["after_failed_refresh"]["selection"] == ["tool_b"]
    assert before["retained_entries_after_100_objectives"] == 100
    assert after["retained_entries_after_100_objectives"] == 8
    assert before["same_task_calls_simulated"] == after["same_task_calls_simulated"] == 1
    report = {"reference_python": str(args.reference_python), **reports, "model_used": False,
              "limits": "Synthetic selection only; no tool execution or real-model quality measurement."}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
