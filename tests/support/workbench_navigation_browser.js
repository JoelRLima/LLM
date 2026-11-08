const navigationCalls = [];
const info = {workspace_id:'project-a',workspace_name:'Projeto A',workspace_root:'D:\\Projetos\\A',
  mode:'READ_ONLY',profile:{name:'Local',provider:'fixture',model:'small-model'},
  commands:[{action_id:'workspace.switch',command:'/workspace switch'}, {action_id:'model.select',command:'/model select'}],
  availability:{backend:{configured:true,state:'unverified'},resources:{files:'available',tasks:'available',tools:'available'}}};
const choices = {accepted:true,workspace_id:'project-a',
  profiles:[{name:'Local',provider:'fixture',model:'small-model'}, {name:'Perfil rápido',provider:'fixture',model:'second-model'}],
  recent_workspaces:['D:\\Projetos\\A','D:\\Meus projetos\\Projeto B']};
let choicesImpl = async () => choices;
window.pywebview={api:{initialize:async()=>info,availability:async()=>info.availability,
  navigation_options:async()=>choicesImpl(),attention:async()=>null,wait_events:()=>new Promise(()=>{}),
  outputs:async()=>({state:'available',items:[]}),update_status:async()=>({state:'unmanaged',available:false}),
  submit:async text=>{navigationCalls.push(text);return {accepted:true,request_id:'navigation-request'};}}};
window.addEventListener('load',async()=>{
  const proof=document.createElement('pre');proof.id='qa-proof';proof.hidden=true;document.body.append(proof);
  const check=(value,message)=>{if(!value)throw new Error(message);};
  const pause=()=>new Promise(resolve=>setTimeout(resolve,30));
  const get=id=>document.getElementById(id);
  try {
    if(new URLSearchParams(location.search).get('theme')==='light')document.documentElement.dataset.theme='light';
    window.dispatchEvent(new Event('pywebviewready'));await pause();
    const ids=[...document.querySelectorAll('[id]')].map(element=>element.id);
    check(ids.length===new Set(ids).size,'duplicate DOM ids');
    const draft='Meu pedido ainda em edição';get('prompt').value=draft;
    get('workspace-switch').click();await pause();
    check(get('navigation-dialog').open,'project chooser did not open');
    check(navigationCalls.length===0,'opening chooser sent a command');
    get('navigation-options').children[1].click();
    check(get('navigation-command').textContent==='/workspace switch D:\\Meus projetos\\Projeto B','path with spaces changed');
    check(get('prompt').value===draft,'project selection displaced draft');
    check(!navigationValue('folder\n/exit'),'multiline payload admitted');
    get('navigation-value').value='folder\x01/exit';syncNavigationChoice();
    check(get('navigation-apply').disabled,'multiline path admitted');
    get('navigation-options').children[1].click();
    get('navigation-apply').click();get('navigation-apply').click();await pause();
    check(navigationCalls.length===1,'selection submitted twice');
    check(navigationCalls[0]==='/workspace switch D:\\Meus projetos\\Projeto B','canonical project command not used');
    check(get('prompt').value===draft,'applying selection displaced draft');
    consume([{type:'settled',request_id:'navigation-request',observation:{status:'succeeded',answer:'Synthetic selection'},reference:null}]);
    get('session-open').click();get('session-profile-model').click();await pause();
    check(get('navigation-value').readOnly,'profile field should use configured choices');
    get('navigation-options').children[1].click();
    check(get('navigation-command').textContent==='/model select Perfil rápido','profile name changed');
    check(navigationCalls.length===1,'profile choice auto-submitted');
    setBusy(true,'busy');
    check(get('navigation-apply').disabled,'apply enabled during work');
    await applyNavigationChoice();check(navigationCalls.length===1,'busy chooser submitted');
    setBusy(false);get('navigation-apply').click();await pause();
    check(navigationCalls[1]==='/model select Perfil rápido','canonical profile command not used');
    consume([{type:'settled',request_id:'navigation-request',observation:{status:'failed',answer:'Synthetic rejection'},reference:null}]);
    check(get('prompt').value===draft,'rejection lost draft');
    let release;
    choicesImpl=()=>new Promise(resolve=>{release=resolve;});
    const loading=openNavigationChoice('workspace');closeNavigationChoice();
    release(choices);await loading;check(!get('navigation-dialog').open,'late response reopened dialog');
    choicesImpl=async()=>({...choices,workspace_id:'other-project'});
    await openNavigationChoice('workspace');check(get('navigation-apply').disabled,'stale workspace admitted');closeNavigationChoice();
    choicesImpl=async()=>({...choices,profiles:[],recent_workspaces:[]});
    await openNavigationChoice('profile');check(get('navigation-apply').disabled,'empty profile list admitted');closeNavigationChoice();
    choicesImpl=async()=>({accepted:false,message:'Configuração indisponível'});
    await openNavigationChoice('profile');check(get('navigation-status').textContent.includes('Configuração indisponível'),'error reason hidden');closeNavigationChoice();
    choicesImpl=async()=>choices;
    await openNavigationChoice(new URLSearchParams(location.search).get('view')==='queue'?'workspace':'profile');
    check(document.documentElement.scrollWidth<=innerWidth,'viewport overflows');
    check(get('navigation-dialog').scrollWidth<=get('navigation-dialog').clientWidth,'dialog overflows');
    proof.textContent=JSON.stringify({passed:true,model_used:false,canonical_commands:true,draft_preserved:true,
      duplicate_click_blocked:true,busy_blocked:true,late_response_ignored:true,stale_workspace_blocked:true,
      errors_visible:true,empty_profiles_blocked:true,multiline_path_blocked:true,submit_calls:navigationCalls.length,viewport:innerWidth});
  }catch(error){proof.textContent=JSON.stringify({passed:false,error:String(error)});}
});
