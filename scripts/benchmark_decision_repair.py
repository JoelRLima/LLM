"""Offline before/after trace comparison for authorized structured decision repair."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

FIXTURE = r'''
import json,re
from llm_agent.agent.llm.context_manager import ContextManager
from llm_agent.agent.llm.contracts import ModelResponse,ProviderCapabilities
from llm_agent.agent.llm.decision_contract import ModelRequestContract
from llm_agent.agent.llm.session import ChatSession
from llm_agent.agent.runtime.budget import TaskBudgetLedger,BudgetExhausted
from llm_agent.agent.state import AgentState
VALID='{"action":"direct_response","answer":"fixture answer"}'
MARKER='HARNESS DECISION FORMAT FEEDBACK'
class Gateway:
    provider_name='offline-repair-fixture'
    model='offline-repair-fixture'
    capabilities=ProviderCapabilities(streaming=False)
    def __init__(self,responses):self.responses=list(responses);self.requests=[]
    def complete(self,request):
        self.requests.append(request)
        value=self.responses.pop(0)
        return value if isinstance(value,ModelResponse) else ModelResponse(content=value)
    def measure_request_input_tokens(self,request):return None
    def count_tokens(self,text):return max(1,len(text)//4)
cases=[('valid',[VALID],8),('json',['ATTACKER_INSTRUCTION {invalid',VALID],8),('contract',['{"action":"unknown"}',VALID],8),
       ('length',[ModelResponse(content=VALID,finish_reason='length'),VALID],8),
       ('filtered',[ModelResponse(content=VALID,finish_reason='content_filter')],8),
       ('second_invalid',['first bad','last bad'],8),('call_budget',['bad',VALID],1)]
results=[]
for name,responses,limit in cases:
    gateway=Gateway(responses)
    ledger=TaskBudgetLedger(max_model_calls=limit)
    session=ChatSession('ORIGINAL_SYSTEM',{'model':'fixture','agent_max_tokens':64,'hardware_profile':'low_vram_8gb'},gateway=gateway,budget_ledger=ledger)
    manager=ContextManager(session,AgentState(),workspace_root='.')
    try:
        result=manager.ask_model('ORIGINAL_REQUEST',base_prompt='BASE',step_type='plan',request_contract=ModelRequestContract.INITIAL_PLAN,include_task_definition=False)
        outcome=result.get('error_code') or result.get('action')
    except BudgetExhausted:
        outcome='TASK_BUDGET_EXHAUSTED'
    prompts=[request.messages[-1].content for request in gateway.requests]
    notes=[prompt.split(MARKER,1)[1] for prompt in prompts if MARKER in prompt]
    assert not any('ATTACKER_INSTRUCTION' in prompt for prompt in prompts)
    assert session.messages==[{'role':'system','content':'ORIGINAL_SYSTEM'}]
    results.append({'case':name,'outcome':outcome,'provider_calls_simulated':len(prompts),'ledger_calls':ledger.snapshot().model_calls,
                    'feedback_count':len(notes),'feedback_codes':[re.search(r'reason_code=(\w+)',note).group(1) for note in notes],
                    'initial_prompt_unchanged':prompts[0]=='ORIGINAL_REQUEST',
                    'retry_has_original_request':all(prompt.startswith('ORIGINAL_REQUEST') for prompt in prompts),
                    'feedback_chars':[len(note)+len(MARKER) for note in notes]})
print(json.dumps(results))
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = {}
    for label, executable, prefix in (
        ("previous_package", args.reference_python, ""),
        ("current_source", Path(sys.executable), f"import sys;sys.path.insert(0,{str(root / 'src')!r});"),
    ):
        result = subprocess.run([str(executable), "-I", "-c", prefix + FIXTURE],
                                capture_output=True, text=True, check=True)
        report[label] = json.loads(result.stdout)
    for before, after in zip(report["previous_package"], report["current_source"], strict=True):
        for field in ("case", "outcome", "provider_calls_simulated", "ledger_calls", "initial_prompt_unchanged"):
            assert before[field] == after[field], (before["case"], field)
        assert before["feedback_count"] == 0
        expected = 1 if after["case"] in {"json", "contract", "length", "second_invalid"} else 0
        assert after["feedback_count"] == expected
        assert after["retry_has_original_request"]
    report.update(reference_python=str(args.reference_python), model_used=False,
                  fixture_results_predict_model_accuracy=False,
                  limits="Scripted outputs compare request shape and gates, not real-model repair success or cost.")
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"episodes_per_arm": len(report["current_source"]), "outcomes_and_calls_preserved": True,
                      "model_used": False, "output": str(args.output)}))


if __name__ == "__main__":
    main()
