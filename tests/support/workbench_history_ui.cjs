const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

let source = '';
let section = '';

function element(tag = 'div') {
  return {
    tag, children: [], listeners: {}, textContent: '', hidden: false, disabled: false,
    append(child) { child.parent = this; this.children.push(child); },
    replaceChildren(...children) { this.children = []; children.forEach(child => this.append(child)); },
    querySelector(selector) { return this.children.find(child => child.id === selector.slice(1)) || null; },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); },
    addEventListener(event, handler) { this.listeners[event] = handler; },
  };
}

function harness(tasks) {
  const elements = Object.fromEntries(['task-runs', 'task-run-detail', 'task-history-note'].map(id => [id, element()]));
  const context = vm.createContext({
    $: id => elements[id], document: {createElement: element},
    api: () => ({tasks, task_details: async id => ({run_id: id})}),
    panelMode: 'tasks', MAX_STREAM_CHARS: 24000, renderTaskStatus: () => {},
  });
  vm.runInContext(section, context);
  return {context, elements};
}

const run = id => ({run_id: id, status: 'succeeded', completeness: 'final'});
const page = (runs, nextOffset = null) => ({
  current: {}, runs, next_offset: nextOffset, history_state: 'available', historical_reports_state: 'available',
});

async function runHistoryUi(workbenchSource) {
  source = workbenchSource;
  new vm.Script(source);
  section = source.slice(source.indexOf('let taskHistoryOffset ='), source.indexOf('async function openTasksPanel()'));
  const calls = [];
  const view = harness(async offset => {
    calls.push(offset);
    return offset === 0 ? page(Array.from({length: 50}, (_, index) => run(`run-${index}`)), 50)
      : page([run('run-49'), run('run-50')]);
  });
  await view.context.loadTasks();
  const more = view.elements['task-runs'].querySelector('#load-older-tasks');
  assert.ok(more);
  assert.equal(view.elements['task-runs'].children.length, 51);
  more.listeners.click();
  assert.equal(more.disabled, true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(view.elements['task-runs'].children.length, 51);
  assert.equal(view.elements['task-runs'].querySelector('#load-older-tasks'), null);
  assert.deepEqual(calls, [0, 50]);

  let resolveOld;
  let resets = 0;
  const race = harness(async offset => offset ? await new Promise(resolve => { resolveOld = resolve; })
    : page([run(++resets === 1 ? 'old' : 'new')], 50));
  await race.context.loadTasks();
  const oldRequest = race.context.loadTasks(true);
  await race.context.loadTasks();
  resolveOld(page([run('stale')]));
  await oldRequest;
  const text = race.elements['task-runs'].children.map(child => child.textContent).join('|');
  assert.ok(text.includes('new'));
  assert.ok(!text.includes('stale'));

  const detailRequests = new Map();
  const detailRace = harness(async () => page([run('detail-a'), run('detail-b')]));
  // Replace only the bridge detail endpoint so each selected row can settle independently.
  const originalApi = detailRace.context.api;
  detailRace.context.api = () => ({
    ...originalApi(),
    task_details: id => new Promise(resolve => detailRequests.set(id, resolve)),
  });
  await detailRace.context.loadTasks();
  const rowA = detailRace.elements['task-runs'].children[0];
  const rowB = detailRace.elements['task-runs'].children[1];
  rowA.listeners.click();
  rowB.listeners.click();
  detailRequests.get('detail-b')({run_id:'detail-b', detail:'newer selection'});
  await new Promise(resolve => setImmediate(resolve));
  detailRequests.get('detail-a')({run_id:'detail-a', detail:'older selection'});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(detailRace.elements['task-run-detail'].textContent, /detail-b/);
  assert.doesNotMatch(detailRace.elements['task-run-detail'].textContent, /detail-a/,
    'late detail response for an earlier row cannot replace the most recent selection');
  return 'Workbench history pagination, deduplication and stale selection responses: PASS';
}

module.exports = {run:runHistoryUi};

if (require.main === module) {
  runHistoryUi(fs.readFileSync(process.argv[2], 'utf8'))
    .then(result => console.log(result))
    .catch(error => { console.error(error); process.exitCode = 1; });
}
