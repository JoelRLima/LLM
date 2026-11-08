const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function makeElement(tag = 'div', id = '') {
  const classes = new Set();
  return {
    tag, id, children: [], listeners: {}, dataset: {}, style: {}, attributes: {}, className: '',
    textContent: '', value: '', placeholder: '', title: '', disabled: false, hidden: false, isConnected: false,
    append(...items) { for (const item of items) { item.parent = this; item.isConnected = true; this.children.push(item); } },
    prepend(item) { item.parent = this; this.children.unshift(item); },
    showModal() { this.open = true; }, close() { this.open = false; this.listeners.close?.({target:this}); },
    replaceChildren(...items) { this.children = []; this.append(...items); this.textContent = ''; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    removeAttribute(name) { delete this.attributes[name]; },
    getAttribute(name) { return this.attributes[name] ?? null; },
    focus() { this.focused = true; }, select() { this.selected = true; },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(item => item !== this); this.isConnected = false; this.removed = true; },
    getBoundingClientRect() { return this.rect || {top:0, bottom:0}; },
    click(event = {}) { return this.listeners.click?.({target: this, ...event}); },
    querySelector(selector) {
      if (selector.startsWith('.')) return this.children.find(item => item.className === selector.slice(1)) || null;
      return this.children.find(item => item.id === selector.slice(1)) || null;
    },
    querySelectorAll(selector) {
      const classNames = selector.split(',').map(value => value.trim().replace(/^\./, ''));
      const result = [];
      const visit = item => { for (const child of item.children) { if (classNames.includes(child.className)) result.push(child); visit(child); } };
      visit(this); return result;
    },
    classList: {
      add(value) { classes.add(value); }, remove(value) { classes.delete(value); }, contains(value) { return classes.has(value); },
      toggle(value) { if (classes.has(value)) { classes.delete(value); return false; } classes.add(value); return true; },
    },
  };
}

function pressDialogEscape(element) {
  const event = {defaultPrevented:false, preventDefault() { this.defaultPrevented = true; }};
  element.listeners.cancel?.(event);
  if (!event.defaultPrevented) element.close();
}

async function run(source, controls, html, queueSource = '') {
  assert.match(html, /Escreva abaixo o que você precisa/);
  assert.match(html, /placeholder="Descreva o que você quer fazer/);
  assert.match(html, /<b>Ver arquivos do projeto<\/b>/, 'the welcome shortcut describes its Files action');
  const elements = Object.create(null);
  const documentIds = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(documentIds).size, documentIds.length, 'DOM ids must be unique; duplicate ids can bind actions to text instead of buttons');
  for (const match of html.matchAll(/<([a-z][a-z0-9-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)) {
    const [, tag, attrs, id] = match;
    const element = elements[id] ||= makeElement(tag, id);
    element.hidden = /\bhidden(?:\s|=|$)/i.test(attrs);
    element.disabled = /\bdisabled(?:\s|=|$)/i.test(attrs);
    element.value = attrs.match(/\bvalue="([^"]*)"/)?.[1] || '';
    element.placeholder = attrs.match(/\bplaceholder="([^"]*)"/)?.[1] || '';
    element.className = attrs.match(/\bclass="([^"]*)"/)?.[1] || '';
    for (const [, name, value] of attrs.matchAll(/\b(aria-[\w-]+)="([^"]*)"/g)) element.attributes[name] = value;
  }
  const staticButtons = [...html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/gi)].map(match => {
    const id = match[1].match(/\bid="([^"]+)"/)?.[1];
    assert.ok(id, `Static button lacks a stable id: ${match[2]}`);
    const element = elements[id] ||= makeElement('button', id);
    element.disabled = /\bdisabled(?:\s|=|$)/i.test(match[1]);
    element.hidden = /\bhidden(?:\s|=|$)/i.test(match[1]);
    element.title = match[1].match(/\btitle="([^"]*)"/)?.[1] || '';
    const pressed = match[1].match(/\baria-pressed="([^"]*)"/)?.[1];
    if (pressed !== undefined) element.attributes['aria-pressed'] = pressed;
    return {id, element};
  });
  const get = id => elements[id] ||= makeElement('div', id);
  const root = makeElement('html'); root.dataset.theme = 'dark';
  const body = makeElement('body');
  const createdElements = [];
  let copySucceeded = true;
  let clipboardWrites = [];
  const apiCalls = [];
  let connectionProbes = 0;
  const api = {
    submit: async text => { apiCalls.push(['submit', text]); return {accepted:true, request_id:'sent'}; },
    inspect: async kind => { apiCalls.push(['inspect', kind]); return {accepted:true, request_id:'inspect'}; },
    query_workspace: async (kind, args) => { apiCalls.push(['query_workspace', kind, args]); return {accepted:true, reference:{token:'ref', title:'Report', source:'test', workspace_id:'workspace-1', run_id:null, artifact_id:'a', kind:'text', completeness:'final', operation:{query:kind}}}; },
    read_report: async token => ({token, reference:{workspace_id:'workspace-1', run_id:null, artifact_id:'a'}, text:'file text', renderer:'text', truncated:false}),
    outputs: async () => ({state:'available', items:[]}),
    tasks: async () => ({current:{controller_state:'idle'}, runs:[], next_offset:null}),
    task_status: async () => ({controller_state:'idle'}),
    attention: async () => ({state:'none'}),
    wait_events: async () => [],
    test_connection: async () => { connectionProbes += 1; return {backend:{configured:true,state:'available'},resources:{files:'available',tasks:'available',tools:'available'}}; },
    update_status: async () => ({state:'managed',available:false,restart_required:false,version:'1',revision:'abc',message:'Atualizado'}),
    cancel: async id => ({accepted:true, request_id:id}),
  };
  const document = {
    body, documentElement:root,
    getElementById:get,
    createElement:tag => { const item = makeElement(tag); createdElements.push(item); return item; },
    querySelector:selector => selector.startsWith('#') ? get(selector.slice(1)) : selector === '.top-actions' ? get('top-actions') : null,
    querySelectorAll:selector => selector === '#pending-queue-items button'
      ? get('pending-queue-items').children.flatMap(row => row.children.filter(item => item.tag === 'button'))
      : body.querySelectorAll(selector),
    addEventListener(name, fn) { document.listeners ||= {}; document.listeners[name] = fn; },
    execCommand(command) { assert.equal(command, 'copy'); return copySucceeded; },
  };
  const window = {pywebview:{api}, addEventListener(name, fn) { window.listeners ||= {}; window.listeners[name] = fn; }};
  const navigator = {clipboard:{async writeText(text) { clipboardWrites.push(text); throw new Error('clipboard denied'); }}};
  const context = vm.createContext({document, window, navigator, CSS:{escape:value => value},
    setTimeout:() => 1, setInterval:() => 1, clearTimeout(){}, console, Promise, String, Boolean, Array, Object, Math, Error});
  vm.runInContext(source, context, {filename:'workbench.js'});
  vm.runInContext(controls, context, {filename:'workbench-controls.js'});
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../../src/llm_agent/interfaces/workbench/static/workbench-tests.js'), 'utf8'), context, {filename:'workbench-tests.js'});
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../../src/llm_agent/interfaces/workbench/static/workbench-plugins.js'), 'utf8'), context, {filename:'workbench-plugins.js'});
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../../src/llm_agent/interfaces/workbench/static/workbench-navigation.js'), 'utf8'), context, {filename:'workbench-navigation.js'});
  if (queueSource) vm.runInContext(queueSource, context, {filename:'workbench-queue.js'});
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../../src/llm_agent/interfaces/workbench/static/workbench-updates.js'), 'utf8'), context, {filename:'workbench-updates.js'});

  // Every static button, including temporarily disabled ones, owns a click action.
  for (const {id, element} of staticButtons) {
    assert.equal(typeof element.listeners.click, 'function', `${id} has no click action`);
  }
  assert.equal(get('more-resources-panel').hidden, true, 'secondary navigation starts collapsed');
  assert.equal(get('more-resources-toggle').getAttribute('aria-expanded'), 'false');
  for (const id of ['nav-chat', 'nav-files', 'nav-tasks', 'workspace-switch', 'workspace-name', 'profile-menu', 'session-open', 'session-mode-label', 'approval-card', 'cancel']) {
    assert.ok(elements[id], `${id} remains directly available outside the disclosure`);
  }
  await get('more-resources-toggle').click();
  assert.equal(get('more-resources-panel').hidden, false, 'secondary controls are reachable when expanded');
  assert.equal(get('more-resources-toggle').getAttribute('aria-expanded'), 'true');
  for (const id of ['nav-tools', 'artifact-link', 'refresh-artifacts']) assert.ok(elements[id], `${id} remains available in the expanded section`);
  await get('more-resources-toggle').click();
  assert.equal(get('more-resources-panel').hidden, true, 'the disclosure can be collapsed again');
  assert.equal(get('more-resources-toggle').getAttribute('aria-expanded'), 'false');

  // Follow the tail only when the reader was already there; preserve older position and offer an explicit jump.
  const conversation = get('conversation');
  const messageHost = get('messages');
  assert.equal(vm.runInContext('captureConversationState().following', context), true, 'unknown initial geometry defaults to following');
  assert.equal(get('scroll-latest').getAttribute('aria-label'), 'Rolar para a mensagem mais recente');
  const appendMessageRows = messageHost.append.bind(messageHost);
  messageHost.append = (...items) => { appendMessageRows(...items); conversation.scrollHeight += 80 * items.length; };
  conversation.clientHeight = 300;
  conversation.scrollHeight = 1000;
  conversation.scrollTop = 700;
  conversation.listeners.scroll();
  assert.equal(get('scroll-latest').hidden, true, 'the button stays hidden while pinned to the newest message');
  vm.runInContext("message('agent', 'new message while pinned')", context);
  assert.equal(conversation.scrollTop, 780, 'a pinned reader follows appended messages');
  assert.equal(get('scroll-latest').hidden, true);

  conversation.scrollTop = 180;
  conversation.listeners.scroll();
  assert.equal(get('scroll-latest').hidden, false, 'scrolling up reveals an explicit return button');
  conversation.scrollHeight += 60;
  vm.runInContext("streamDelta({request_id:'reader-test',text:'next streamed delta'})", context);
  assert.equal(conversation.scrollTop, 180, 'streamed deltas keep the reader position');
  assert.equal(get('scroll-latest').hidden, false);
  assert.equal(conversation.focused, undefined, 'stream updates do not steal keyboard focus');

  conversation.scrollTop = conversation.scrollHeight - conversation.clientHeight;
  conversation.listeners.scroll();
  assert.equal(get('scroll-latest').hidden, true, 'returning to the bottom resumes follow mode');
  vm.runInContext("eventRow('new event while pinned')", context);
  assert.equal(conversation.scrollTop, conversation.scrollHeight - conversation.clientHeight, 'events follow while pinned');

  conversation.rect = {top:100, bottom:400};
  const anchor = makeElement('div'); anchor.rect = {top:150, bottom:180}; anchor.parent = messageHost; anchor.isConnected = true;
  messageHost.children = [anchor];
  conversation.scrollHeight = 1500; conversation.scrollTop = 230;
  conversation.listeners.scroll();
  vm.runInContext(`{ const beforeTrim = captureConversationState();
    document.getElementById('messages').children[0].rect = {top:90,bottom:120};
    document.getElementById('conversation').scrollHeight = 1450;
    completeConversationMutation(beforeTrim); }`, context);
  assert.equal(conversation.scrollTop, 170, 'trimming rows above preserves the visible anchor offset');
  assert.equal(get('scroll-latest').hidden, false);

  const sendsBeforeJump = apiCalls.filter(call => call[0] === 'submit').length;
  const probesBeforeJump = connectionProbes;
  await get('scroll-latest').click();
  assert.equal(conversation.scrollTop, conversation.scrollHeight - conversation.clientHeight);
  assert.equal(get('scroll-latest').hidden, true);
  assert.equal(conversation.focused, true, 'jump moves keyboard focus to the conversation container');
  assert.equal(apiCalls.filter(call => call[0] === 'submit').length, sendsBeforeJump, 'jump does not submit');
  assert.equal(connectionProbes, probesBeforeJump, 'jump does not make a network request');

  vm.runInContext(`initialized = true; busy = false; sessionEnded = false; sessionInfo = {workspace_id:'workspace-1'}; commandEntries = [
    {action_id:'model.select', command:'/model', title:'Select model'},
    {action_id:'model.show', command:'/model show', title:'Show model'},
    {action_id:'workspace.switch', command:'/workspace switch', title:'Switch workspace'},
    {action_id:'configuration.mode', command:'/mode', title:'Change mode'},
    {action_id:'discovery.help', command:'/help', title:'Help'},
    {action_id:'interaction.agent_submit', command:'/run', title:'Run agent'},
  ];`, context);
  assert.equal(get('run-status').attributes['aria-live'], 'polite', 'Run state is announced to assistive technology');

  // Current Tasks summary stays observational, distinguishes states, and only announces changed text.
  const callsBeforeTaskSummary = apiCalls.length;
  const taskProbesBeforeSummary = connectionProbes;
  assert.match(html, /id="task-current-summary" role="status" aria-live="polite"/);
  assert.equal(get('task-current-summary').getAttribute('aria-live'), 'polite');
  vm.runInContext(`panelMode = 'tasks'; renderTaskStatus({
    state:'WAITING_ATTENTION', controller_state:'WAITING_ATTENTION', current_tool:'Review change', current_step:'Confirm the patch',
    last_activity_at:'2026-11-01T12:30:00Z', attention_pending:true, pending_count:2, warning_count:1, error_count:0, terminal_outcome:null
  })`, context);
  const activeTaskSummary = get('task-current-summary').textContent;
  assert.match(activeTaskSummary, /Execução aguardando sua atenção/);
  assert.match(activeTaskSummary, /Ferramenta: Review change/);
  assert.match(activeTaskSummary, /Etapa: Confirm the patch/);
  assert.match(activeTaskSummary, /Última atividade reportada: 2026-11-01T12:30:00Z/);
  assert.match(activeTaskSummary, /Atenção pendente/);
  assert.match(activeTaskSummary, /Na fila: 2.*nenhum é enviado automaticamente/);
  assert.match(activeTaskSummary, /Avisos: 1 · erros: 0/);
  const summaryElement = get('task-current-summary');
  let taskSummaryWrites = 0;
  let taskSummaryText = summaryElement.textContent;
  Object.defineProperty(summaryElement, 'textContent', {
    configurable:true,
    get() { return taskSummaryText; },
    set(value) { taskSummaryWrites += 1; taskSummaryText = value; },
  });
  vm.runInContext(`renderTaskStatus({state:'WAITING_ATTENTION', controller_state:'WAITING_ATTENTION', current_tool:'Review change', current_step:'Confirm the patch',
    last_activity_at:'2026-11-01T12:30:00Z', attention_pending:true, pending_count:2, warning_count:1, error_count:0, terminal_outcome:null})`, context);
  assert.equal(taskSummaryWrites, 0, 'unchanged task polls do not re-announce the live summary');

  vm.runInContext(`renderTaskStatus({state:'RUNNING', controller_state:'CANCELLING', attention_pending:false, pending_count:0})`, context);
  assert.match(get('task-current-summary').textContent, /Cancelamento solicitado; aguardando término/);
  assert.doesNotMatch(get('task-current-summary').textContent, /Cancelamento reportado/);
  vm.runInContext(`renderTaskStatus({state:'TERMINAL', controller_state:'IDLE', terminal_outcome:'cancelled', attention_pending:false, pending_count:0})`, context);
  assert.match(get('task-current-summary').textContent, /Cancelamento reportado pela execução: cancelled/);
  vm.runInContext(`renderTaskStatus({state:'IDLE', terminal_outcome:'failed', attention_pending:false, pending_count:0, warning_count:0, error_count:1})`, context);
  assert.match(get('task-current-summary').textContent, /Nenhuma execução ativa/);
  assert.match(get('task-current-summary').textContent, /Falha reportada pela execução: failed/);
  vm.runInContext(`renderTaskStatus({state:'UNKNOWN_STATE', current_tool:'<img src=x onerror=alert(1)>', terminal_outcome:'succeeded', model_active:false})`, context);
  assert.match(get('task-current-summary').textContent, /Estado não informado/);
  assert.match(get('task-current-summary').textContent, /Estado da atenção não informado/);
  assert.match(get('task-current-summary').textContent, /Contagem da fila não informada/);
  assert.match(get('task-current-summary').textContent, /<img src=x onerror=alert\(1\)>/);
  assert.match(get('task-current-summary').textContent, /Resultado reportado pela execução: succeeded.*não confirma inferência do modelo/);
  assert.match(get('task-current-state').textContent, /UNKNOWN_STATE/, 'the technical disclosure retains the raw projected state');
  assert.equal(apiCalls.filter(call => call[0] === 'submit').length, 0, 'task summary never submits queued work');
  assert.equal(apiCalls.length, callsBeforeTaskSummary, 'rendering the summary does not call the bridge');
  assert.equal(connectionProbes, taskProbesBeforeSummary, 'rendering the summary does not probe connectivity');

  // Session summary uses only projected allowlisted values and does not probe or submit.
  vm.runInContext(`sessionInfo = {
    workspace_name:'Demo', workspace_root:'/workspace/Demo', workspace_id:'workspace-1', mode:'EDITOR', execution_mode:'configured_endpoint',
    profile:{name:'local_profile',provider:'OpenAI-compatible',model:'gpt-test',api_key:'secret-profile-key'},
    availability:{backend:{configured:true,state:'unverified',connectivity:'unverified',model_readiness:'unverified',reason:'CATALOG_INCONCLUSIVE',generation:17,api_url:'https://private.example/key-secret'},
      resources:{model:'unverified',files:'available',tasks:'available',tools:'available',approval:'available',attachments:'not_implemented',private:'secret-resource'}},
    commands:[{action_id:'private.command',payload:'secret-command'}]
  }`, context);
  const probesBeforeSessionDialog = connectionProbes;
  const submitsBeforeSessionDialog = apiCalls.filter(call => call[0] === 'submit').length;
  await get('session-open').click();
  assert.equal(get('session-dialog').open, true);
  assert.equal(get('session-workspace-name').textContent, 'Demo');
  assert.equal(get('session-workspace-root').textContent, '/workspace/Demo');
  assert.equal(get('session-profile-name').textContent, 'local_profile');
  assert.equal(get('session-profile-description').textContent, 'OpenAI-compatible · gpt-test');
  assert.equal(get('session-mode-value').textContent, 'Editor');
  assert.equal(get('session-connectivity').textContent, 'Não verificada');
  assert.equal(get('session-inference').textContent, 'Inferência não verificada');
  assert.equal(get('session-tech-configured').textContent, 'Sim (true)');
  assert.equal(get('session-tech-readiness').textContent, 'Não verificado (unverified)');
  assert.equal(get('session-tech-reason').textContent, 'CATALOG_INCONCLUSIVE');
  assert.equal(get('session-tech-generation').textContent, '17');
  const renderedSessionText = Object.values(elements).map(item => item.textContent).join('\n');
  for (const secret of ['secret-profile-key', 'key-secret', 'secret-resource', 'secret-command']) assert.equal(renderedSessionText.includes(secret), false, `${secret} is excluded from the session summary`);
  assert.equal(connectionProbes, probesBeforeSessionDialog, 'opening the summary does not probe connectivity');
  assert.equal(apiCalls.filter(call => call[0] === 'submit').length, submitsBeforeSessionDialog, 'opening the summary does not submit');
  await get('session-close').click();
  assert.equal(get('session-dialog').open, false);
  assert.equal(get('session-open').focused, true, 'closing the summary restores focus to its opener');

  vm.runInContext(`sessionInfo.mode = 'READ_ONLY';`, context);
  await get('session-open').click();
  assert.equal(get('session-mode-value').textContent, 'Somente leitura');
  await get('session-close').click();
  vm.runInContext(`sessionInfo.mode = 'FULL';`, context);
  await get('session-open').click();
  assert.equal(get('session-mode-value').textContent, 'Full');
  assert.match(get('session-mode-note').textContent, /cada mudança continua sujeita às permissões, aprovações e limites do projeto/);
  await get('session-close').click();

  vm.runInContext(`sessionInfo.availability.backend = {configured:true,state:'available',connectivity:'verified',model_readiness:'listed'};`, context);
  await get('session-open').click();
  assert.equal(get('session-connectivity').textContent, 'Catálogo acessível; isso não verifica geração');
  assert.equal(get('session-inference').textContent, 'Modelo listado no catálogo; geração não verificada');
  pressDialogEscape(get('session-dialog'));
  assert.equal(get('session-open').focused, true);

  vm.runInContext(`sessionInfo = {mode:'UNKNOWN',profile:null,availability:{backend:{configured:false,state:'not_configured',connectivity:'unverified',model_readiness:'unverified',reason:'CONFIGURATION_MISSING'}}}`, context);
  await get('session-open').click();
  assert.equal(get('session-mode-value').textContent, 'Desconhecido');
  assert.equal(get('session-profile-name').textContent, 'Não configurado');
  assert.equal(get('session-tech-configured').textContent, 'Não (false)');
  assert.equal(get('session-connectivity').textContent, 'Modelo não configurado');
  assert.equal(get('session-inference').textContent, 'Modelo não configurado');
  await get('session-close').click();

  vm.runInContext(`sessionInfo = {mode:'UNKNOWN',availability:{backend:{state:'custom_state',connectivity:'custom_connectivity',model_readiness:'custom_readiness'}}}`, context);
  await get('session-open').click();
  assert.equal(get('session-mode-value').textContent, 'Desconhecido');
  assert.equal(get('session-tech-configured').textContent, 'Desconhecido', 'missing configured metadata is not treated as false');
  assert.equal(get('session-connectivity').textContent, 'Desconhecida');
  assert.equal(get('session-inference').textContent, 'Inferência não verificada');
  assert.equal(get('session-tech-backend-state').textContent, 'Desconhecido (custom_state)');
  assert.equal(get('session-tech-connectivity').textContent, 'Desconhecido (custom_connectivity)');
  assert.equal(get('session-tech-readiness').textContent, 'Desconhecido (custom_readiness)');
  await get('session-close').click();

  vm.runInContext(`sessionInfo = {workspace_name:'Demo',workspace_root:'/workspace/Demo',workspace_id:'workspace-1',mode:'EDITOR',profile:{name:'local_profile',provider:'OpenAI-compatible',model:'gpt-test'},availability:{backend:{configured:true,state:'unverified',connectivity:'unverified',model_readiness:'unverified'},resources:{model:'unverified'}}}`, context);
  get('prompt').value = 'Draft with IME text';
  const beforeImeEnter = apiCalls.filter(call => call[0] === 'submit').length;
  let compositionPrevented = false;
  get('prompt').listeners.keydown({key:'Enter', ctrlKey:true, isComposing:true, preventDefault() { compositionPrevented = true; }});
  get('prompt').listeners.keydown({key:'Enter', metaKey:true, keyCode:229, preventDefault() { compositionPrevented = true; }});
  assert.equal(compositionPrevented, false, 'Composition Enter must remain available to the IME');
  assert.equal(apiCalls.filter(call => call[0] === 'submit').length, beforeImeEnter, 'IME Enter must not submit');
  assert.equal(get('prompt').value, 'Draft with IME text', 'IME draft remains intact');
  // Profile opens only session/profile/workspace commands.
  await get('profile-menu').click();
  assert.equal(get('command-palette-backdrop').hidden, false);
  assert.equal(get('command-palette-title').textContent, 'Sessão e perfis');
  const profileCommands = get('command-list').children.map(item => item.children[0]?.textContent);
  assert.deepEqual(profileCommands, ['/model', '/model show', '/workspace switch', '/mode']);
  await get('command-filter').listeners.keydown({key:'Escape'});
  assert.equal(get('command-palette-backdrop').hidden, true);

  // The active workspace affordance opens only project commands and does not submit.
  get('prompt').value = 'Keep this draft';
  const beforeWorkspaceChoice = apiCalls.filter(call => call[0] === 'submit').length;
  await get('workspace-switch').click();
  assert.equal(get('command-palette-title').textContent, 'Escolher projeto');
  assert.equal(get('command-palette-backdrop').hidden, false);
  assert.deepEqual(get('command-list').children.map(item => item.children[0]?.textContent), ['/workspace switch']);
  assert.equal(get('prompt').value, 'Keep this draft');
  assert.equal(apiCalls.filter(call => call[0] === 'submit').length, beforeWorkspaceChoice, 'project selection only opens a draft command');
  await get('command-filter').listeners.keydown({key:'Escape'});

  // Session actions close the modal before opening canonically filtered palettes.
  const beforeSessionActions = apiCalls.filter(call => call[0] === 'submit').length;
  await get('session-open').click();
  await get('session-profile-model').click();
  assert.equal(get('session-dialog').open, false);
  assert.equal(get('command-palette-backdrop').hidden, false);
  assert.equal(get('command-palette-title').textContent, 'Perfil e modelo');
  assert.deepEqual(get('command-list').children.map(item => item.children[0]?.textContent), ['/model', '/model show']);
  await get('command-list').children[0].click();
  assert.equal(get('prompt').value, '/model', 'choosing a command fills a draft only');
  assert.equal(apiCalls.filter(call => call[0] === 'submit').length, beforeSessionActions);

  await get('session-open').click();
  await get('session-work-mode').click();
  assert.equal(get('session-dialog').open, false);
  assert.equal(get('command-palette-title').textContent, 'Modo de trabalho');
  assert.deepEqual(get('command-list').children.map(item => item.children[0]?.textContent), ['/mode']);
  await get('command-filter').listeners.keydown({key:'Escape'});
  await get('session-open').click();
  await get('session-choose-workspace').click();
  assert.equal(get('session-dialog').open, false);
  assert.equal(get('command-palette-title').textContent, 'Escolher projeto');
  assert.deepEqual(get('command-list').children.map(item => item.children[0]?.textContent), ['/workspace switch']);
  get('session-dialog').listeners.close?.({target:get('session-dialog')});
  assert.equal(get('command-filter').focused, true, 'a delayed dialog close event cannot steal focus from the palette');
  assert.equal(apiCalls.filter(call => call[0] === 'submit').length, beforeSessionActions);
  await get('command-filter').listeners.keydown({key:'Escape'});

  get('prompt').value = 'draft before shortcut';
  const beforeSessionShortcut = apiCalls.filter(call => call[0] === 'submit').length;
  await get('session-open').click();
  let sessionShortcutPrevented = false;
  let sessionShortcutStopped = false;
  await get('session-dialog').listeners.keydown({key:'k',ctrlKey:true,
    preventDefault() { sessionShortcutPrevented = true; }, stopPropagation() { sessionShortcutStopped = true; }});
  assert.equal(sessionShortcutPrevented, true);
  assert.equal(sessionShortcutStopped, true, 'the document shortcut cannot open a second modal behind Session');
  assert.equal(get('session-dialog').open, false);
  assert.equal(get('command-palette-backdrop').hidden, false);
  assert.equal(get('command-filter').focused, true);
  assert.equal(get('prompt').value, 'draft before shortcut');
  assert.equal(apiCalls.filter(call => call[0] === 'submit').length, beforeSessionShortcut);
  await get('command-filter').listeners.keydown({key:'Escape'});
  await get('session-open').click();
  let f2ShortcutStopped = false;
  await get('session-dialog').listeners.keydown({key:'F2',preventDefault(){},stopPropagation(){f2ShortcutStopped = true;}});
  assert.equal(f2ShortcutStopped, true);
  assert.equal(get('session-dialog').open, false);
  assert.equal(get('command-palette-backdrop').hidden, false);
  await get('command-filter').listeners.keydown({key:'Escape'});

  // The plus opens Files and chooses the read form without sending anything.
  await get('attach-file').click();
  assert.equal(get('query-kind').value, 'read');
  assert.equal(get('query-path').value, '');
  assert.equal(get('query-path').focused, true);
  assert.ok(apiCalls.some(call => call[0] === 'query_workspace' && call[1] === 'list_files'), 'file chooser first loads the workspace listing');
  assert.equal(apiCalls.filter(call => call[0] === 'submit').length, 0);

  // A successful file read can be inserted as a draft; it never submits by itself.
  get('query-path').value = 'docs/guide.txt';
  await get('run-workspace-query').click();
  assert.equal(vm.runInContext('fileReference.path', context), 'docs/guide.txt');
  get('prompt').value = 'Summarize';
  get('use-file-in-chat').disabled = false;
  await get('use-file-in-chat').click();
  assert.match(get('prompt').value, /Summarize\nLeia o arquivo "docs\/guide\.txt"\./);
  assert.equal(apiCalls.filter(call => call[0] === 'submit').length, 0);
  assert.equal(get('prompt').focused, true);
  context.setFileReference('old.txt', 'workspace-1');
  vm.runInContext("sessionInfo = {workspace_id:'workspace-2'}", context);
  const staleDraft = get('prompt').value;
  await get('use-file-in-chat').click();
  assert.equal(get('prompt').value, staleDraft, 'stale workspace reference must not enter the draft');
  assert.equal(vm.runInContext('fileReference', context), null);

  // Report wrapping, navigation collapse/expand, theme, shortcuts and clipboard fallback.
  const pressedBefore = get('report-options').getAttribute('aria-pressed');
  await get('report-options').click();
  assert.notEqual(get('report-options').getAttribute('aria-pressed'), pressedBefore);
  await get('collapse-nav').click();
  assert.equal(get('collapse-nav').getAttribute('aria-expanded'), 'false');
  await get('collapse-nav').click();
  assert.equal(get('collapse-nav').getAttribute('aria-expanded'), 'true');
  await get('theme-toggle').click();
  assert.equal(root.dataset.theme, 'light');
  await get('shortcuts').click();
  assert.match(get('toast').textContent, /Ctrl\+Enter/);
  get('copy-report').disabled = false;
  get('copy-report').dataset.text = 'copy me';
  await get('copy-report').click();
  assert.deepEqual(clipboardWrites, ['copy me']);
  assert.ok(createdElements.some(item => item.className === 'clipboard-fallback' && item.removed));

  // Unsupported controls are disabled, and supported Tasks remains available during live work.
  if (queueSource) {
    const originalQueueApi = {task_status:api.task_status, control:api.control, submit:api.submit};
    context.queueFixture = {workspace_id:'queue-project',controller_state:'RUNNING',pending_count:2,
      pending_items:[{pending_id:1,text:'Primeiro pedido <script>literal</script>'},{pending_id:2,text:'Segundo pedido'}]};
    api.task_status = async () => context.queueFixture;
    let queueReject = true;
    api.control = async text => {
      apiCalls.push(['control',text]);
      if (queueReject) { queueReject=false; return {accepted:false,message:'Fila indisponível neste momento'}; }
      context.queueFixture.pending_items = context.queueFixture.pending_items.filter(item => item.pending_id !== Number(text.split(' ').at(-1)));
      return {accepted:true,observation:{status:'succeeded'}};
    };
    api.submit = async text => {
      apiCalls.push(['submit',text]);
      context.queueFixture.pending_items = context.queueFixture.pending_items.filter(item => item.pending_id !== Number(text.split(' ').at(-1)));
      return {accepted:true,request_id:'queue-sent'};
    };
    vm.runInContext(`sessionInfo={workspace_id:'queue-project'}; panelMode='tasks'; currentSubmission=null; setBusy(true,'active'); renderPendingQueue(queueFixture);`,context);
    const queueRow = get('pending-queue-items').children[0];
    assert.equal(queueRow.children[0].textContent, 'Primeiro pedido <script>literal</script>', 'pending text stays literal');
    const sendPending = queueRow.children.find(item => item.dataset.queueAction === 'send');
    assert.equal(sendPending.disabled,true);
    const callsBeforeQueue = apiCalls.length;
    await sendPending.click();
    assert.equal(apiCalls.length,callsBeforeQueue,'busy click cannot send');
    get('prompt').value='Rascunho preservado durante ações da fila';
    await get('pending-queue-items').children[1].children.find(item=>item.dataset.queueAction==='discard').click();
    assert.equal(get('pending-queue-items').children.length,2,'a rejected removal cannot hide a pending request');
    assert.match(get('toast').textContent,/Fila indisponível neste momento/);
    await get('pending-queue-items').children[1].children.find(item=>item.dataset.queueAction==='discard').click();
    assert.equal(apiCalls.at(-1)[1],'/pending discard 2');
    assert.equal(get('pending-queue-items').children.length,1);
    assert.equal(get('prompt').value,'Rascunho preservado durante ações da fila');
    context.queueFixture.controller_state='IDLE';
    vm.runInContext('setBusy(false);renderPendingQueue(queueFixture)',context);
    const sendOnce = get('pending-queue-items').children[0].children.find(item=>item.dataset.queueAction==='send');
    await sendOnce.click();
    assert.equal(apiCalls.at(-1)[1],'/pending send 1');
    const sentCount=apiCalls.length;
    await sendOnce.click();
    assert.equal(apiCalls.length,sentCount,'consumed pending row cannot submit twice');
    assert.equal(get('pending-queue-items').children.length,0);
    assert.equal(get('prompt').value,'Rascunho preservado durante ações da fila');
    vm.runInContext(`consume([{type:'settled',request_id:'queue-sent',observation:{status:'succeeded',answer:'synthetic'},reference:null}]);`,context);
    context.queueFixture.pending_items=[{pending_id:3,text:'Other pending request'}];
    vm.runInContext('renderPendingQueue(queueFixture)',context);
    const staleButton=get('pending-queue-items').children[0].children.find(item=>item.dataset.queueAction==='send');
    vm.runInContext(`sessionInfo={workspace_id:'other-project'}`,context);
    await staleButton.click();
    assert.equal(apiCalls.length,sentCount,'a stale workspace action cannot send');
    assert.equal(vm.runInContext(`validPendingQueue({workspace_id:'x',pending_items:[{pending_id:1,text:'one'},{pending_id:1,text:'duplicate'}]})`,context),false);
    vm.runInContext(`renderPendingQueue({workspace_id:'other-project',pending_items:'invalid'})`,context);
    assert.equal(get('pending-queue-items').children.length,0);
    let resolveQueue;
    api.task_status=()=>new Promise(resolve=>{resolveQueue=resolve;});
    const lateQueue=vm.runInContext('refreshPendingQueue()',context);
    vm.runInContext(`panelMode='files'`,context);
    resolveQueue(context.queueFixture);
    await lateQueue;
    assert.equal(get('pending-queue-items').children.length,0,'late refresh cannot repaint a closed queue panel');
    Object.assign(api, originalQueueApi);
  }
  vm.runInContext(`initialized = true; busy = false; sessionEnded = false; currentSubmission = null;
    sessionInfo = {workspace_id:'review-project'}; clearDraftRecovery();`, context);
  const recoveryBefore = get('messages').children.filter(item => item.className === 'request-recovery').length;
  await vm.runInContext(`dispatchSubmission('Verifique notes.txt sem alterar arquivos')`, context);
  get('prompt').value = 'Meu próximo pedido ainda em edição';
  vm.runInContext(`consume([{type:'settled',request_id:'sent',observation:{status:'failed',reason_code:'MODEL_CONNECTION_ERROR',answer:'Resultado parcial'},reference:null}]);`, context);
  const reviewCards = get('messages').children.filter(item => item.className === 'request-recovery');
  assert.equal(reviewCards.length, recoveryBefore + 1, 'one recovery card per failed request');
  const reviewButton = reviewCards.at(-1).children.find(item => item.tag === 'button');
  assert.equal(reviewButton.textContent, 'Revisar pedido');
  const callsBeforeReview = apiCalls.length;
  await reviewButton.click();
  assert.equal(get('prompt').value, 'Verifique notes.txt sem alterar arquivos');
  assert.equal(apiCalls.length, callsBeforeReview, 'review only edits the composer; no submit, enqueue or control');
  vm.runInContext('recoverDisplacedDraft()', context);
  assert.equal(get('prompt').value, 'Meu próximo pedido ainda em edição', 'the displaced draft remains recoverable');
  vm.runInContext(`sessionInfo = {workspace_id:'another-project'}`, context);
  await reviewButton.click();
  assert.equal(get('prompt').value, 'Meu próximo pedido ainda em edição');
  assert.match(get('toast').textContent, /outro projeto/);
  vm.runInContext(`sessionInfo = {workspace_id:'review-project'}; setBusy(true,'other-run')`, context);
  await reviewButton.click();
  assert.equal(get('prompt').value, 'Meu próximo pedido ainda em edição', 'busy recovery is inert even on a forced click');
  vm.runInContext('setBusy(false)', context);
  get('prompt').value = 'x'.repeat(16_001);
  await reviewButton.click();
  assert.equal(get('prompt').value.length, 16_001, 'oversized current draft is never overwritten');
  get('prompt').value = '';
  vm.runInContext(`currentSubmission = null; setBusy(true,'unverified-ux'); consume([{type:'settled',request_id:'unverified-ux',observation:{status:'unverified',reason_code:'CHECK_PENDING',answer:'Ainda não confirmado'},reference:null}]);`, context);
  assert.equal(get('run-status').textContent, 'Resultado não verificado');
  const unverifiedEvent = get('messages').children.find(item => item.className === 'event' && item.children[1]?.textContent === 'Resultado não verificado');
  assert.equal(unverifiedEvent.dataset.severity, 'warning');
  assert.match(unverifiedEvent.children[2].textContent, /status=unverified.*CHECK_PENDING/);
  assert.equal(vm.runInContext(`executionStatusLabel('toString')`, context), 'Execução encerrada');
  vm.runInContext(`currentSubmission = null; setBusy(true,'conflicting-status'); consume([{type:'settled',request_id:'conflicting-status',observation:{status:'succeeded',error:'A execução falhou'},reference:null}]);`, context);
  assert.equal(get('run-status').textContent, 'Falha na execução', 'an error cannot be presented as completed because of a conflicting status');
  vm.runInContext(`currentSubmission = null; setBusy(true,'unverified-error'); consume([{type:'settled',request_id:'unverified-error',observation:{status:'unverified',error:'Falha explícita'},reference:null}]);`, context);
  assert.equal(get('messages').children.filter(item => item.className === 'event').at(-1).dataset.severity, 'error');
  assert.equal(apiCalls.length, callsBeforeReview);

  vm.runInContext(`renderAvailability({backend:{configured:true,state:'available'},resources:{files:'available',tasks:'available',tools:'not_implemented'}})`, context);
  vm.runInContext("setBusy(true, 'request-1')", context);
  assert.equal(get('nav-files').disabled, true);
  assert.equal(get('nav-tasks').disabled, false);
  assert.equal(get('nav-tools').disabled, true, 'unsupported tools remain disabled');
  assert.equal(get('run-workspace-query').disabled, true);
  assert.equal(get('model-availability').disabled, true);
  const beforeBusyPlus = apiCalls.length;
  await get('attach-file').click();
  assert.equal(apiCalls.length, beforeBusyPlus, 'file chooser is inert while a mutating action is busy');

  return {buttons:staticButtons.map(item => item.id), exercised:['IME submission guard','draft preservation','live run status','profile scope','workspace picker scope','session summary allowlist/status','session dialog focus and action scopes','current task summary/status/outcome','conversation follow/reader preservation','explicit jump/focus/no-send','file chooser','draft reference','stale workspace','wrap','collapse-expand','secondary navigation disclosure','theme','shortcuts','copy fallback','busy availability']};
}

module.exports = {run};

if (require.main === module) {
  const [sourcePath, controlsPath, htmlPath] = process.argv.slice(2);
  run(fs.readFileSync(sourcePath, 'utf8'), fs.readFileSync(controlsPath, 'utf8'), fs.readFileSync(htmlPath, 'utf8'),
      fs.readFileSync(require('node:path').join(require('node:path').dirname(sourcePath),'workbench-queue.js'),'utf8'))
    .then(result => console.log(JSON.stringify(result, null, 2)))
    .catch(error => { console.error(error); process.exitCode = 1; });
}
