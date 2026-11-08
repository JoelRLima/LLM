const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');

function element(id = '') {
  return {id, textContent:'', value:'', disabled:false, hidden:false, dataset:{}, children:[], style:{},
    append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; this.textContent = ''; }, querySelector() { return null; },
    setAttribute(name, value) { this.attributes ||= {}; this.attributes[name] = value; },
    addEventListener(name, fn) { this.listeners ||= {}; this.listeners[name] = fn; }};
}
function section(start, end) { return source.slice(source.indexOf(start), source.indexOf(end)); }
function harness() {
  const els = Object.fromEntries(['send','cancel','nav-files','nav-tasks','nav-tools','run-workspace-query','model-availability',
    'workspace-name','workspace-name-status','session-mode-label','session-mode','profile-menu','workspace-switch','model-name','artifact-link','session-dialog',
    'inspector','resize-grip','copy-report','report-integrity','report-body','task-current-summary','task-current-state','task-runs','task-run-detail','workspace-query',
    'task-history','report-content','tab-title','report-heading','panel-kind-label','report-source','report-run',
    'report-workspace','completeness-label','report-state','query-kind','query-path','query-pattern','query-diff-paths',
    'query-path-row','query-pattern-row','query-diff-row','artifact-count'].map(id => [id, element(id)]));
  let resolveOutputs, resolveQuery;
  let taskFailure = null;
  let nextTaskPromise = null;
  const api = {outputs:() => new Promise(resolve => { resolveOutputs = resolve; }),
    query_workspace:() => new Promise(resolve => { resolveQuery = resolve; }),
    tasks:() => nextTaskPromise ? (value => { nextTaskPromise = null; return value; })(nextTaskPromise) : taskFailure ? Promise.reject(taskFailure) : Promise.resolve({current:{state:'running'},runs:[],history_state:'available',historical_reports_state:'available'}),
    test_connection:() => Promise.resolve({backend:{configured:true,state:'available'},resources:{files:'available',tasks:'available',tools:'available'}}),
    availability:() => Promise.resolve({backend:{configured:true,state:'available'},resources:{files:'available',tasks:'available',tools:'available'}})};
  const context = vm.createContext({$:id => els[id] ||= element(id), api:() => api,
    document:{createElement:tag => element(tag), querySelectorAll:() => []}, panelGeneration:0, panelMode:'artifact', panelRef:null,
    latestRef:{token:'latest'}, busy:false, workspaceQueryBusy:false, sessionEnded:false, busyActionInFlight:false,
    activeRequestId:null, MAX_STREAM_CHARS:1000, availabilityLabels:{available:'Available',busy:'Busy',forbidden:'Forbidden',not_implemented:'Unavailable'},
    sessionInfo:{availability:{backend:{configured:true},resources:{files:'available',tasks:'available',tools:'available'}}},
    availabilityRequestId:0, appliedAvailabilityRequestId:0, connectionProbeInFlight:false, initialized:true,
    showPanel(mode) { context.panelGeneration++; context.panelMode = mode; context.panelRef = null; },
    openPanel(ref) { context.panelGeneration++; context.panelMode = 'artifact'; context.panelRef = ref; },
    clearPanelContent(){}, renderReport(){}, updateQueryControls(){}, toast(text){context.toastMessages.push(text);}, toastMessages:[],
    renderSessionSummary(){context.sessionSummaryRenders++;}, sessionSummaryRenders:0,
    renderCurrentTaskSummary(snapshot){context.currentTaskSummarySnapshot=snapshot; els['task-current-summary'].textContent=snapshot.state || '';}, currentTaskSummarySnapshot:null,
    expireArtifact(){}, closePanel(){}, commandEntries:[], updateDraftRecoveryButton(){}, draftWorkspaceKey(){return null;}, clearDraftRecovery(){}});
  vm.runInContext(section('function setBusy(', 'function restoreDraftIfEmpty('), context);
  vm.runInContext(section('function renderSessionInfo(info, requestId', 'function openCommandPalette('), context);
  vm.runInContext(section('async function runWorkspaceQuery(', 'function currentTaskProjection('), context);
  vm.runInContext(section('function currentTaskProjection(', 'let taskHistoryOffset'), context);
  vm.runInContext(section('let taskHistoryOffset = 0;', 'async function showArtifacts('), context);
  vm.runInContext(section('async function showArtifacts(', 'async function inspectWorkspace('), context);
  const start = source.indexOf('const availabilityLabels =');
  const end = source.indexOf("$('model-availability').addEventListener", start);
  vm.runInContext(source.slice(start, end), context);
  const handlerEnd = source.indexOf('\n});', end) + '\n});'.length;
  vm.runInContext(source.slice(end, handlerEnd), context);
  return {context, els, api, outputs:() => resolveOutputs, query:() => resolveQuery, setTaskFailure(error){taskFailure = error;}, setTaskPromise(promise){nextTaskPromise = promise;}};
}

async function main() {
  const h = harness();
  h.context.panelMode = 'tasks';
  h.context.renderTaskStatus({state:'running',current_tool:'read_file',terminal_outcome:'RAW_CODE',unapproved_secret:'do-not-project'});
  assert.equal(h.els['task-current-summary'].textContent, 'running', 'task summary helper is called with the current allowlisted projection');
  assert.equal(h.context.currentTaskSummarySnapshot.terminal_outcome, 'RAW_CODE');
  assert.equal('unapproved_secret' in h.context.currentTaskSummarySnapshot, false, 'task summary receives only allowlisted fields');
  assert.equal(JSON.parse(h.els['task-current-state'].textContent).unapproved_secret, undefined, 'technical JSON stays allowlisted too');
  h.els['nav-files'].dataset.available = 'no'; h.els['nav-tasks'].dataset.available = 'yes'; h.els['nav-tools'].dataset.available = 'no';
  h.context.setBusy(true);
  assert.equal(h.els['nav-files'].disabled, true);
  assert.equal(h.els['nav-tasks'].disabled, false, 'read-only Tasks remains available during work');
  assert.equal(h.els['nav-tools'].disabled, true);
  h.context.sessionEnded = true; h.context.setBusy(false);
  assert.equal(h.els.send.disabled, true, 'busy transition cannot re-enable send after session end');
  assert.equal(h.els['nav-tasks'].disabled, true, 'ended sessions do not re-enable task queries');
  h.context.sessionEnded = false;

  h.context.renderAvailability({backend:{configured:true,state:'available'}, resources:{files:'forbidden',tasks:'available',tools:'not_implemented'}});
  assert.equal(h.els['nav-files'].disabled, true);
  assert.equal(h.els['nav-tasks'].disabled, false);
  assert.equal(h.els['nav-tools'].disabled, true);

  const configuredProjection = {backend:{configured:true,state:'unavailable',connectivity:'failed',model_readiness:'unverified',reason:'MODEL_CONNECTION_ERROR',generation:4},resources:{files:'available',tasks:'available',tools:'available'}};
  h.context.renderAvailability(configuredProjection, 20);
  h.context.renderAvailability({backend:{configured:true,state:'available',connectivity:'verified',model_readiness:'listed',reason:'CATALOG_REACHABLE'},resources:{files:'available',tasks:'available',tools:'available'}}, 19);
  assert.equal(h.context.sessionInfo.availability.backend.reason, 'MODEL_CONNECTION_ERROR', 'stale availability responses cannot replace a newer projection');
  assert.equal(h.els['model-availability'].textContent.includes('Falha de conex'), true, 'transport failure is described without implying catalog success');
  assert.match(h.els['model-availability'].title, /MODEL_CONNECTION_ERROR/, 'raw reason remains available in the accessible details');
  assert.match(h.els['model-availability'].attributes['aria-label'], /MODEL_CONNECTION_ERROR/, 'raw reason is also part of the accessible button description');

  h.context.renderAvailability({backend:{state:'not_implemented',reason:'CONNECTION_TEST_UNAVAILABLE'}}, 21);
  assert.equal(h.els['nav-files'].dataset.available, 'yes', 'incomplete bridge replies preserve the last known local resource state');
  assert.equal(h.els['model-availability'].dataset.configured, 'yes', 'omitted configured is unknown and keeps the last explicit setting');
  assert.equal(h.els['model-availability'].disabled, true, 'an unavailable test method is not presented as a usable probe');
  assert.match(h.els['model-availability'].title, /CONNECTION_TEST_UNAVAILABLE/);

  h.context.renderAvailability({backend:{configured:true,state:'available'},resources:{files:'forbidden',tasks:'available',tools:'available'}}, 22);
  h.context.renderAvailability({backend:{state:'not_implemented',reason:'CONNECTION_TEST_UNAVAILABLE'}}, 23);
  assert.equal(h.els['nav-files'].dataset.available, 'no');
  assert.equal(h.els['nav-files'].disabled, true, 'an incomplete reply cannot re-enable a resource with a known denial');

  const verifiedDenied = {backend:{configured:true,state:'available',connectivity:'verified',model_readiness:'listed',reason:'CATALOG_REACHABLE',generation:5},
    resources:{files:'forbidden',tasks:'available',tools:'available'}};
  h.context.availabilityRequestId = 30;
  h.context.renderAvailability(verifiedDenied, 30);
  const session = {workspace_id:'w1',workspace_name:'Workspace',mode:'READ_ONLY',commands:[],inspection_kinds:['files','tasks','tools'],
    profile:{name:'local_8gb',provider:'openai_compatible',model:'example'},availability:{backend:{configured:true,state:'unverified',reason:'NOT_TESTED'},resources:{files:'available',tasks:'available',tools:'available'}}};
  h.context.renderSessionInfo(session, 29);
  assert.match(h.els['profile-menu'].title, /local_8gb.*openai_compatible.*example/, 'session tooltip retains model profile details');
  assert.equal(h.context.sessionInfo.availability.backend.model_readiness, 'listed', 'stale session refresh cannot replace newer availability metadata');
  assert.match(h.els['model-availability'].textContent, /Catálogo respondeu/, 'stale session refresh cannot replace the newer toolbar status');
  assert.match(h.els['model-availability'].title, /Modelo encontrado no catálogo; geração não testada/, 'latest readiness details remain visible');
  assert.equal(h.els['nav-files'].disabled, true, 'stale inspection metadata cannot re-enable a resource with a newer denial');

  h.context.renderSessionInfo({...session,mode:undefined,availability:undefined}, 29);
  assert.match(h.els['session-mode-label'].textContent, /Modo não informado/, 'missing mode is labeled unknown rather than inferred as read-only');
  assert.match(h.els['model-availability'].textContent, /Catálogo respondeu/, 'missing stale availability preserves the latest projection');

  const artifacts = h.context.showArtifacts(); await Promise.resolve();
  h.context.showPanel('tasks'); h.outputs()({state:'available',items:[{title:'stale'}]}); await artifacts;
  assert.equal(h.els['report-body'].children.length, 0, 'late outputs cannot overwrite a newer panel');

  h.context.panelMode = 'files'; h.context.panelGeneration++;
  h.els['query-kind'].value = 'list_files'; h.els['query-path'].value = '.';
  const query = h.context.runWorkspaceQuery(); await Promise.resolve();
  h.context.showPanel('tasks'); h.query()({accepted:true,reference:{token:'stale'}}); await query;
  assert.equal(h.context.panelMode, 'tasks', 'late query cannot reclaim panel ownership');
  assert.equal(h.context.busy, false, 'query releases its busy state');

  h.setTaskFailure(null);
  await h.context.openTasksPanel();
  assert.equal(h.els['task-current-summary'].textContent, 'running', 'successful task query updates the visible summary');
  h.setTaskFailure(new Error('status service unavailable'));
  const failedTaskOpen = h.context.openTasksPanel();
  assert.equal(h.els['task-current-summary'].textContent, 'Consultando estado da execução…', 'opening Tasks immediately replaces stale summary with loading state');
  await failedTaskOpen;
  assert.match(h.els['task-current-summary'].textContent, /Não foi possível consultar o estado da execução.*status service unavailable/, 'failed refresh replaces old visible summary with actionable error');
  assert.match(h.els['task-current-state'].textContent, /Estado indisponível: Error: status service unavailable/, 'technical error remains available in the details');
  let rejectLateTasks;
  h.setTaskFailure(null);
  h.setTaskPromise(new Promise((resolve, reject) => { rejectLateTasks = reject; }));
  const lateTaskFailure = h.context.openTasksPanel();
  h.context.showPanel('files');
  h.els['task-current-summary'].textContent = 'Resumo do painel atual';
  rejectLateTasks(new Error('late status failure'));
  await lateTaskFailure;
  assert.equal(h.els['task-current-summary'].textContent, 'Resumo do painel atual', 'late task failure cannot overwrite a newer panel');
  h.setTaskPromise(new Promise((resolve, reject) => { rejectLateTasks = reject; }));
  const outdatedTaskOpen = h.context.openTasksPanel();
  await Promise.resolve();
  await h.context.loadTasks();
  assert.equal(h.els['task-current-summary'].textContent, 'running', 'newer task refresh updates the summary in the same panel');
  rejectLateTasks(new Error('outdated task failure'));
  await outdatedTaskOpen;
  assert.equal(h.els['task-current-summary'].textContent, 'running', 'older failed panel open cannot replace a newer same-panel refresh');
  assert.match(h.els['task-current-state'].textContent, /\"state\": \"running\"/, 'older failure cannot replace the latest technical snapshot either');

  let finishProbe;
  let probeCalls = 0;
  h.api.test_connection = () => { probeCalls++; return new Promise(resolve => { finishProbe = resolve; }); };
  const probeButton = h.els['model-availability'];
  const firstProbe = probeButton.listeners.click();
  await Promise.resolve();
  const secondProbe = probeButton.listeners.click();
  assert.equal(probeCalls, 1, 'a probe in flight cannot be started a second time');
  h.context.renderAvailability({backend:{configured:true,state:'unverified',reason:'REFRESH_DURING_PROBE'},resources:{files:'available',tasks:'available',tools:'available'}}, ++h.context.availabilityRequestId);
  assert.equal(probeButton.disabled, true, 'a concurrent refresh cannot re-enable the probe button');
  finishProbe({backend:{configured:true,state:'available',connectivity:'verified',model_readiness:'listed',reason:'CATALOG_REACHABLE'},resources:{files:'available',tasks:'available',tools:'available'}});
  await Promise.all([firstProbe, secondProbe]);
  assert.equal(probeButton.disabled, false, 'the completed probe and its final snapshot restore the button');

  h.els['session-dialog'].open = true;
  h.api.availability = () => Promise.reject(new Error('snapshot unavailable'));
  h.api.test_connection = () => Promise.resolve({backend:{configured:true,state:'available',connectivity:'verified',model_readiness:'listed',reason:'CATALOG_REACHABLE'},resources:{files:'available',tasks:'available',tools:'available'}});
  await probeButton.listeners.click();
  assert.equal(probeButton.disabled, false, 'failed refresh keeps the completed probe result actionable');
  assert.match(h.context.toastMessages.at(-1), /resultado conhecido foi mantido/);
  assert.ok(h.context.sessionSummaryRenders > 0, 'an open Session dialog refreshes from the latest projection');

  h.api.availability = undefined;
  h.api.test_connection = () => Promise.resolve({backend:{configured:true,state:'unverified',reason:'CATALOG_INCONCLUSIVE'}});
  await probeButton.listeners.click();
  assert.equal(probeButton.disabled, false, 'missing refresh API restores availability from the latest known projection');
  console.log('Workbench availability and panel ownership races: PASS');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
