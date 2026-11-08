const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function makeElement(tag = 'div') {
  const element = {
    tag, children: [], listeners: {}, dataset: {}, style: {}, classList: {
      add() {}, remove() {}, toggle() {},
    }, textContent: '', value: '', placeholder: '', hidden: false, disabled: false,
    append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    setAttribute(name, value) { this.attributes ||= {}; this.attributes[name] = value; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    querySelector(selector) {
      const className = selector.startsWith('.') ? selector.slice(1) : null;
      return this.children.find(child => child.id === selector.slice(1)
        || (className && child.className === className)) || null;
    },
    querySelectorAll() { return []; },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); },
    focus() {}, click() { this.listeners.click?.({target: this}); },
    scrollTop: 0, scrollHeight: 0,
  };
  return element;
}

async function run(source) {
  new vm.Script(source);
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) { const item = makeElement(); item.id = id; elements.set(id, item); }
    return elements.get(id);
  };
  const apiCalls = [];
  let attentionResponse = null;
  let resolveGate;
  let closeCount = 0;
  let submitImpl = async () => ({accepted: true, request_id: 'idle-request'});
  let enqueueImpl = async () => ({accepted: true, observation: {status: 'succeeded', answer: 'queued'}});
  let controlImpl = async text => ({accepted: true, observation: {status: 'succeeded', answer: 'controlled', session_exit: text === '/exit'}});
  const api = {
    submit: async text => { apiCalls.push(['submit', text]); return submitImpl(text); },
    control: async text => { apiCalls.push(['control', text]); return controlImpl(text); },
    enqueue: async text => { apiCalls.push(['enqueue', text]); return enqueueImpl(text); },
    resolve_attention: async (...args) => { apiCalls.push(['resolve_attention', ...args]); await new Promise(resolve => { resolveGate = resolve; }); return {accepted: true}; },
    read_report: async token => { apiCalls.push(['read_report', token]); return {token,reference:{token,title:'Partial artifact',workspace_id:'workspace-a',run_id:null,artifact_id:'artifact-a',kind:'report'},text:'read-only partial report',renderer:'text',truncated:false,next_offset:null,sha256:null}; },
    attention: async () => attentionResponse,
    close: async () => { closeCount += 1; },
  };
  const document = {
    createElement: makeElement,
    getElementById: get,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    documentElement: {dataset: {}},
  };
  const window = {addEventListener() {}, pywebview: {api}};
  const context = vm.createContext({
    document, window, navigator: {clipboard: {writeText: async () => {}}},
    CSS: {escape: value => value}, setTimeout: () => 0, clearTimeout() {},
    console, Promise, String, Boolean, Array, Object, Math, Error,
  });
  const conversationCalls = [];
  context.captureConversationState = () => { conversationCalls.push('capture'); return {following:false}; };
  context.completeConversationMutation = snapshot => { conversationCalls.push(['complete', snapshot]); };
  context.followConversation = () => { conversationCalls.push('follow'); };
  context.testConversationCalls = () => conversationCalls;
  context.testSetAttention = value => { attentionResponse = value; };
  context.testSetSubmit = value => { submitImpl = value; };
  context.testSetEnqueue = value => { enqueueImpl = value; };
  context.testSetControl = value => { controlImpl = value; };
  context.testApiCalls = () => apiCalls;
  context.testResolveGate = () => resolveGate();
  context.testCloseCount = () => closeCount;
  vm.runInContext(source, context);
  const call = (code) => vm.runInContext(code, context);
  call(`commandEntries = [
    {action_id:'run.cancel', command:'/cancel', aliases:['/stop'], local_control:true, title:'Cancel'},
    {action_id:'run.pending_send', command:'/pending send', aliases:[], local_control:true, title:'Send pending'},
    {action_id:'run.pending_edit', command:'/pending edit', aliases:[], local_control:true, title:'Edit pending'},
    {action_id:'run.status', command:'/status', aliases:[], local_control:true, title:'Status', description:'Show current status'},
    {action_id:'session.exit', command:'/exit', aliases:[], local_control:true, title:'Exit'},
  ];`);

  call('openCommandPalette()');
  assert.equal(get('command-palette-backdrop').hidden, false);
  get('command-filter').value = 'status';
  call('renderCommandPalette()');
  const option = get('command-list').children[0];
  assert.ok(option);
  option.listeners.click();
  assert.equal(get('command-palette-backdrop').hidden, true);
  assert.equal(get('prompt').value, '/status');
  assert.equal(context.testApiCalls().length, 0, 'selecting a command only drafts it');

  context.sessionForDraft = {workspace_id:'workspace-a'};
  call('sessionInfo = sessionForDraft;');
  get('prompt').value = 'unsent user draft A';
  get('prompt').placeholder = 'original hint';
  call('openCommandPalette()');
  get('command-filter').value = 'status';
  call('renderCommandPalette()');
  get('command-list').children[0].listeners.click();
  assert.equal(get('prompt').value, '/status');
  assert.equal(get('restore-draft').hidden, false, 'displacing a user draft exposes explicit recovery');
  call('openCommandPalette()');
  get('command-filter').value = 'exit';
  call('renderCommandPalette()');
  get('command-list').children[0].listeners.click();
  assert.equal(call("displacedDrafts.get('id:workspace-a').drafts.length"), 1, 'replacing an untouched generated command preserves the original draft slot');
  const commandBeforeCancel = get('prompt').value;
  call('openCommandPalette(); closeCommandPalette();');
  assert.equal(get('prompt').value, commandBeforeCancel, 'cancelling the palette leaves the current draft unchanged');
  get('restore-draft').click();
  assert.equal(get('prompt').value, 'unsent user draft A');
  assert.equal(get('prompt').placeholder, 'original hint', 'recovery restores text and placeholder');
  get('prompt').value = 'newer user draft B';
  get('restore-draft').click();
  assert.equal(get('prompt').value, '/exit', 'recovery swaps while preserving a newer draft');
  get('restore-draft').click();
  assert.equal(get('prompt').value, 'newer user draft B', 'repeated recovery toggles without losing the draft typed afterward');
  get('prompt').value = 'x'.repeat(16_001);
  get('restore-draft').click();
  assert.equal(get('prompt').value.length, 16_001, 'oversized current text is never truncated or discarded during recovery');
  assert.match(get('toast').textContent, /limite de troca/);
  get('prompt').value = 'newer user draft B';
  get('restore-draft').click();
  assert.equal(get('prompt').value, '/exit', 'oversized swap rejection retains the displaced draft for later recovery');

  call(`renderSessionInfo({workspace_id:'workspace-b',workspace_name:'B',mode:'READ_ONLY',commands:[],inspection_kinds:[],availability:{backend:{configured:false},resources:{}}}, availabilityRequestId + 1);`);
  assert.equal(call("displacedDrafts.has('id:workspace-a')"), false, 'workspace changes clear recovered text from the prior workspace');
  call('sessionInfo = {};');
  call(`commandEntries = [
    {action_id:'run.cancel', command:'/cancel', aliases:['/stop'], local_control:true, title:'Cancel'},
    {action_id:'run.pending_send', command:'/pending send', aliases:[], local_control:true, title:'Send pending'},
    {action_id:'run.pending_edit', command:'/pending edit', aliases:[], local_control:true, title:'Edit pending'},
    {action_id:'run.status', command:'/status', aliases:[], local_control:true, title:'Status', description:'Show current status'},
    {action_id:'session.exit', command:'/exit', aliases:[], local_control:true, title:'Exit'},
  ];`);
  get('prompt').value = 'draft without workspace metadata';
  call('openCommandPalette()');
  get('command-filter').value = 'status';
  call('renderCommandPalette()');
  get('command-list').children[0].listeners.click();
  assert.equal(get('prompt').value, 'draft without workspace metadata', 'missing workspace metadata never causes a draft to be overwritten');

  assert.equal(call("isLocalControl('/stop')"), true);
  assert.equal(call("isLocalControl('/pending send 1')"), false);
  assert.equal(call("isLocalControl('/pending edit 1')"), true, 'catalogued controls route through control');
  call("busy = true; initialized = true; activeRequestId = 'run-1';");
  await call("submitWhileBusy('/cancel')");
  await call("submitWhileBusy('follow up')");
  await call("submitWhileBusy('/pending send 1')");
  assert.deepEqual(context.testApiCalls().slice(0, 3), [
    ['control', '/cancel'], ['enqueue', 'follow up'], ['enqueue', '/pending send 1'],
  ]);

  call('busy = false; sessionEnded = false;');
  context.testSetSubmit(async () => ({accepted: false, message: 'rejected'}));
  get('prompt').value = '  exact idle draft  ';
  await call('submit()');
  assert.equal(get('prompt').value, '  exact idle draft  ', 'idle rejection restores exact submitted text');
  assert.ok(context.testConversationCalls().includes('follow'), 'an explicit user send intentionally follows the conversation');
  assert.ok(context.testConversationCalls().some(item => Array.isArray(item) && item[0] === 'complete'), 'message mutations use the viewport snapshot contract');

  let releaseSubmit;
  context.testSetSubmit(() => new Promise(resolve => { releaseSubmit = resolve; }));
  get('prompt').value = 'old idle draft';
  const idleRace = call('submit()');
  await Promise.resolve();
  get('prompt').value = 'newer user draft';
  releaseSubmit({accepted: false, message: 'late rejection'});
  await idleRace;
  assert.equal(get('prompt').value, 'newer user draft', 'late idle rejection preserves newer typing');

  call('busy = true; activeRequestId = "run-1";');
  context.testSetEnqueue(async () => ({accepted: false, message: 'queue full'}));
  get('prompt').value = '  exact busy draft  ';
  await call('submit()');
  assert.equal(get('prompt').value, '  exact busy draft  ', 'busy enqueue rejection restores exact submitted text');

  let releaseEnqueue;
  context.testSetEnqueue(() => new Promise(resolve => { releaseEnqueue = resolve; }));
  get('prompt').value = 'old busy draft';
  const busyRace = call('submit()');
  await Promise.resolve();
  get('prompt').value = 'newer busy typing';
  releaseEnqueue({accepted: false, message: 'late queue rejection'});
  await busyRace;
  assert.equal(get('prompt').value, 'newer busy typing', 'late enqueue rejection preserves newer typing');

  context.testSetSubmit(() => new Promise(resolve => { releaseSubmit = resolve; }));
  call('busy = false; sessionEnded = false;');
  get('prompt').value = 'draft before late submit rejection';
  const draftRejectRace = call('submit()');
  await Promise.resolve();
  get('prompt').value = 'new draft after submit';
  releaseSubmit({accepted: false, message: 'late rejection'});
  await draftRejectRace;
  assert.equal(get('prompt').value, 'new draft after submit', 'async rejection cannot clobber the current recovered or typed draft');

  call(`busy = true; activeRequestId = 'failed-with-report'; currentSubmission = null;`);
  call(`consume([{type:'settled',request_id:'failed-with-report',observation:{status:'failed',reason_code:'MODEL_CONNECTION_ERROR',answer:'partial result'},reference:{token:'partial',kind:'report',title:'Partial report',workspace_id:'workspace-a',run_id:'run-a',artifact_id:'artifact-a'}}]);`);
  const failedEvent = get('messages').children.find(item => item.className === 'event' && item.children[1]?.textContent === 'Execu\u00e7\u00e3o n\u00e3o conclu\u00edda');
  assert.equal(failedEvent.dataset.severity, 'error', 'failed executions cannot use the success event state');
  assert.equal(failedEvent.children[0].textContent.charCodeAt(0), 0x00d7, 'failed event uses an error glyph, never a success check');
  assert.notEqual(failedEvent.children[0].textContent, '✓');
  assert.match(get('run-status').textContent, /Execu\u00e7\u00e3o n\u00e3o conclu\u00edda.*relat\u00f3rio dispon\u00edvel/, 'a partial report does not turn a failure into success');

  call(`busy = true; activeRequestId = 'cancelled-with-report'; currentSubmission = null;`);
  call(`consume([{type:'settled',request_id:'cancelled-with-report',observation:{status:'cancelled',reason_code:'INTERACTION_CANCELLED',answer:'partial result'},reference:{token:'partial-cancel',kind:'report',title:'Partial report',workspace_id:'workspace-a',run_id:'run-a',artifact_id:'artifact-cancel'}}]);`);
  const cancelledEvent = get('messages').children.find(item => item.className === 'event' && item.children[1]?.textContent === 'Execu\u00e7\u00e3o cancelada');
  assert.equal(cancelledEvent.dataset.severity, 'warning', 'cancelled executions are not shown with a success check');
  assert.equal(cancelledEvent.children[0].textContent, '!', 'cancelled event uses a warning glyph');
  assert.notEqual(cancelledEvent.children[0].textContent, '✓');
  assert.match(get('run-status').textContent, /Execu\u00e7\u00e3o cancelada/);

  call(`busy = true; activeRequestId = 'terminal-partial'; currentSubmission = null;`);
  call(`streamDelta({request_id:'terminal-partial',text:'streamed partial text'});`);
  const callsBeforeTerminalError = context.testApiCalls().length;
  call(`consume([{type:'error',request_id:'terminal-partial',message:'terminal projection failed',settlement:{observation:{status:'failed',reason_code:'RAW_TERMINAL_REASON',answer:'observed partial answer',summary:'observed partial summary',session_exit:true},reference:{token:'partial-token',title:'Partial artifact',workspace_id:'workspace-a',run_id:null,artifact_id:'artifact-a',kind:'report'}}}]);`);
  assert.equal(get('run-status').textContent, 'Falha na execu\u00e7\u00e3o \u00b7 resultado parcial dispon\u00edvel');
  assert.equal(get('artifact-link').disabled, false, 'a structurally valid partial reference remains available');
  assert.equal(call('latestRef.token'), 'partial-token');
  const visibleMessages = get('messages').children.filter(item => item.className?.startsWith('message ')).flatMap(item => item.children).map(item => item.textContent).join(' ');
  assert.match(visibleMessages, /observed partial answer/);
  assert.match(visibleMessages, /observed partial summary/);
  assert.match(visibleMessages, /streamed partial text/, 'previous streaming text is retained');
  assert.match(visibleMessages, /terminal projection failed/);
  assert.equal(get('messages').children.some(item => item.className?.includes('artifact-offer') && item.textContent.includes('resultado parcial')), true);
  const terminalEvent = get('messages').children.find(item => item.className === 'event' && item.dataset.severity === 'error'
    && item.children.some(child => child.textContent.includes('RAW_TERMINAL_REASON')));
  assert.ok(terminalEvent, 'raw terminal reason code stays in the failure details');
  assert.equal(context.testApiCalls().length, callsBeforeTerminalError, 'terminal errors do not retry or call another service');
  assert.equal(context.testCloseCount(), 0, 'session_exit metadata on an error event does not close the session');
  assert.equal(call('streamedRequestId'), null, 'finished errored request releases its streaming identity');
  assert.equal(context.testApiCalls().some(item => item[0] === 'read_report'), false, 'partial references are not opened automatically');
  get('messages').children.find(item => item.className?.includes('artifact-offer') && item.dataset.artifactToken === 'partial-token').listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(context.testApiCalls().some(item => item[0] === 'read_report' && item[1] === 'partial-token'), true, 'a nullable run_id reference still opens its report on explicit click');

  const offersBeforeMalformed = get('messages').children.filter(item => item.className?.includes('artifact-offer')).length;
  call(`busy = true; activeRequestId = 'malformed-terminal'; currentSubmission = null;`);
  call(`consume([{type:'error',request_id:'malformed-terminal',message:'malformed settlement',settlement:{observation:'invalid',reference:{token:'bad'}}}]);`);
  assert.equal(get('run-status').textContent, 'Falha na execu\u00e7\u00e3o', 'malformed settlement degrades to the terminal error only');
  assert.equal(get('messages').children.filter(item => item.className?.includes('artifact-offer')).length, offersBeforeMalformed, 'invalid reference metadata is never offered');

  call(`busy = true; activeRequestId = 'partial-without-reference'; currentSubmission = null;`);
  call(`consume([{type:'error',request_id:'partial-without-reference',message:'projection failed',settlement:{observation:{status:'failed',reason_code:'RAW_PARTIAL',answer:'partial but no report'}}}]);`);
  assert.equal(get('run-status').textContent, 'Falha na execu\u00e7\u00e3o');
  assert.equal(get('messages').children.some(item => item.className === 'event' && item.dataset.severity === 'warning'
    && item.children.some(child => child.textContent.includes('Resultado parcial preservado'))), true, 'partial text without a report is still labeled incomplete');

  call(`busy = true; activeRequestId = 'still-live'; currentSubmission = null;`);
  const beforeStaleError = get('messages').children.length;
  call(`consume([{type:'error',request_id:'old-terminal',message:'stale',settlement:{observation:{answer:'must not render'},reference:{token:'old'}}}]);`);
  assert.equal(get('messages').children.length, beforeStaleError, 'stale terminal errors remain ignored');
  assert.equal(call('busy'), true, 'a stale terminal cannot release the current request');
  call('setBusy(false);');

  context.testSetControl(async () => ({accepted: false, message: 'control rejected'}));
  get('prompt').value = 'busy control draft';
  await call("submitWhileBusy('/cancel', $('prompt').value)");
  assert.equal(get('prompt').value, 'busy control draft', 'control rejection restores exact draft');

  call(`busy = true; activeRequestId = 'pending-edit'; currentSubmission = null;`);
  get('prompt').value = '';
  call(`consume([{type:'settled', request_id:'pending-edit', observation:{status:'succeeded', answer:'loaded', draft:'pending edit draft'}, reference:null}]);`);
  assert.equal(get('prompt').value, 'pending edit draft', 'pending edit settlement loads its draft');
  call(`busy = true; activeRequestId = 'pending-edit-newer'; currentSubmission = null;`);
  get('prompt').value = 'new text while pending edit settles';
  call(`consume([{type:'settled', request_id:'pending-edit-newer', observation:{status:'succeeded', answer:'loaded', draft:'stale pending edit draft'}, reference:null}]);`);
  assert.equal(get('prompt').value, 'new text while pending edit settles', 'pending edit does not overwrite newer typing');

  const first = {attention_id: 1, run_generation: 2, request_fingerprint: 'fingerprint-1', action: 'write', resource: 'a.txt', prompt: 'Allow?', metadata: {proposed_diff: '+ok'}};
  context.testSetAttention(first);
  context.firstAttention = first;
  call('renderAttention(firstAttention)');
  assert.equal(get('approval-card').hidden, false);
  assert.equal(get('approval-diff').textContent, '+ok');
  const pendingResolution = call("resolveAttention('approve')");
  await Promise.resolve();
  await call("resolveAttention('deny')");
  assert.equal(context.testApiCalls().filter(item => item[0] === 'resolve_attention').length, 1, 'double click is suppressed');
  const second = {...first, attention_id: 2, run_generation: 3, request_fingerprint: 'fingerprint-2'};
  context.testSetAttention(second);
  context.secondAttention = second;
  call('renderAttention(secondAttention)');
  context.testResolveGate();
  await pendingResolution;
  assert.equal(context.testApiCalls().find(item => item[0] === 'resolve_attention')[1], 1);
  assert.equal(context.testApiCalls().find(item => item[0] === 'resolve_attention')[2], 'approve');
  assert.equal(get('approval-card').hidden, false);
  assert.equal(call('approvalIdentity.attention_id'), 2, 'late response does not replace the newer attention');

  call(`busy = true; activeRequestId = 'exit-run'; currentSubmission = null;`);
  await call("submitWhileBusy('/exit')");
  assert.equal(context.testCloseCount(), 1);
  assert.equal(get('prompt').disabled, true);
  assert.equal(get('send').disabled, true);
  assert.equal(get('run-status').textContent, 'Sessão encerrada');
  assert.equal(call('sessionEnded'), true);

  call(`sessionEnded = false; busy = true; initialized = true; activeRequestId = 'idle-exit'; currentSubmission = null;`);
  call(`consume([{type:'settled', request_id:'idle-exit', observation:{status:'succeeded', answer:'bye', session_exit:true}, reference:null}]);`);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(context.testCloseCount(), 2, 'settled idle /exit closes the session');
  assert.equal(get('prompt').disabled, true);
  console.log('Workbench command palette, busy controls, approval races and both exit paths: PASS');
}

if (require.main === module) {
  const source = fs.readFileSync(process.argv[2], 'utf8');
  run(source).catch(error => { console.error(error); process.exitCode = 1; });
}

module.exports = {run};
