const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function run(source) {
  const elements = new Map();
  const make = () => ({hidden:false, disabled:false, textContent:'', listeners:{},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    async click() { return this.listeners.click?.(); },
    showModal() { this.open = true; }, close() { this.open = false; }});
  const get = id => { if (!elements.has(id)) elements.set(id, make()); return elements.get(id); };
  const events = {};
  const intervals = [];
  let status = {state:'managed', available:false, restart_required:false, version:'1', revision:'abc', message:'Atualizado'};
  let calls = 0;
  let resolveUpdate;
  let resolveStatus;
  let holdStatus = false;
  let statusCalls = 0;
  let resolveRestart;
  let holdRestart = false;
  let restartCalls = 0;
  const api = {update_status:async () => { statusCalls++; return holdStatus ? new Promise(resolve => { resolveStatus = resolve; }) : status; },
    apply_update:async () => { calls++; return new Promise(resolve => { resolveUpdate = resolve; }); },
    restart_workbench:async () => { restartCalls++; return holdRestart
      ? new Promise(resolve => { resolveRestart = resolve; }) : {accepted:false, message:'Tarefa ativa'}; }};
  const document = {hidden:false, getElementById:get, createElement:make,
    querySelector:() => ({prepend(button) { elements.set(button.id, button); }})};
  const window = {pywebview:{api}, addEventListener(name, callback) { events[name] = callback; }};
  vm.runInNewContext(source, {document, window, setInterval(callback) { intervals.push(callback); }, Date});
  await events.pywebviewready();
  assert.equal(get('workbench-updates').textContent, 'Atualizações');
  assert.equal(get('updates-apply').hidden, true);
  status = {...status, available:true};
  await get('workbench-updates').click();
  assert.equal(get('updates-dialog').open, true);
  assert.equal(get('workbench-updates').textContent, 'Atualização disponível');
  assert.equal(get('updates-apply').hidden, false);
  const pending = get('updates-apply').click();
  await get('updates-apply').click();
  assert.equal(calls, 1, 'Duplicate install must be blocked');
  assert.equal(get('updates-check').disabled, true);
  resolveUpdate({accepted:false, message:'Falha no build <arquivo>'});
  await pending;
  assert.match(get('updates-message').textContent, /Falha no build <arquivo>/);
  assert.equal(get('updates-check').disabled, false);
  const success = get('updates-apply').click();
  resolveUpdate({...status, available:false, accepted:true, restart_required:true});
  await success;
  assert.equal(get('updates-restart').hidden, false);
  assert.equal(get('updates-apply').hidden, true);
  await get('updates-restart').click();
  assert.match(get('updates-message').textContent, /Tarefa ativa/);
  assert.equal(get('updates-restart').disabled, false);
  assert.equal(get('updates-check').disabled, false, 'Rejected restart restores check control');
  assert.equal(get('updates-apply').disabled, false, 'Rejected restart restores apply control');
  await get('updates-close').click();
  assert.equal(get('updates-dialog').open, false);
  status = {state:'unmanaged', available:false, restart_required:false, message:'Use o gerenciador'};
  await get('updates-check').click();
  assert.equal(get('updates-apply').hidden, true);
  status = {...status, state:'managed', available:true};
  holdStatus = true;
  const refresh = get('updates-check').click();
  assert.equal(get('updates-check').disabled, true);
  assert.equal(get('updates-apply').disabled, true);
  const beforeOverlappingApply = calls;
  await get('updates-apply').click();
  assert.equal(calls, beforeOverlappingApply, 'Apply must not overlap a pending status refresh');
  resolveStatus(status);
  await refresh;
  assert.equal(get('updates-apply').disabled, false);
  holdStatus = false;
  status = {...status, available:false, restart_required:true};
  await get('updates-check').click();
  const beforeRestartRefresh = statusCalls;
  const beforeRestart = restartCalls;
  holdRestart = true;
  const restartPending = get('updates-restart').click();
  await get('updates-restart').click();
  await get('updates-apply').click();
  await get('updates-check').click();
  events.focus();
  intervals[0]();
  assert.equal(restartCalls, beforeRestart + 1, 'Duplicate restart and overlapping actions must be blocked');
  assert.equal(calls, beforeOverlappingApply, 'Apply must remain blocked during restart');
  assert.equal(statusCalls, beforeRestartRefresh, 'Focus refresh must not overlap restart');
  assert.equal(get('updates-check').disabled, true);
  assert.equal(get('updates-apply').disabled, true);
  assert.equal(get('updates-restart').disabled, true);
  resolveRestart({accepted:false, message:'Tarefa ativa'});
  await restartPending;
  assert.match(get('updates-message').textContent, /Tarefa ativa/);
  assert.equal(get('updates-check').disabled, false);
  assert.equal(get('updates-apply').disabled, false);
  assert.equal(get('updates-restart').disabled, false);
  return 'update panel: detection, errors, duplicate admission, restart and unmanaged state passed';
}

module.exports = {run};
if (require.main === module) {
  run(fs.readFileSync(process.argv[2], 'utf8')).then(result => console.log(result)).catch(error => { console.error(error); process.exitCode = 1; });
}
