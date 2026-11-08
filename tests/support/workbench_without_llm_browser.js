// Synthetic bridge: proves presentation only; Python tests exercise the real core.
const calls=[];
const info={workspace_id:'offline-project',workspace_name:'Projeto local',workspace_root:'D:/Local',mode:'READ_ONLY',llm_enabled:false,profile:null,commands:[],
 availability:{backend:{configured:false,state:'not_configured'},resources:{files:'available',tasks:'available',tools:'unavailable'}}};
const queue={queue:'D:/Queue',manifest_hash:'a'.repeat(64),status:'ready',counts:{pending:1},without_checks:['01'],not_imported:[],cases:[{id:'01',status:'pending',checks:0}]};
const state=()=>({accepted:true,workspace_id:info.workspace_id,queue_id:'q'.repeat(64),queue,profiles:[],recent:[],run_active:false,configuration_error:'Configuration missing'});
let ref;
window.pywebview={api:{initialize:async()=>info,availability:async()=>info.availability,attention:async()=>null,wait_events:()=>new Promise(()=>{}),
 outputs:async()=>({state:'available',items:[]}),update_status:async()=>({state:'unmanaged',available:false}),
 set_llm_enabled:async enabled=>{calls.push({toggle:enabled});info.llm_enabled=enabled;return {accepted:true,session:info};},
 navigation_options:async()=>({accepted:true,workspace_id:info.workspace_id,profiles:[],recent_workspaces:[],configuration_error:'Configuration missing'}),
 query_workspace:async(kind,args)=>{calls.push({kind,args});ref={token:'local',workspace_id:info.workspace_id,run_id:null,artifact_id:'local',operation:{query:kind},title:'Local'};return {accepted:true,reference:ref};},
 read_report:async()=>({token:'local',reference:ref,text:'def add(a,b): return a+b',renderer:'text',next_offset:null}),
 submit:async text=>{calls.push({submit:text});return {accepted:true,request_id:'local-analysis'};},
 test_queue_state:async()=>state(),
 test_queue_action:async(id,q,hash,action)=>{calls.push({action});return {accepted:true,local_only:true,preflight:queue};}
}};
window.addEventListener('load',async()=>{
 const proof=document.createElement('pre');proof.id='qa-proof';proof.hidden=true;document.body.append(proof);
 const get=id=>document.getElementById(id),pause=()=>new Promise(r=>setTimeout(r,35));
 const check=(v,msg)=>{if(!v)throw Error(msg);};
 try {
  if(new URLSearchParams(location.search).get('theme')==='light')document.documentElement.dataset.theme='light';
  window.dispatchEvent(new Event('pywebviewready'));await pause();
  const draft='Meu pedido por terminar';get('prompt').value=draft;
  check(!get('nav-files').disabled,'files blocked without model');
  check(get('llm-toggle').textContent==='Ativar LLM','activation not obvious');
  await submit();check(get('prompt').value===draft,'local chat lost draft');check(!calls.some(c=>c.submit),'local chat sent to model');
  get('llm-toggle').click();get('llm-toggle').click();await pause();
  check(calls.filter(c=>'toggle' in c).length===1,'duplicate activation');
  check(get('llm-toggle').textContent==='Desativar LLM','enabled state hidden');
  check(get('prompt').value===draft,'activation lost draft');
  setBusy(true);check(get('llm-toggle').disabled,'toggle enabled while busy');setBusy(false);
  get('llm-toggle').click();await pause();check(info.llm_enabled===false,'deactivation failed');
  get('workspace-switch').click();await pause();check(get('navigation-dialog').open,'workspace picker blocked');closeNavigationChoice();
  get('nav-files').click();await pause();
  get('query-kind').value='read';get('query-path').value='folder/sample.py';get('run-workspace-query').click();await pause();
  check(!get('analyze-file-locally').disabled,'local analysis unavailable');
  get('analyze-file-locally').click();get('analyze-file-locally').click();await pause();
  check(calls.filter(c=>c.submit).length===1,'duplicate submission');
  check(calls.find(c=>c.submit).submit==='/code analyze "folder/sample.py"','wrong code action');
  check(get('prompt').value===draft,'draft lost');
  consume([{type:'settled',request_id:'local-analysis',observation:{status:'succeeded',answer:'Static analysis complete',model_used:false},reference:null}]);
  get('open-tests').click();await pause();check(!get('tests-check').disabled,'local preflight blocked without profile');
  get('tests-check').click();await pause();check(get('tests-preflight').textContent.includes('sem LLM'),'local explanation missing');
  check(get('tests-start').disabled,'model execution allowed without profile');
  check(!get('tests-model-toggle').hidden,'inline activation missing');get('tests-model-toggle').click();await pause();
  check(info.llm_enabled===true,'inline activation failed');
  check(get('tests-start').disabled,'activation bypassed execution authorization');
  await toggleLlm();check(info.llm_enabled===false,'return to local mode failed');
  check(get('prompt').value===draft,'preflight lost draft');
  check(document.documentElement.scrollWidth<=innerWidth,'page overflow');
  check(get('tests-dialog').scrollWidth<=get('tests-dialog').clientWidth,'dialog overflow');
  if(new URLSearchParams(location.search).get('view')!=='queue') {
   closeTests();get('nav-files').click();await pause();get('query-kind').value='read';get('query-path').value='folder/sample.py';get('run-workspace-query').click();await pause();
  }
  proof.textContent=JSON.stringify({passed:true,model_used:false,files_local:true,workspace_without_profile:true,local_code:true,explicit_activation:true,draft_preserved:true,local_preflight:true,model_run_blocked:true,viewport:innerWidth});
 }catch(error){proof.textContent=JSON.stringify({passed:false,error:String(error)});}
});
