const pluginCalls = [];
let pluginConsent = false;
window.confirm = () => pluginConsent;
const info = {workspace_id:'plugin-project',workspace_name:'Projeto de plugins',workspace_root:'D:\\Projeto',mode:'READ_ONLY',
  profile:{name:'Fixture',provider:'fixture',model:'sem inferência'},commands:[],inspection_kinds:['files','tasks','tools'],
  availability:{backend:{configured:true,state:'unverified'},resources:{files:'available',tasks:'available',tools:'available'}}};
let rows = [];
let selected = [];
const state = () => ({accepted:true,workspace_id:info.workspace_id,items:JSON.parse(JSON.stringify(rows)),authorized_ids:[...selected],mode:'READ_ONLY'});
let readState = async () => state();
let rejectAction = false;
window.pywebview={api:{initialize:async()=>info,availability:async()=>info.availability,attention:async()=>null,
  wait_events:()=>new Promise(()=>{}),outputs:async()=>({state:'available',items:[]}),
  update_status:async()=>({state:'unmanaged',available:false}),plugin_state:async()=>readState(),
  plugin_action:async(workspace,action,id,value,fingerprint)=>{
    pluginCalls.push({workspace,action,id,value,fingerprint});
    if(rejectAction)return {accepted:false,message:'Configuração mudou. Atualize a lista.'};
    if(action==='register') rows=[{extension_id:'demo.extension',manifest_path:value,manifest_sha256:'a'.repeat(64),observed_sha256:'a'.repeat(64),
      manifest_status:'unchanged',required_capabilities:['read','process'],grants:[],enabled:false,activation_status:'not_enabled',diagnostic_codes:[],session_authorized:false,
      execution:{review_status:'matched',command:'["python", "demo.py"]',cwd:'D:\\Projeto',sandboxed:false,warning:'Processo confiável, sem sandbox.'}}];
    else if(action==='enable')rows[0].enabled=true;
    else if(action==='grant')rows[0].grants.push(value);
    else if(action==='authorize')selected=['demo.extension'];
    else if(action==='deauthorize'||action==='clear_authority')selected=[];
    else if(action==='replace'){rows[0].manifest_sha256=value;rows[0].manifest_status='unchanged';selected=[];}
    else if(action==='remove'){rows=[];selected=[];}
    else if(action==='disable'){rows[0].enabled=false;selected=[];}
    for(const row of rows){row.activation_status=!row.enabled?'disabled':row.grants.length===2?'ready':'blocked';row.session_authorized=selected.includes(row.extension_id);}
    return state();
  }}};
window.addEventListener('load',async()=>{
  const proof=document.createElement('pre');proof.id='qa-proof';proof.hidden=true;document.body.append(proof);
  const get=id=>document.getElementById(id);
  const check=(value,message)=>{if(!value)throw new Error(message);};
  const pause=()=>new Promise(resolve=>setTimeout(resolve,35));
  const find=(action,text='')=>[...document.querySelectorAll('#plugins-items button')].find(button=>button.dataset.pluginAction===action&&button.textContent.includes(text));
  try{
    if(new URLSearchParams(location.search).get('theme')==='light')document.documentElement.dataset.theme='light';
    window.dispatchEvent(new Event('pywebviewready'));await pause();
    const ids=[...document.querySelectorAll('[id]')].map(element=>element.id);
    check(ids.length===new Set(ids).size,'duplicate ids');
    const draft='Meu pedido ainda está em edição.';get('prompt').value=draft;
    get('more-resources-toggle').click();get('open-plugins').click();await pause();
    check(get('plugins-dialog').open,'manager did not open');
    check(get('plugins-status').textContent.includes('Nenhum plugin'),'empty state missing');
    check(pluginCalls.length===0,'open manager mutated config');
    get('plugins-manifest').value='D:\\Plugins\\Demo\\manifest.json';get('plugins-register').click();await pause();
    check(pluginCalls.length===1&&pluginCalls[0].action==='register','registration failed');
    check(find('authorize').disabled,'registration granted session authority');
    find('enable').click();await pause();
    find('grant','read').click();await pause();
    check(find('authorize').disabled,'missing process grant admitted');
    find('grant','process').click();await pause();
    check(!find('authorize').disabled,'ready plugin cannot be authorized');
    find('authorize').click();await pause();
    check(!pluginCalls.some(call=>call.action==='authorize'),'cancelled trust review granted authority');
    pluginConsent = true;
    find('authorize').click();find('authorize')?.click();await pause();
    check(pluginCalls.filter(call=>call.action==='authorize').length===1,'double click duplicated authorization');
    check(find('deauthorize'),'session authorization missing');
    check(pluginCalls.every(call=>call.workspace==='plugin-project'),'workspace fence lost');
    check(pluginCalls.filter(call=>call.id).every(call=>call.fingerprint==='a'.repeat(64)),'reviewed fingerprint missing');
    setBusy(true,'active');check(find('deauthorize').disabled,'actions available during task');setBusy(false);
    const before=pluginCalls.length;
    renderSessionInfo({...info,workspace_id:'other-project'});
    await actOnPlugin('deauthorize',pluginsSnapshot.items[0]);
    check(pluginCalls.length===before,'stale workspace mutated plugins');renderSessionInfo(info);
    rejectAction=true;find('deauthorize').click();await pause();
    check(get('plugins-status').textContent.includes('Configuração mudou'),'backend error hidden');
    check(get('plugins-register').disabled,'failed operation retained actionable stale state');
    rejectAction=false;await refreshPlugins();
    let release;readState=()=>new Promise(resolve=>{release=resolve;});
    const pending=refreshPlugins();closePlugins();release(state());await pending;
    check(!get('plugins-dialog').open,'late reply reopened dialog');
    readState=async()=>state();await openPlugins();
    rows[0].observed_sha256='b'.repeat(64);rows[0].manifest_status='changed';selected=[];
    await refreshPlugins();find('replace').click();await pause();
    check(pluginCalls.at(-1).value==='b'.repeat(64),'new fingerprint not reviewed');
    check(rows[0].manifest_sha256==='b'.repeat(64),'manifest update missing');
    find('remove').click();await pause();
    check(rows.length===0,'plugin removal missing');
    get('plugins-manifest').value='D:\\Plugins\\Demo\\manifest.json';get('plugins-register').click();await pause();
    check(get('prompt').value===draft,'plugin administration displaced draft');
    check(document.documentElement.scrollWidth<=innerWidth,'viewport overflows');
    check(get('plugins-dialog').scrollWidth<=get('plugins-dialog').clientWidth,'dialog overflows');
    proof.textContent=JSON.stringify({passed:true,model_used:false,draft_preserved:true,separate_grants:true,explicit_session_authority:true,
      duplicate_click_blocked:true,busy_blocked:true,stale_workspace_blocked:true,errors_visible:true,late_response_ignored:true,mutation_calls:pluginCalls.length,viewport:innerWidth});
  }catch(error){proof.textContent=JSON.stringify({passed:false,error:String(error)});}
});
