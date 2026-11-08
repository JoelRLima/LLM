"""Exercise recovery and profile controls in headless Edge with a synthetic bridge."""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

FIXTURE = r'''
const qaCalls = [];
const qaQueueActions = [];
let qaQueue = [{pending_id:1,text:'Compare notes.txt com o resumo anterior.'},{pending_id:2,text:'Liste os testes disponíveis no projeto.'}];
let qaQueueState = 'RUNNING';
const qaInfo = {workspace_id:'qa-project',workspace_name:'Projeto de exemplo',workspace_root:'D:\Fixture\Projeto',
  mode:'READ_ONLY',profile:{name:'QA local',provider:'fixture',model:'sem inferência'},
  availability:{backend:{configured:true,state:'unverified',connectivity:'unverified',model_readiness:'unverified'},
    resources:{files:'available',tasks:'available',tools:'available'}},
  commands:[{action_id:'model.select',command:'/model select',title:'Selecionar perfil',payload_hint:'PERFIL'}]};
const queueSnapshot=()=>({workspace_id:'qa-project',state:qaQueueState,controller_state:qaQueueState,
  pending_items:qaQueue,pending_count:qaQueue.length,attention_pending:false,warning_count:0,error_count:0});
window.pywebview = {api:{initialize:async()=>qaInfo,availability:async()=>qaInfo.availability,
  attention:async()=>null,wait_events:()=>new Promise(()=>{}),outputs:async()=>({state:'available',items:[]}),
  task_status:async()=>queueSnapshot(),tasks:async()=>({current:queueSnapshot(),runs:[],next_offset:null,
    history_state:'available',historical_reports_state:'not_available: o serviço atual não oferece catálogo de relatórios históricos'}),
  control:async text=>{qaQueueActions.push(text);qaQueue=qaQueue.filter(item=>item.pending_id!==Number(text.split(' ').at(-1)));return {accepted:true,observation:{status:'succeeded'}};},
  submit:async text=>{qaCalls.push(text);if(text.startsWith('/pending send ')){qaQueueActions.push(text);qaQueue=qaQueue.filter(item=>item.pending_id!==Number(text.split(' ').at(-1)));}return {accepted:true,request_id:'qa-request'};},
  update_status:async()=>({state:'unmanaged',available:false,restart_required:false,message:'Fixture local'})}};
window.addEventListener('load',async()=>{
  const check=(value,message)=>{if(!value)throw new Error(message);};
  const proof=document.createElement('pre');proof.id='qa-proof';proof.hidden=true;document.body.append(proof);
  try {
    if(new URLSearchParams(location.search).get('theme')==='light')document.documentElement.dataset.theme='light';
    window.dispatchEvent(new Event('pywebviewready'));
    await new Promise(resolve=>setTimeout(resolve,80));
    const ids=[...document.querySelectorAll('[id]')].map(element=>element.id);
    check(ids.length===new Set(ids).size,'duplicate DOM ids');
    document.getElementById('session-open').click();
    const profile=document.getElementById('session-profile-model');
    check(profile.tagName==='BUTTON','profile action resolves to text');
    profile.click();
    check(!document.getElementById('command-palette-backdrop').hidden,'profile button did not open commands');
    check(document.getElementById('command-palette-title').textContent==='Perfil e modelo','wrong command scope');
    closeCommandPalette();
    const input=document.getElementById('prompt');
    input.value='Leia notes.txt e explique o conteúdo sem alterar arquivos.';
    await submit();
    input.value='Meu próximo pedido, ainda em edição.';
    consume([{type:'settled',request_id:'qa-request',observation:{status:'failed',reason_code:'MODEL_CONNECTION_ERROR',
      answer:'A conexão com o modelo foi interrompida. O resultado parcial permanece disponível para revisão.'},reference:null}]);
    const review=document.querySelector('.request-review-button');
    check(Boolean(review),'review action missing');
    check(document.getElementById('run-status').textContent==='Execução não concluída','failure label missing');
    const colorProbe=document.createElement('span');colorProbe.style.color='var(--red)';document.body.append(colorProbe);
    const errorColor=getComputedStyle(colorProbe).color;colorProbe.remove();
    check(getComputedStyle(document.querySelector('.event[data-severity="error"] i')).color===errorColor,'failure still uses success color');
    check(getComputedStyle(document.querySelector('.event[data-severity="error"]')).borderTopColor===errorColor,'failure border still uses success color');
    review.click();
    check(input.value.startsWith('Leia notes.txt'),'request not recovered');
    document.getElementById('restore-draft').click();
    check(input.value==='Meu próximo pedido, ainda em edição.','current draft lost');
    check(qaCalls.length===1,'recovery resent a request');
    let queueVerified=false;
    if(new URLSearchParams(location.search).get('view')==='queue') {
      await openTasksPanel();
      setBusy(true,'qa-active');
      check(document.querySelector('[data-queue-action="send"]').disabled,'queue send enabled during work');
      document.querySelectorAll('[data-queue-action="discard"]')[1].click();
      await new Promise(resolve=>setTimeout(resolve,40));
      check(qaQueue.length===1&&qaQueue[0].pending_id===1,'removed wrong queued request');
      qaQueueState='IDLE';setBusy(false);renderTaskStatus(queueSnapshot());
      document.querySelector('[data-queue-action="send"]').click();
      await new Promise(resolve=>setTimeout(resolve,40));
      check(qaQueue.length===0,'sent request remained queued');
      check(qaQueueActions.join('|')==='/pending discard 2|/pending send 1','queue actions bypassed canonical commands');
      check(qaCalls.length===2,'queue sent more than once');
      check(input.value==='Meu próximo pedido, ainda em edição.','queue actions replaced current draft');
      consume([{type:'settled',request_id:'qa-request',observation:{status:'succeeded',answer:'Pedido da fila enviado pelo fluxo canônico simulado.'},reference:null}]);
      qaQueue=[{pending_id:3,text:'Explique a organização dos arquivos do projeto.'},{pending_id:4,text:'Compare os resultados dos testes disponíveis.'}];
      renderTaskStatus(queueSnapshot());queueVerified=true;
    }
    check(document.documentElement.scrollWidth<=innerWidth,'page overflows viewport');
    proof.textContent=JSON.stringify({passed:true,unique_ids:true,profile_action:true,failure_color:true,review_without_resend:true,
      current_draft_preserved:true,queue_verified:queueVerified,submit_calls:qaCalls.length,viewport:innerWidth,model_used:false});
  }catch(error){proof.textContent=JSON.stringify({passed:false,error:String(error)});}
});
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--static-dir", type=Path, help="Optional installed static assets instead of checkout assets.")
    parser.add_argument("--fixture", type=Path, help="Optional local synthetic bridge and browser assertions.")
    parser.add_argument("--edge", type=Path, default=Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    scratch = root / ".tmp" / ("ux-recovery-browser-" + uuid.uuid4().hex)
    fixture = scratch / "fixture"
    static = args.static_dir or root / "src/llm_agent/interfaces/workbench/static"
    shutil.copytree(static, fixture)
    page = fixture / "index.html"
    page.write_text(page.read_text(encoding="utf-8").replace("</body>", '<script src="qa_fixture.js"></script></body>'), encoding="utf-8")
    (fixture / "qa_fixture.js").write_text(args.fixture.read_text(encoding="utf-8") if args.fixture else FIXTURE, encoding="utf-8")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for label, width, theme, view in (("desktop-dark", 1365, "dark", "chat"), ("compact-light", 800, "light", "chat"), ("queue-desktop", 1365, "dark", "queue")):
        screenshot = (args.output_dir / f"{label}.png").resolve()
        result = subprocess.run([
            str(args.edge), "--headless=new", "--disable-background-networking", "--disable-component-update",
            "--no-first-run", "--disable-sync", f"--user-data-dir={scratch / label}",
            f"--window-size={width},950", "--force-device-scale-factor=1", "--hide-scrollbars",
            "--virtual-time-budget=2000", "--dump-dom", f"--screenshot={screenshot}",
            page.as_uri() + f"?theme={theme}&view={view}",
        ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45, check=True)
        match = re.search(r'<pre id="qa-proof"[^>]*>(.*?)</pre>', result.stdout, re.S)
        if match is None:
            raise RuntimeError("Headless browser did not publish its test result")
        proof = json.loads(html.unescape(match.group(1)))
        if proof.get("passed") is not True:
            raise RuntimeError(proof)
        results.append({"view": label, "proof": proof, "screenshot": str(screenshot)})
    report = {"browser": "Edge headless", "static_dir": str(static), "synthetic_bridge": True, "model_used": False, "results": results}
    (args.output_dir / "browser-proof.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
