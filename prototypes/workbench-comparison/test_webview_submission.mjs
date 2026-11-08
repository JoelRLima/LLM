import fs from 'node:fs/promises';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = await fs.readFile(new URL('../../src/llm_agent/interfaces/workbench/static/workbench.js', import.meta.url), 'utf8');
const html = await fs.readFile(new URL('../../src/llm_agent/interfaces/workbench/static/index.html', import.meta.url), 'utf8');

function fixture() {
  class Element {
    constructor() {
      this.textContent = ''; this.value = ''; this.dataset = {}; this.style = {};
      this.hidden = false; this.disabled = false; this.children = []; this.className = '';
      this.classList = {add(){}, remove(){}, toggle(){}};
    }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; this.textContent = ''; }
    querySelectorAll() { return []; }
    querySelector() { return this.children.find(x => x.className === 'bubble'); }
    addEventListener() {}
    remove() {}
  }
  const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element()]));
  const pending = [];
  const context = vm.createContext({
    document: {
      getElementById(id) { assert.ok(elements[id], `Unknown DOM id ${id}`); return elements[id]; },
      createElement() { return new Element(); }, querySelector() { return null; }, documentElement: new Element(),
    },
    window: {addEventListener(){}, pywebview: {api: {
      submit(text) { return new Promise((resolve, reject) => pending.push({text, resolve, reject})); },
    }}},
    setTimeout() {}, CSS: {escape: value => value}, console,
  });
  const run = code => vm.runInContext(code, context);
  run(source);
  const terminal = (id, type = 'settled', reference = null) => {
    context.event = {
      type, request_id: id, message: `bridge error ${id}`, reference,
      observation: {status: 'succeeded', answer: `answer ${id}`},
    };
    run('consume([event])');
  };
  const idle = () => {
    assert.equal(run('busy'), false);
    assert.equal(run('activeRequestId'), null);
    assert.equal(elements.send.disabled, false);
    assert.equal(elements.cancel.hidden, true);
  };
  return {elements, pending, run, terminal, idle};
}

const ref = token => ({
  token, title: `Report ${token}`, workspace_id: 'workspace', run_id: `run-${token}`,
  artifact_id: `artifact-${token}`, kind: 'report', completeness: 'final',
});
const cases = [];

{
  const f = fixture();
  const submit = f.run('dispatchSubmission("fast settlement")');
  f.terminal('request-1', 'settled', ref('A'));
  f.idle();
  f.pending[0].resolve({accepted: true, request_id: 'request-1'});
  await submit;
  f.idle();
  assert.equal(f.elements['run-status'].textContent, 'Relatório disponível');
  cases.push('settlement before accepted acknowledgement remains terminal and keeps report status');
}

{
  const f = fixture();
  const submit = f.run('dispatchSubmission("fast error")');
  f.terminal('request-1', 'error');
  f.idle();
  f.pending[0].resolve({accepted: true, request_id: 'request-1'});
  await submit;
  f.idle();
  assert.equal(f.elements['run-status'].textContent, 'Falha na execução');
  cases.push('error before accepted acknowledgement cannot restore busy state');
}

{
  const f = fixture();
  const refused = f.run('dispatchSubmission("refused")');
  f.pending[0].resolve({accepted: false, message: 'recusada pelo bridge'});
  await refused;
  f.idle();
  assert.match(f.elements.messages.children.at(-1).querySelector('.bubble').textContent, /recusada pelo bridge/);
  const next = f.run('dispatchSubmission("after refusal")');
  f.pending[1].resolve({accepted: true, request_id: 'request-2'});
  await next;
  assert.equal(f.run('activeRequestId'), 'request-2');
  f.terminal('request-2');
  f.idle();
  cases.push('refused submission releases the UI for a subsequent execution');
}

{
  const f = fixture();
  const first = f.run('dispatchSubmission("first fast run")');
  f.terminal('request-1', 'settled', ref('A'));
  const second = f.run('dispatchSubmission("second fast run")');
  assert.equal(f.run('submissionGeneration'), 2);
  f.pending[1].resolve({accepted: true, request_id: 'request-2'});
  await second;
  f.pending[0].resolve({accepted: true, request_id: 'request-1'});
  await first;
  assert.equal(f.run('busy'), true);
  assert.equal(f.run('activeRequestId'), 'request-2');
  f.terminal('request-2', 'settled', ref('B'));
  f.idle();
  assert.equal(f.run('latestRef.token'), 'B');
  assert.equal(f.elements['run-status'].textContent, 'Relatório disponível');
  cases.push('two consecutive runs ignore the first run’s late acknowledgement');
}

export const results = {passed: cases.length, cases};
console.log(JSON.stringify(results, null, 2));
