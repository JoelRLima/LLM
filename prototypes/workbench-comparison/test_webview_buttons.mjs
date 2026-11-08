import fs from 'node:fs/promises';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const base = '../../src/llm_agent/interfaces/workbench/static/';
const html = await fs.readFile(new URL(`${base}index.html`, import.meta.url), 'utf8');
const source = await fs.readFile(new URL(`${base}workbench.js`, import.meta.url), 'utf8');

class Element {
  constructor() {
    this.textContent = ''; this.value = ''; this.dataset = {}; this.style = {};
    this.hidden = false; this.disabled = false; this.children = []; this.listeners = {}; this.className = '';
    const classes = new Set();
    this.classList = {
      add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value),
      toggle(value) { if (classes.has(value)) classes.delete(value); else classes.add(value); },
    };
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; this.textContent = ''; }
  querySelectorAll() { return []; }
  querySelector(selector) { return selector === '.bubble' ? this.children.find(item => item.className === 'bubble') : null; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  focus() { this.focused = true; }
  remove() {}
}

const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element()]));
const buttons = [...html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].map(match => {
  const id = match[1].match(/\bid="([^"]+)"/)?.[1];
  assert.ok(id, `Button without stable identity: ${match[2]}`);
  elements[id].disabled = /\bdisabled\b/.test(match[1]);
  return {id, title: match[1].match(/\btitle="([^"]+)"/)?.[1]};
});
const root = new Element(); root.dataset.theme = 'dark';
const submitted = [], inspected = [], cancelled = [], reads = [], copied = [], queries = [];
const refs = new Map();
let nextRequest = 0;
let nextQuery = 0;
const availability = {
  backend: {state:'unverified', configured:true, connectivity:'unverified', model_readiness:'unverified', reason:'NOT_TESTED'},
  resources: {files:'available', tasks:'available', tools:'available'},
};
const api = {
  async initialize() {
    return {workspace_name:'configured-workspace', workspace_id:'workspace-1', mode:'READ_ONLY',
      profile:{name:'local',provider:'openai_compatible',model:'offline-test'},
      availability, inspection_kinds:['files','tasks','tools']};
  },
  async availability() { return availability; },
  async test_connection() { return availability; },
  async submit(text) { submitted.push(text); return {accepted:true,request_id:`request-${++nextRequest}`}; },
  async inspect(kind) { inspected.push(kind); return {accepted:true,request_id:`request-${++nextRequest}`}; },
  async cancel(id) { cancelled.push(id); return {accepted:true}; },
  async query_workspace(kind) {
    queries.push(kind);
    const token = `query-${++nextQuery}`;
    const reference = {token,title:'Workspace query',source:'ReadOnlyWorkspaceQueryService',workspace_id:'workspace-1',
      run_id:null,artifact_id:`projection-${nextQuery}`,kind:'text',completeness:'unknown',operation:{query:kind}};
    refs.set(token, reference);
    return {accepted:true,reference,expired:[]};
  },
  async read_report(token) {
    reads.push(token);
    return {token,reference:refs.get(token),text:'file notes.txt\n',renderer:'text',sha256:null,truncated:false,next_offset:null};
  },
  async tasks() { return {current:{mode:'READ_ONLY',controller_state:'idle'},runs:[],history_state:'available',historical_reports_state:'not available'}; },
  async task_status() { return {mode:'READ_ONLY',controller_state:'idle'}; },
  async outputs() { return {state:'available',items:[],expired:[]}; },
  async wait_events() { return []; },
};
const context = vm.createContext({
  document: {
    getElementById(id) { assert.ok(elements[id], `Unknown DOM id ${id}`); return elements[id]; },
    createElement() { return new Element(); }, querySelector() { return null; }, documentElement: root,
  },
  window: {addEventListener(){}, pywebview:{api}},
  navigator: {clipboard:{async writeText(text) { copied.push(text); }}},
  CSS: {escape: value => value}, setTimeout(){}, console,
});
const run = code => vm.runInContext(code, context);
run(source);
context.info = await api.initialize();
context.projection = await api.availability();
run('renderSessionInfo(info)');
run('renderAvailability(projection)');

const actionable = buttons.filter(button => typeof elements[button.id].listeners.click === 'function');
for (const button of buttons.filter(button => !elements[button.id].disabled && !elements[button.id].hidden)) {
  assert.equal(typeof elements[button.id].listeners.click, 'function', `${button.id} is enabled without an action`);
}
for (const button of buttons.filter(button => elements[button.id].disabled && !actionable.some(item => item.id === button.id))) {
  assert.ok(button.title || elements[button.id].getAttribute?.('aria-label'), `${button.id} is disabled without an explanation`);
}
const click = async id => {
  assert.equal(elements[id].disabled, false, `Cannot click disabled ${id}`);
  assert.equal(typeof elements[id].listeners.click, 'function', `${id} has no action`);
  await elements[id].listeners.click();
};

await click('collapse-nav');
await click('nav-chat');
assert.equal(elements.prompt.focused, true);
await click('theme-toggle');
assert.equal(root.dataset.theme, 'light');
await click('shortcuts');
assert.match(elements.toast.textContent, /Ctrl\+Enter/);
await click('model-availability');
await click('nav-files');
assert.deepEqual(queries, ['list_files']);
assert.deepEqual(reads, ['query-1']);
assert.equal(elements['copy-report'].disabled, false);
await click('copy-report');
assert.deepEqual(copied, ['file notes.txt\n']);
await click('run-workspace-query');
await click('close-panel');
assert.equal(elements.inspector.hidden, true);
await click('nav-tasks');
await click('refresh-tasks');
assert.match(elements['task-current-state'].textContent, /idle/);
await click('nav-tools');
assert.deepEqual(inspected, ['tools']);
run(`consume([{type:'settled',request_id:'request-1',observation:{status:'succeeded',answer:'inventory'}}])`);
await click('artifact-link');
await click('refresh-artifacts');
elements.prompt.value = 'request from UI';
await click('send');
assert.deepEqual(submitted, ['request from UI']);
assert.equal(elements.cancel.hidden, false);
await click('cancel');
assert.deepEqual(cancelled, ['request-2']);
run(`consume([{type:'settled',request_id:'request-2',observation:{status:'failed',reason_code:'INTERACTION_CANCELLED',answer:'cancelled'}}])`);
await click('suggestion');
assert.equal(submitted.length, 2);

const unavailable = buttons.filter(button => !actionable.some(item => item.id === button.id));
assert.ok(unavailable.length > 0);
assert.ok(unavailable.every(button => elements[button.id].disabled && button.title));
export const results = {
  buttons: buttons.length,
  actionHandlers: actionable.map(button => button.id),
  explicitlyUnavailable: unavailable.map(button => ({id:button.id, explanation:button.title})),
  exercised: ['files', 'tasks', 'tools', 'outputs', 'query', 'chat', 'cancel', 'connection-test', 'copy'],
};
console.log(JSON.stringify(results, null, 2));
