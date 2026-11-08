// Synthetic transport proof: no model, queue service or external commands run here.
const batteryCalls = [];
const info = {workspace_id:'battery-project',workspace_name:'Projeto de testes',workspace_root:'D:\\Projeto',mode:'READ_ONLY',
  profile:{name:'local_8gb',provider:'fixture',model:'Modelo de demonstração'},commands:[],inspection_kinds:['files','tasks','tools'],
  availability:{backend:{configured:true,state:'unverified'},resources:{files:'available',tasks:'available',tools:'available'}}};
let queue = null, queueId = null, runActive = false, pauseRequested = false;
let pick = null;
const snapshot = () => ({accepted:true,workspace_id:info.workspace_id,queue_id:queueId,run_active:runActive,pause_requested:pauseRequested,
  recent:queueId?[{id:queueId,label:'Tarefas de demonstração',path:'D:\\Filas\\demo'}]:[],
  profiles:[{name:'local_8gb',model:'Modelo de demonstração',provider:'fixture'}],selected_profile:'local_8gb',queue:queue?JSON.parse(JSON.stringify(queue)):null});
let readState = async () => snapshot();
window.pywebview = {api:{initialize:async()=>info,availability:async()=>info.availability,attention:async()=>null,
  wait_events:()=>new Promise(()=>{}),outputs:async()=>({state:'available',items:[]}),update_status:async()=>({state:'unmanaged',available:false}),
  test_queue_state:async()=>readState(),
  test_queue_pick_directory:async id=>({accepted:true,path:pick,cancelled:!pick}),
  test_queue_select:async(id,action,value)=>{
    batteryCalls.push({action,value,workspace:id});
    queueId='q'.repeat(64);queue={queue:'D:\\Filas\\demo',manifest_hash:'a'.repeat(64),identity:null,status:'ready',runner_active:false,
      counts:{pending:2},without_checks:[],not_imported:[],cases:[{id:'01-json',status:'pending',checks:1},{id:'02-function',status:'pending',checks:1}],elapsed_seconds:0};
    return snapshot();
  },
  test_queue_action:async(id,selected,fingerprint,action,value,options)=>{
    batteryCalls.push({action,value,workspace:id,selected,fingerprint,options});
    if(action==='check')return {accepted:true,preflight:{...queue,configuration:{resume_compatible:true,task_budgets:{max_model_calls:20}}}};
    if(action==='detach'){runActive=true;queue.runner_active=true;queue.status='running';return {accepted:true,worker:{status:'starting',pid:123,log:'fixture.log',message:'Worker solicitado.'}};}
    if(action==='pause'){pauseRequested=true;return {accepted:true,requested:'pause'};}
    if(action==='stop'){runActive=false;queue.runner_active=false;queue.status='stopped';return {accepted:true,requested:'stop'};}
    if(action==='details')return {accepted:true,details:{id:value,status:'failed',execution:{success:true,status:'completed',answer:'<img src=x onerror=bad()>'},
      verification:{status:'failed',checks:[{kind:'text_equals',path:'result.txt',passed:false,error:'FileNotFoundError'}]}},
      detail_summary:'Sucesso declarado pelo runtime: sim\nVerificação externa: Reprovada\nO arquivo esperado não foi produzido.'};
    return snapshot();
  },
  test_queue_start:async(id,selected,fingerprint,options)=>{
    batteryCalls.push({action:'start',workspace:id,selected,fingerprint,options});
    runActive=true;pauseRequested=false;queue.status='running';queue.counts={running:1,pending:1};queue.cases[0].status='running';
    return {accepted:true,request_id:'battery-'+batteryCalls.filter(call=>call.action==='start').length};
  },
  test_queue_export:async()=>({accepted:true,report_path:'D:\\Filas\\demo\\report.html'})
}};
window.addEventListener('load',async()=>{
  const proof=document.createElement('pre');proof.id='qa-proof';proof.hidden=true;document.body.append(proof);
  const get=id=>document.getElementById(id);
  const check=(condition,message)=>{if(!condition)throw new Error(message);};
  const pause=()=>new Promise(resolve=>setTimeout(resolve,40));
  try {
    if(new URLSearchParams(location.search).get('theme')==='light')document.documentElement.dataset.theme='light';
    window.dispatchEvent(new Event('pywebviewready'));await pause();
    const ids=[...document.querySelectorAll('[id]')].map(element=>element.id);
    check(ids.length===new Set(ids).size,'duplicate ids');
    const draft='Meu pedido ainda está em edição.';get('prompt').value=draft;
    get('open-tests').click();await pause();
    check(get('tests-dialog').open,'test panel did not open');
    check(get('tests-prepare').open,'new queue guidance missing');
    check(batteryCalls.length===0,'opening tests caused execution');
    get('tests-source').value='keep this path';get('tests-browse').click();await pause();
    check(get('tests-source').value==='keep this path','cancelled picker erased the source');
    pick='D:\\Minhas tarefas';get('tests-browse').click();await pause();
    check(get('tests-source').value===pick,'native picker not applied');
    get('tests-create').click();get('tests-create').click();await pause();
    check(batteryCalls.filter(call=>call.action==='create').length===1,'double import');
    get('tests-live').checked=true;get('tests-live').dispatchEvent(new Event('change'));
    check(get('tests-start').disabled,'run allowed without preflight');
    get('tests-check').click();await pause();
    check(!get('tests-start').disabled,'checked queue cannot start');
    get('tests-case-seconds').value='200';get('tests-case-seconds').dispatchEvent(new Event('change'));
    check(get('tests-start').disabled,'changed settings reused stale preflight');
    get('tests-check').click();await pause();
    get('tests-start').click();get('tests-start').click();await pause();
    check(batteryCalls.filter(call=>call.action==='start').length===1,'double start');
    check(batteryCalls.find(call=>call.action==='start').options.automatic_approval===false,'automatic tools granted implicitly');
    check(busy,'battery bypassed normal request owner');
    closeTests();check(!get('tests-dialog').open,'panel close failed');
    check(!batteryCalls.some(call=>call.action==='stop'),'closing panel stopped the run');
    check(!get('open-tests').disabled,'cannot reopen running queue');
    get('open-tests').click();await pause();
    check(!get('tests-pause').disabled,'pause unavailable during execution');
    get('tests-pause').click();await pause();
    queue.status='paused';runActive=false;pauseRequested=false;queue.counts={verified:1,pending:1};queue.cases[0].status='verified';
    consume([{type:'settled',request_id:'battery-1',observation:{status:'incomplete',success:false,answer:'Bateria pausada.'},reference:null}]);
    await refreshTests();
    check(!busy,'settlement left UI busy');
    get('tests-live').checked=true;get('tests-live').dispatchEvent(new Event('change'));get('tests-check').click();await pause();
    get('tests-start').click();await pause();
    check(batteryCalls.filter(call=>call.action==='start').length===2,'resume not sent');
    queue.status='completed';runActive=false;queue.counts={verified:1,failed:1};queue.cases[0].status='verified';queue.cases[1].status='failed';
    consume([{type:'settled',request_id:'battery-2',observation:{status:'failed',success:false,answer:'Um teste reprovado.'},reference:null}]);await refreshTests();
    get('tests-filter').value='attention';get('tests-filter').dispatchEvent(new Event('change'));
    check(document.querySelectorAll('#tests-cases article').length===1,'attention filter incorrect');
    document.querySelector('#tests-cases button').click();await pause();
    check(get('tests-detail-summary').textContent.includes('não foi produzido'),'independent evidence missing');
    check(!get('tests-detail').querySelector('img'),'model output became executable markup');
    get('tests-export').click();await pause();await refreshTests();
    check(get('tests-location').textContent.includes('report.html'),'export path disappeared on refresh');
    queue.status='ready';queue.counts={pending:2};queue.cases.forEach(item=>item.status='pending');await refreshTests();
    get('tests-background').checked=true;get('tests-live').checked=true;get('tests-live').dispatchEvent(new Event('change'));
    get('tests-check').click();await pause();get('tests-start').click();get('tests-start').click();await pause();
    check(batteryCalls.filter(call=>call.action==='detach').length===1,'background launch missing or duplicated');
    check(batteryCalls.filter(call=>call.action==='start').length===2,'background launch used window worker');
    check(!busy,'background launch retained interface request ownership');
    closeTests();await openTests();check(!get('tests-stop').disabled,'independent worker cannot be stopped after reopening');
    get('tests-stop').click();await pause();
    const before=batteryCalls.length;
    renderSessionInfo({...info,workspace_id:'other-project'});
    await testQueueAction('check');check(batteryCalls.length===before,'stale workspace acted on queue');renderSessionInfo(info);
    await refreshTests();
    let release;readState=()=>new Promise(resolve=>{release=resolve;});
    const late=refreshTests();closeTests();release(snapshot());await late;
    check(!get('tests-dialog').open,'late state reopened panel');
    readState=async()=>snapshot();await openTests();
    check(get('prompt').value===draft,'test actions changed chat draft');
    check(document.documentElement.scrollWidth<=innerWidth,'page overflows viewport');
    check(get('tests-dialog').scrollWidth<=get('tests-dialog').clientWidth+2,'dialog overflows viewport');
    clearInterval(testsTimer);get('tests-dialog').scrollTop=0;
    proof.textContent=JSON.stringify({passed:true,unique_ids:true,source_picker:true,preflight_required:true,settings_fenced:true,
      double_click_guard:true,pause_resume:true,independent_evidence:true,background_worker:true,context_fences:true,draft_preserved:true,
      model_used:false,start_calls:batteryCalls.filter(call=>call.action==='start').length,viewport:innerWidth});
  } catch(error) { proof.textContent=JSON.stringify({passed:false,error:String(error)}); }
});
