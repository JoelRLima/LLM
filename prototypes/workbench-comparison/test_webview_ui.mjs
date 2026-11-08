import fs from 'node:fs/promises';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = await fs.readFile(new URL('../../src/llm_agent/interfaces/workbench/static/workbench.js', import.meta.url), 'utf8');
const html = await fs.readFile(new URL('../../src/llm_agent/interfaces/workbench/static/index.html', import.meta.url), 'utf8');
const ids = [...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]);
class Element {
  constructor() { this.textContent = ''; this.dataset = {}; this.style = {}; this.hidden = false; this.disabled = false; this.children = []; this.listeners = {}; this.classList = {add(){}, remove(){}, toggle(){}}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; this.textContent = ''; }
  querySelectorAll() { return []; }
  querySelector() { return this.children.find(x => x.className === 'bubble'); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  remove() {}
}
const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
const pending = [];
const cancellations = [];
const sandbox = vm.createContext({
  document: {getElementById(id) { assert.ok(elements[id], `Unknown DOM id ${id}`); return elements[id]; },
    createElement() { return new Element(); }, querySelector() { return null; }, documentElement: new Element()},
  window: {addEventListener(){}, pywebview: {api: {
    read_report(token) { return new Promise((resolve, reject) => pending.push({token, resolve, reject})); },
    async cancel(id) { cancellations.push(id); return {accepted: true}; },
  }}},
  navigator: {clipboard: {async writeText(){}}}, CSS: {escape: x => x}, setTimeout(){}, console,
});
const run = code => vm.runInContext(code, sandbox);
run(source);
const flush = async () => { for (let i=0; i<8; i++) await Promise.resolve(); };
const ref = (token) => ({token, title: token, workspace_id:'w', run_id:'r', artifact_id:token, completeness:'final'});
const result = (token, extra={}) => ({token, reference:ref(token), text:`text ${token}`, renderer:'text', truncated:false, sha256:'abc', next_offset:null, ...extra});
const open = token => { sandbox.ref = ref(token); run('openPanel(ref)'); };
const body = () => elements['report-body'].children[0]?.textContent;
const copy = elements['copy-report'];
const cases = [];
function check(name, fn) { fn(); cases.push(name); }

for (const id of ['request-1', 'request-2']) {
  run(`setBusy(true, '${id}')`);
  assert.equal(elements.cancel.disabled, false);
  await run('cancelActive()');
  assert.equal(elements.cancel.disabled, true);
  sandbox.events = [{type:'settled',request_id:id,observation:{status:'failed',reason_code:'INTERACTION_CANCELLED',answer:'cancelled'}}];
  run('consume(events)');
}
check('two cancel cycles rearm Stop', () => assert.deepEqual(cancellations, ['request-1','request-2']));
open('A'); const a = pending.shift();
open('B'); const b = pending.shift();
check('switch clears copy and old content', () => { assert.equal(copy.disabled,true); assert.equal(copy.dataset.text,undefined); assert.equal(body(),'Carregando artefato…'); });
b.resolve(result('B')); await flush();
a.resolve(result('A')); await flush();
check('out of order A/B keeps B', () => { assert.equal(body(),'text B'); assert.equal(copy.dataset.text,'text B'); });
open('A'); const a2 = pending.shift(); open('B'); const b2 = pending.shift();
a2.resolve(result('A')); await flush();
check('stale success cannot enable copy while B pending', () => assert.equal(copy.disabled,true));
b2.reject(new Error('read failure')); await flush();
check('read failure clears copy', () => { assert.equal(copy.disabled,true); assert.equal(copy.dataset.text,undefined); assert.match(body(),/read failure/); });
open('A'); const a3=pending.shift(); open('B'); const b3=pending.shift();
b3.resolve(result('B')); await flush(); a3.reject(new Error('stale failure')); await flush();
check('stale failure cannot replace success', () => assert.equal(body(),'text B'));
open('A'); const closing=pending.shift(); run('closePanel()'); closing.resolve(result('A')); await flush();
check('close invalidates pending read', () => { assert.equal(elements.inspector.hidden,true); assert.equal(copy.disabled,true); assert.equal(copy.dataset.text,undefined); assert.equal(body(),undefined); });
open('A'); const old=pending.shift(); run('closePanel()'); open('A'); const fresh=pending.shift();
fresh.resolve(result('A',{text:'fresh A',truncated:true,sha256:null,next_offset:42})); await flush(); old.resolve(result('A')); await flush();
check('same artifact reopen uses generation', () => assert.equal(body(),'fresh A'));
check('truncation and absent digest independent from final execution', () => {
  assert.match(elements['report-integrity'].textContent,/truncado/);
  assert.match(elements['report-integrity'].textContent,/Digest não disponível/);
  assert.match(elements['report-integrity'].textContent,/42/);
  assert.match(elements['report-state'].textContent,/final/);
});
open('A'); pending.shift().resolve(result('B')); await flush();
check('identity mismatch fails closed', () => { assert.equal(copy.disabled,true); assert.match(body(),/divergente/); });
open('A'); pending.shift().resolve(result('A')); await flush();
check('digest never claimed verified', () => { assert.match(elements['report-integrity'].textContent,/abc/); assert.match(elements['report-integrity'].textContent,/não verificada/); assert.doesNotMatch(html,/SHA-256 verificado/); });
run(`streamDelta({request_id:'r',text:'x'.repeat(64001),truncated:true})`);
check('stream truncation observable', () => assert.equal(run('streamedBubble.dataset.truncated'),'yes'));
export const results = {passed: cases.length, cases};
console.log(JSON.stringify(results, null, 2));
