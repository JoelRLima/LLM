// Presentation only: every operation crosses the shared session/application API.
let testsSnapshot = null;
let testsEpoch = 0;
let testsRead = 0;
let testsPending = false;
let testsTimer = null;
let testsOpener = null;
let testsLoadedQueue = null;
let testsCheckedKey = null;
let testsOwnRequest = false;
let testsPageSize = 100;
let testsRowsKey = '';
let testsReportPath = '';
let testsFilterChosen = false;
const testsLabels = {ready:'Pronta',running:'Em execução',paused:'Pausada',stopped:'Parada',completed:'Finalizada',
  budget_exhausted:'Limite de tempo atingido',pending:'Pendente',verified:'Verificado',unverified:'Sem verificação',
  failed:'Reprovado',error:'Erro de execução',timeout:'Tempo esgotado',interrupted:'Interrompido',passed:'Aprovada'};
const testsLabel = value => testsLabels[value] || value || 'Indisponível';

function testsOptions() {
  return {profile:$('tests-profile').value,live_authorized:$('tests-live').checked,
    automatic_approval:$('tests-auto').checked,case_seconds:Number($('tests-case-seconds').value),
    total_seconds:Number($('tests-total-seconds').value),skip_interrupted:$('tests-skip').checked};
}
function testsKey() {
  const options = testsOptions(); delete options.live_authorized;
  return JSON.stringify([sessionInfo?.workspace_id,testsSnapshot?.queue_id,testsSnapshot?.queue?.manifest_hash,options]);
}
function validTestsContext(epoch, workspaceId) {
  return epoch === testsEpoch && workspaceId === sessionInfo?.workspace_id && !sessionEnded && $('tests-dialog').open;
}
function syncTestsControls() {
  if (!busy && !testsPending && testsOwnRequest) {
    testsOwnRequest = false;
    if (testsSnapshot) testsSnapshot.run_active = false;
  }
  if (testsSnapshot && testsSnapshot.workspace_id !== sessionInfo?.workspace_id) {
    testsEpoch++; testsSnapshot = null; testsCheckedKey = null; testsLoadedQueue = null; testsOwnRequest = false;
    $('tests-cases').replaceChildren(); $('tests-detail').hidden = true;
    $('tests-status').textContent = 'Projeto alterado. Atualize o painel antes de continuar.';
  }
  const known = Boolean(testsSnapshot && testsSnapshot.workspace_id === sessionInfo?.workspace_id);
  const queue = known ? testsSnapshot.queue : null;
  const active = known && (testsSnapshot.run_active || queue?.runner_active);
  const idle = !busy && !testsPending && !sessionEnded;
  const localMode = sessionInfo?.llm_enabled === false;
  $('tests-llm-note').textContent = localMode ? 'Uso local: prepare e confira a bateria. Ative a LLM para executar as tarefas.' : 'LLM ativada. Revise a preparação e autorize esta execução abaixo.';
  $('tests-model-toggle').hidden = !localMode;
  $('tests-model-toggle').disabled = !idle || (typeof llmTogglePending !== 'undefined' && llmTogglePending);
  $('open-tests').disabled = sessionEnded || !initialized;
  for (const id of ['tests-source','tests-browse','tests-open','tests-existing','tests-open-path','tests-recent','tests-create','tests-profile',
    'tests-case-id','tests-case-objective','tests-check-kind','tests-check-path','tests-check-expected','tests-seed-path','tests-seed-content',
    'tests-auto','tests-skip','tests-case-seconds','tests-total-seconds','tests-background']) $(id).disabled = !idle;
  $('tests-create').disabled = !idle || !$('tests-source').value.trim();
  const checkKind = $('tests-check-kind').value;
  const caseId = $('tests-case-id').value.trim();
  const validCaseId = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(caseId);
  $('tests-case-id').setAttribute('aria-invalid', String(Boolean(caseId) && !validCaseId));
  $('tests-case-id').title = caseId && !validCaseId ? 'Use letras sem acento, números, hífen ou sublinhado; comece por letra ou número.' : '';
  const needsExpected = checkKind === 'text_equals' || checkKind === 'json_equals';
  $('tests-check-expected-wrap').hidden = !needsExpected;
  $('tests-case-create').disabled = !idle || !$('tests-source').value.trim()
    || !validCaseId || !$('tests-case-objective').value.trim()
    || !$('tests-check-path').value.trim() || (checkKind === 'json_equals' && !$('tests-check-expected').value.trim())
    || (Boolean($('tests-seed-content').value) && !$('tests-seed-path').value.trim());
  $('tests-open-path').disabled = !idle || !$('tests-existing').value.trim();
  $('tests-check').disabled = !idle || !queue;
  const options = testsOptions();
  const limitsValid = Number.isInteger(options.case_seconds) && options.case_seconds >= 1 && options.case_seconds <= 3600
    && Number.isInteger(options.total_seconds) && options.total_seconds >= 1 && options.total_seconds <= 86400;
  const startHelp = sessionInfo?.llm_enabled === false ? 'Ative a LLM para executar as tarefas desta bateria.'
    : !queue || !limitsValid ? 'Prepare uma bateria válida antes de executar.'
    : active ? 'Aguarde a execução atual terminar.'
    : !options.live_authorized ? 'Marque a autorização para chamadas à LLM desta execução.'
    : testsCheckedKey !== testsKey() ? 'Confira a preparação novamente após alterar as opções.'
    : '';
  $('tests-start').title = startHelp;
  $('tests-start-help').title = startHelp;
  $('tests-start-help').setAttribute('aria-label', startHelp || 'Iniciar ou retomar a bateria');
  $('tests-start-help').tabIndex = startHelp ? 0 : -1;
  $('tests-start').disabled = sessionInfo?.llm_enabled === false || !idle || active || !queue || !limitsValid || !options.live_authorized
    || testsCheckedKey !== testsKey() || !(queue.counts.pending > 0)
    || (queue.counts.interrupted > 0 && !options.skip_interrupted);
  $('tests-live').disabled = busy || testsPending || sessionEnded;
  $('tests-approval-note').textContent = options.automatic_approval
    ? 'Ferramentas: aprovação automática autorizada nos projetos de teste, sem sandbox.'
    : 'Ferramentas: aprovação automática desativada. Ações que exigirem aprovação ficarão bloqueadas; a bateria não abre pedidos interativos.';
  $('tests-pause').disabled = !active || testsPending || sessionEnded;
  $('tests-stop').disabled = !active || testsPending || sessionEnded;
  $('tests-export').disabled = !queue || testsPending || sessionEnded;
  $('tests-refresh').disabled = testsPending || sessionEnded;
  for (const button of $('tests-cases').querySelectorAll('button')) {
    button.disabled = testsPending || sessionEnded || (button.dataset.action === 'next' && (!idle || active));
  }
  $('tests-nav-status').textContent = active || testsOwnRequest ? 'Em execução' : '';
}

function testsSettingsChanged() {
  testsCheckedKey = null;
  $('tests-preflight').textContent = 'Opções alteradas. Confira a preparação antes de iniciar.';
  syncTestsControls();
}

function renderTestCases(queue) {
  const filter = $('tests-filter').value;
  const attention = ['failed','error','timeout','interrupted','unverified'];
  const rows = (queue?.cases || []).filter(row => filter === 'all' || (filter === 'attention' ? attention.includes(row.status) : row.status === filter));
  const key = JSON.stringify([testsSnapshot?.queue_id,filter,testsPageSize,rows]);
  if (key === testsRowsKey) return;
  testsRowsKey = key;
  const host = $('tests-cases');
  const scroll = $('tests-dialog').scrollTop;
  const focusedAction = document.activeElement?.dataset?.action;
  const focusedCase = document.activeElement?.parentElement?.dataset?.caseId;
  host.replaceChildren();
  for (const row of rows.slice(0, testsPageSize)) {
    const card = document.createElement('article'); card.className = 'tests-case';
    card.dataset.caseId = row.id;
    const title = document.createElement('h4'); title.textContent = `${row.id} — ${testsLabel(row.status)}`;
    const note = document.createElement('p'); note.textContent = `${row.checks} critério(s) externo(s)${row.checks ? '' : ' · Não poderá receber resultado verificado.'}`;
    card.append(title,note);
    const details = document.createElement('button'); details.type = 'button'; details.textContent = 'Ver evidências';
    details.dataset.action = 'details'; details.addEventListener('click', () => testQueueAction('details',row.id)); card.append(details);
    if (row.status === 'pending') {
      const next = document.createElement('button'); next.type = 'button'; next.textContent = 'Executar na próxima posição';
      next.dataset.action = 'next'; next.addEventListener('click', () => testQueueAction('next',row.id)); card.append(next);
    }
    host.append(card);
  }
  if (!rows.length) { const note = document.createElement('p'); note.textContent = queue ? 'Nenhuma tarefa neste filtro.' : 'Prepare ou abra uma fila para começar.'; host.append(note); }
  $('tests-more').hidden = rows.length <= testsPageSize;
  $('tests-more').textContent = `Mostrar mais tarefas (${Math.min(rows.length,testsPageSize)} de ${rows.length})`;
  $('tests-dialog').scrollTop = scroll;
  if (focusedCase && focusedAction) {
    const target = [...host.querySelectorAll('button')].find(button => button.dataset.action === focusedAction && button.parentElement?.dataset.caseId === focusedCase);
    target?.focus({preventScroll:true});
  }
}

function renderTests(state) {
  testsSnapshot = state;
  const recent = $('tests-recent');
  const recentKey = JSON.stringify(state.recent);
  if (recent.dataset.key !== recentKey) {
    recent.replaceChildren();
    const empty = document.createElement('option'); empty.value = ''; empty.textContent = 'Selecione uma fila recente'; recent.append(empty);
    for (const item of state.recent) { const option = document.createElement('option'); option.value = item.id; option.textContent = `${item.label} (${item.id.slice(0,6)})`; option.title = item.path; recent.append(option); }
    recent.dataset.key = recentKey;
  }
  recent.value = state.queue_id || '';
  const profiles = $('tests-profile'); const profileKey = JSON.stringify(state.profiles);
  if (profiles.dataset.key !== profileKey) {
    const previous = profiles.value; profiles.replaceChildren();
    for (const profile of state.profiles) { const option = document.createElement('option'); option.value = profile.name; option.textContent = `${profile.name} · ${profile.model || profile.provider || ''}`; profiles.append(option); }
    profiles.value = state.profiles.some(item => item.name === previous) ? previous : state.selected_profile || '';
    profiles.dataset.key = profileKey; testsCheckedKey = null;
  }
  const queue = state.queue;
  if (testsLoadedQueue !== state.queue_id) {
    testsLoadedQueue = state.queue_id; testsCheckedKey = null; testsPageSize = 100; testsRowsKey = '';
    testsReportPath = '';
    testsFilterChosen = false; $('tests-filter').value = 'all';
    $('tests-prepare').open = !queue;
    $('tests-live').checked = false; $('tests-skip').checked = false; $('tests-detail').hidden = true;
    const saved = queue?.execution_options;
    $('tests-auto').checked = saved?.automatic_approval === true;
    $('tests-case-seconds').value = saved?.case_seconds || 300;
    $('tests-total-seconds').value = saved?.total_seconds || 3600;
    if (saved?.profile && state.profiles.some(item => item.name === saved.profile)) profiles.value = saved.profile;
    $('tests-preflight').textContent = 'Confira a preparação antes de iniciar.';
  }
  if (!queue) $('tests-prepare').open = true;
  const counts = queue?.counts || {};
  const needsAttention = ['failed','error','timeout','interrupted','unverified'].some(key => counts[key] > 0);
  if (queue?.status === 'completed' && needsAttention && !testsFilterChosen) $('tests-filter').value = 'attention';
  const total = queue?.cases?.length || 0;
  const completed = total - (counts.pending || 0) - (counts.running || 0);
  $('tests-progress').max = total || 1;
  if ($('tests-progress').value !== completed) $('tests-progress').value = completed;
  $('tests-progress').dataset.attention = needsAttention ? 'yes' : 'no';
  const summary = queue ? `${testsLabel(queue.status)} · ${completed}/${total} tarefas finalizadas · `
    + Object.entries(counts).map(([key,value]) => `${testsLabel(key)}: ${value}`).join(' · ') : 'Nenhuma fila selecionada.';
  let warning = '';
  if (queue?.manifest_validation === 'deferred_to_action_or_execution') warning += ' A verifica\u00e7\u00e3o completa do manifesto ocorre ao conferir ou iniciar a fila.';
  const pythonVerifierCount = (queue?.cases || []).reduce((total, item) =>
    total + (Number.isInteger(item?.python_verifiers) ? item.python_verifiers : 0), 0);
  if (pythonVerifierCount) warning += ` ATENÇÃO: esta bateria executa ${pythonVerifierCount} verificador(es) Python com os privilégios da sua conta e sem isolamento. Eles podem acessar outros arquivos e a rede. Use apenas verificadores que você criou ou revisou.`;
  if (queue?.without_checks?.length) warning += ` ${queue.without_checks.length} tarefa(s) sem critérios externos.`;
  if (queue?.not_imported?.length) warning += ` Pastas não importadas: ${queue.not_imported.join(', ')}.`;
  if (queue?.status === 'completed') warning += ' Finalizada não significa que todos os testes passaram.';
  if (queue?.runner_active && !state.run_active) warning += ' Esta fila está rodando em outro processo. Você pode acompanhá-la e solicitar pausa ou parada aqui.';
  if (state.pause_requested) warning += ' Pausa solicitada: aguardando a tarefa atual e sua verificação.';
  if (queue && !counts.pending && !state.run_active) warning += ' Prepare uma nova bateria para repetir o experimento.';
  const configurationNote = state.configuration_error ? 'Execução com LLM indisponível. Preparação e consulta locais continuam disponíveis.' : '';
  $('tests-location').title = state.configuration_error || '';
  const status = [summary,warning,configurationNote,state.queue_error].filter(Boolean).join(' ');
  if ($('tests-status').textContent !== status) $('tests-status').textContent = status;
  $('tests-location').textContent = queue ? `Pasta: ${queue.queue}
Manifesto: ${queue.manifest_hash}
Identidade da execução: ${queue.identity || 'Ainda não executada'}${testsReportPath ? '\nRelatório HTML: ' + testsReportPath : ''}` : '';
  renderTestCases(queue); syncTestsControls();
}

async function refreshTests() {
  if (testsPending || sessionEnded || !$('tests-dialog').open) return;
  const epoch = testsEpoch, workspaceId = sessionInfo?.workspace_id, read = ++testsRead;
  try {
    const state = await api().test_queue_state(workspaceId);
    if (!validTestsContext(epoch,workspaceId) || read !== testsRead) return;
    if (!state?.accepted || state.workspace_id !== workspaceId || !Array.isArray(state.recent) || !Array.isArray(state.profiles)) throw new Error(state?.message || 'Estado de testes indisponível.');
    renderTests(state);
  } catch (error) {
    if (validTestsContext(epoch,workspaceId)) { testsCheckedKey = null; $('tests-status').textContent = `Não foi possível atualizar: ${String(error)}. Use Atualizar para tentar novamente.`; syncTestsControls(); }
  }
}

async function openTests() {
  if (sessionEnded || !initialized) return;
  if (typeof api().test_queue_state !== 'function') { toast('Atualize e reinicie o Workbench para abrir Testes.'); return; }
  testsOpener = document.activeElement; testsEpoch++; $('tests-dialog').showModal();
  await refreshTests();
  clearInterval(testsTimer); testsTimer = setInterval(() => { if (!document.hidden) refreshTests(); },1500);
}
function closeTests() {
  testsEpoch++; clearInterval(testsTimer); testsTimer = null; testsCheckedKey = null;
  $('tests-live').checked = false; $('tests-dialog').close(); testsOpener?.focus(); testsOpener = null;
}

async function testsOperation(operation, receive, idle = false) {
  if (testsPending || sessionEnded || (idle && busy)) return;
  const epoch = ++testsEpoch, workspaceId = sessionInfo?.workspace_id;
  testsPending = true;
  const ownsBusy = idle && !busy;
  if (ownsBusy) { busyActionInFlight = true; setBusy(true); }
  syncTestsControls();
  try {
    const result = await operation(workspaceId);
    if (!validTestsContext(epoch,workspaceId)) return;
    if (!result?.accepted) throw new Error(result?.message || 'Operação não confirmada. Atualize a fila.');
    receive(result);
  } catch (error) {
    if (validTestsContext(epoch,workspaceId)) { testsCheckedKey = null; $('tests-status').textContent = String(error); }
  } finally {
    testsPending = false;
    if (ownsBusy) { busyActionInFlight = false; setBusy(false); }
    syncTestsControls();
  }
}

async function selectTestQueue(action,value) {
  if (!$('tests-dialog').open) return;
  testsCheckedKey = null;
  await testsOperation(id => api().test_queue_select(id,action,value),renderTests,true);
}
async function pickTestQueueFolder(openExisting) {
  const workspace = sessionInfo?.workspace_id;
  await testsOperation(id => api().test_queue_pick_directory(id), result => {
    if (!result.cancelled && result.path) {
      if (openExisting) {
        const epoch = testsEpoch;
        setTimeout(() => { if (validTestsContext(epoch,workspace)) selectTestQueue('open',result.path); },0);
      }
      else $('tests-source').value = result.path;
    }
  },true);
}
async function testQueueAction(action,value = '') {
  const state = testsSnapshot; if (!state?.queue) return;
  const key = testsKey();
  await testsOperation(id => api().test_queue_action(id,state.queue_id,state.queue.manifest_hash,action,value,testsOptions()), result => {
    if (action === 'check') {
      const checked = result.preflight;
      if (result.local_only === true) {
        testsCheckedKey = null;
        $('tests-preflight').textContent = `Estrutura conferida localmente, sem LLM. ${checked.without_checks.length} tarefa(s) sem critérios externos. Para executar tarefas com a LLM, configure e escolha um perfil.`;
        return;
      }
      const compatible = checked?.configuration?.resume_compatible === true;
      testsCheckedKey = compatible && key === testsKey() ? key : null;
      const budget = checked?.configuration?.task_budgets;
      $('tests-preflight').textContent = compatible
        ? `Preparação conferida sem chamadas à LLM. ${checked.without_checks.length} tarefa(s) sem critérios externos. Limite: ${budget?.max_model_calls || '?'} chamadas por tarefa. ${checked.counts.interrupted ? 'Revise as interrompidas antes de retomar.' : 'Marque a autorização para iniciar.'}`
        : 'A configuração ou o código mudou desde a execução anterior. Prepare uma nova bateria para este experimento.';
    } else if (action === 'details') {
      const row = result.details, execution = row.execution || {}, verification = row.verification || {};
      $('tests-detail').hidden = false; $('tests-detail-title').textContent = `${row.id} — ${testsLabel(row.status)}`;
      const failed = (verification.checks || []).filter(check => check.passed !== true).map(check => `${check.kind} em ${check.path}${check.error ? ': ' + check.error : ''}`);
      $('tests-detail-summary').textContent = `Sucesso declarado pelo runtime: ${execution.success === true ? 'sim' : execution.success === false ? 'não' : 'não disponível'}
Verificação externa: ${testsLabel(verification.status)}
${failed.length ? 'Critérios reprovados:\n' + failed.join('\n') : ''}
${row.error || row.detail_error || execution.error || ''}`;
      if (typeof result.detail_summary === 'string') $('tests-detail-summary').textContent = result.detail_summary;
      $('tests-detail-data').textContent = JSON.stringify(row,null,2);
    } else if (action === 'detach') {
      $('tests-live').checked = false;
      $('tests-status').textContent = `${result.worker.message} PID: ${result.worker.pid}. Log: ${result.worker.log}`;
    } else if (action === 'pause' || action === 'stop') {
      $('tests-status').textContent = action === 'pause' ? 'Pausa solicitada. A tarefa atual e sua verificação terminam antes da pausa.' : 'Parada solicitada. Aguarde a interrupção e revise as evidências antes de retomar.';
    } else renderTests(result);
  },action === 'check' || action === 'next' || action === 'detach');
}
async function startTests() {
  if ($('tests-start').disabled || !testsSnapshot?.queue) return;
  const state = testsSnapshot, options = testsOptions();
  if ($('tests-background').checked) {
    testsCheckedKey = null;
    await testQueueAction('detach');
    await refreshTests();
    return;
  }
  testsCheckedKey = null; testsOwnRequest = true; testsPending = true; syncTestsControls();
  try {
    await dispatchSubmission('',async () => {
      try {
        const result = await api().test_queue_start(state.workspace_id,state.queue_id,state.queue.manifest_hash,options);
        if (!result.accepted) testsOwnRequest = false;
        return result;
      } catch (error) { testsOwnRequest = false; throw error; }
    }, null, 'tests');
  } finally {
    testsPending = false; $('tests-live').checked = false; syncTestsControls(); await refreshTests();
  }
}

$('open-tests').addEventListener('click',openTests);
$('tests-close').addEventListener('click',closeTests);
$('tests-dialog').addEventListener('cancel',event => { event.preventDefault(); closeTests(); });
$('tests-refresh').addEventListener('click',refreshTests);
$('tests-browse').addEventListener('click',() => pickTestQueueFolder(false));
$('tests-open').addEventListener('click',() => pickTestQueueFolder(true));
$('tests-open-path').addEventListener('click',() => selectTestQueue('open',$('tests-existing').value.trim()));
$('tests-existing').addEventListener('input',syncTestsControls);
$('tests-create').addEventListener('click',() => selectTestQueue('create',$('tests-source').value.trim()));
$('tests-check-kind').addEventListener('change',syncTestsControls);
for (const id of ['tests-case-id','tests-case-objective','tests-check-path','tests-check-expected','tests-seed-path','tests-seed-content']) {
  $(id).addEventListener('input',syncTestsControls);
}
$('tests-case-create').addEventListener('click',() => {
  const source = $('tests-source').value.trim();
  const values = {
    caseId:$('tests-case-id').value.trim(), objective:$('tests-case-objective').value,
    kind:$('tests-check-kind').value, path:$('tests-check-path').value.trim(),
    expected:$('tests-check-expected').value, seedPath:$('tests-seed-path').value.trim(), seedContent:$('tests-seed-content').value,
  };
  $('tests-case-status').textContent = 'Criando tarefa e critério de verificação…';
  testsOperation(id => api().test_case_create(id,source,values.caseId,values.objective,values.kind,values.path,
    values.expected,values.seedPath,values.seedContent), result => {
      $('tests-case-status').textContent = `Tarefa criada em ${result.path}. Agora prepare uma bateria para importá-la.`;
      $('tests-case-id').value = '';
      $('tests-case-objective').value = '';
      $('tests-check-expected').value = '';
      $('tests-seed-path').value = '';
      $('tests-seed-content').value = '';
      syncTestsControls();
    },true);
});
$('tests-recent').addEventListener('change',() => { if ($('tests-recent').value) selectTestQueue('select',$('tests-recent').value); });
$('tests-source').addEventListener('input',syncTestsControls);
for (const id of ['tests-profile','tests-auto','tests-skip','tests-case-seconds','tests-total-seconds']) $(id).addEventListener('change',testsSettingsChanged);
$('tests-live').addEventListener('change',syncTestsControls);
$('tests-check').addEventListener('click',() => testQueueAction('check'));
$('tests-start').addEventListener('click',startTests);
$('tests-pause').addEventListener('click',() => testQueueAction('pause'));
$('tests-stop').addEventListener('click',() => testQueueAction('stop'));
$('tests-filter').addEventListener('change',() => { testsFilterChosen = true; testsPageSize = 100; renderTestCases(testsSnapshot?.queue); syncTestsControls(); });
$('tests-more').addEventListener('click',() => { testsPageSize += 100; renderTestCases(testsSnapshot?.queue); syncTestsControls(); });
$('tests-export').addEventListener('click',() => {
  const state = testsSnapshot; if (!state?.queue) return;
  testsOperation(id => api().test_queue_export(id,state.queue_id,state.queue.manifest_hash),result => {
    testsReportPath = result.report_path;
    $('tests-location').textContent += `\nRelatório HTML: ${testsReportPath}`;
    $('tests-status').textContent = `Relatório salvo: ${result.report_path}`;
  });
});
