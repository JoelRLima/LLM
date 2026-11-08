import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const elements = new Map();
function element(id) { if (!elements.has(id)) elements.set(id, {textContent:'',dataset:{},style:{},disabled:false,hidden:false,addEventListener(){},replaceChildren(){},append(){},classList:{add(){},remove(){}},querySelectorAll(){return []}}); return elements.get(id); }
const pending=[];
const context=vm.createContext({document:{getElementById:element,createElement:()=>element('pre'),documentElement:element('root')},window:{addEventListener(){},pywebview:{api:{read_report:token=>new Promise((resolve,reject)=>pending.push({token,resolve,reject}))}}},setTimeout(){},navigator:{}});
vm.runInContext(fs.readFileSync(new URL('./pywebview/workbench.js',import.meta.url),'utf8'),context);
const run=s=>vm.runInContext(s,context);
run('setBusy(true,"a")');element('cancel').disabled=true;run('setBusy(false);setBusy(true,"b")');assert.equal(element('cancel').disabled,false);
run('openPanel({token:"A"});openPanel({token:"B"})');pending[1].resolve({text:'B',truncated:true,sha256:null});await new Promise(setImmediate);pending[0].resolve({text:'A'});await new Promise(setImmediate);assert.equal(element('copy-report').dataset.text,'B');
run('openPanel({token:"C"})');assert.equal(element('copy-report').disabled,true);assert.equal(element('copy-report').dataset.text,undefined);pending[2].reject(Error('read failed'));await new Promise(setImmediate);assert.equal(element('copy-report').disabled,true);
run('openPanel({token:"D"});panelGeneration++;panelRef=null');pending[3].resolve({text:'D'});await new Promise(setImmediate);assert.equal(element('copy-report').dataset.text,undefined);
assert.ok(!fs.readFileSync(new URL('./pywebview/index.html',import.meta.url),'utf8').includes('SHA-256 verificado'));
console.log('PASS: reusable cancel, A/B out of order, read failure, pending close, metadata');
