const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;
const MAX_VISIBLE_ITEMS = 500;
const MAX_STREAM_CHARS = 64_000;
let panelRef = null;
let panelGeneration = 0;
let latestRef = null;
let busy = false;
let activeRequestId = null;
let submissionGeneration = 0;
let currentSubmission = null;
let streamedRequestId = null;
let streamedBubble = null;
let initialized = false;
let offlineSession = false;
let lastBridgeError = '';
let demoPrompt = '';
let demoRoundsRemaining = 0;

function toast(text) {
  const box = $('toast');
  box.textContent = text;
  box.classList.add('show');
  setTimeout(() => box.classList.remove('show'), 2200);
}

function trimHistory() {
  const host = $('messages');
  const rows = [...host.querySelectorAll('.message,.event,.artifact-offer')];
  while (rows.length > MAX_VISIBLE_ITEMS) rows.shift().remove();
}

function message(kind, text) {
  const host = $('messages');
  const row = document.createElement('div');
  row.className = `message ${kind}`;
  if (kind !== 'user') {
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = kind === 'agent' ? 'A' : 'U';
    row.append(avatar);
  }
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  row.append(bubble);
  host.append(row);
  trimHistory();
  $('conversation').scrollTop = $('conversation').scrollHeight;
  return row;
}

function eventRow(text, detail = '') {
  const host = $('messages');
  const row = document.createElement('div');
  row.className = 'event';
  const icon = document.createElement('i');
  icon.textContent = '✓';
  const label = document.createElement('span');
  label.textContent = text;
  row.append(icon, label);
  if (detail) {
    const small = document.createElement('small');
    small.textContent = detail;
    row.append(small);
  }
  host.append(row);
  trimHistory();
}

function setBusy(value, requestId = null) {
  busy = value;
  activeRequestId = value ? requestId : null;
  $('send').disabled = value;
  $('cancel').hidden = !value || !requestId;
  $('cancel').disabled = false;
  for (const id of ['nav-files', 'nav-tasks', 'nav-tools']) {
    $(id).disabled = value || $(id).dataset.available !== 'yes';
    if (value) $(id).title = 'Ocupado: aguarde o encerramento da opera\u00e7\u00e3o atual.';
  }
  $('run-status').textContent = value ? 'Executando an\u00e1lise\u2026' : 'Pronto';
}

async function dispatchSubmission(text, demo = false, operation = null) {
  const submission = {generation: ++submissionGeneration, requestId: null, terminal: false};
  currentSubmission = submission;
  const isCurrent = () => currentSubmission === submission
    && submission.generation === submissionGeneration && !submission.terminal;
  setBusy(true);
  $('run-status').textContent = demo && offlineSession ? 'Executando fixture offline…' : 'Enviando solicitação…';
  try {
    const result = await (operation ? operation() : api().submit(text));
    // A terminal event can arrive before this acknowledgement, or a newer
    // submission can already own the UI. Neither may be reversed by this reply.
    if (!isCurrent()) return;
    if (!result.accepted) throw new Error(result.message || 'A solicitação não foi aceita.');
    if (submission.requestId !== null && submission.requestId !== result.request_id) {
      throw new Error('Identidade da solicitação divergente.');
    }
    submission.requestId = result.request_id;
    setBusy(true, result.request_id);
  } catch (error) {
    if (!isCurrent()) return;
    submission.terminal = true;
    message('agent', `${demo ? 'Falha no fluxo simulado' : 'Não foi possível enviar a solicitação'}: ${String(error)}`);
    if (demo) demoRoundsRemaining = 0;
    setBusy(false);
  }
}

function finishSubmission(requestId) {
  if (currentSubmission) {
    if (currentSubmission.terminal) return false;
    if (currentSubmission.requestId !== null && currentSubmission.requestId !== requestId) return false;
    // The bridge admits one request at a time and drains its events in FIFO.
    // Before acknowledgement, its first terminal therefore binds this identity.
    currentSubmission.requestId = requestId;
    currentSubmission.terminal = true;
  } else if (activeRequestId !== requestId) {
    return false;
  }
  setBusy(false);
  return true;
}

function renderReport(text) {
  const body = $('report-body');
  body.replaceChildren();
  const pre = document.createElement('pre');
  pre.textContent = text;
  body.append(pre);
}

function clearPanelContent() {
  $('copy-report').disabled = true;
  delete $('copy-report').dataset.text;
  $('report-integrity').textContent = 'Leitura pendente; integridade não verificada';
  $('report-body').replaceChildren();
}

function closePanel() {
  panelGeneration += 1;
  panelRef = null;
  clearPanelContent();
  $('inspector').hidden = true;
  $('resize-grip').hidden = true;
}

function openPanel(ref) {
  const generation = ++panelGeneration;
  panelRef = ref;
  clearPanelContent();
  const isCurrent = () => generation === panelGeneration && panelRef?.token === ref.token;
  $('inspector').hidden = false;
  $('resize-grip').hidden = false;
  $('tab-title').textContent = ref.title || 'Relatório';
  $('report-heading').textContent = ref.title || 'Relatório do Agent';
  $('panel-kind-label').textContent = ref.kind && ref.kind !== 'report' ? 'INSPEÇÃO DO WORKSPACE' : 'RELATÓRIO DE EXECUÇÃO';
  $('report-source').textContent = ref.source || 'origem não informada';
  $('report-run').textContent = ref.run_id ? `Execução ${ref.run_id}` : 'Execução não informada';
  $('report-workspace').textContent = ref.workspace_id || $('workspace-name').textContent;
  const phase = {
    final: ['FINAL', 'final'],
    partial: ['PARCIAL', 'parcial'],
    unknown: ['NÃO INFORMADA', 'completude não informada'],
  }[ref.completeness] || ['NÃO INFORMADA', 'completude não informada'];
  $('completeness-label').textContent = phase[0];
  $('report-state').textContent = `${ref.kind && ref.kind !== 'report' ? 'Completude da projeção' : 'Completude do relatório da execução'}: ${phase[1]}`;
  $('artifact-count').textContent = '1';
  $('artifact-link').disabled = false;
  renderReport('Carregando artefato…');
  api().read_report(ref.token)
    .then(result => {
      if (!isCurrent()) return;
      if (result.token !== ref.token || result.reference.workspace_id !== ref.workspace_id
          || result.reference.run_id !== ref.run_id || result.reference.artifact_id !== ref.artifact_id) {
        throw new Error('Identidade do artefato divergente.');
      }
      renderReport(result.text);
      $('copy-report').disabled = false;
      $('report-integrity').textContent = [
        result.truncated ? 'Conteúdo mostrado: truncado' : 'Conteúdo mostrado: leitura sem truncamento',
        `Renderer: ${result.renderer}`,
        result.next_offset == null ? '' : `Próximo offset: ${result.next_offset}`,
        result.sha256 ? `SHA-256 calculado/disponível: ${result.sha256}` : 'Digest não disponível',
        'Integridade não verificada contra digest esperado',
      ].filter(Boolean).join(' · ');
      $('copy-report').dataset.text = result.text;
    })
    .catch(error => {
      if (!isCurrent()) return;
      clearPanelContent();
      $('report-integrity').textContent = 'Falha de leitura; integridade não verificada';
      renderReport(`Não foi possível carregar este artefato.\n${String(error)}`);
    });
}

async function submit() {
  if (busy) return;
  const input = $('prompt');
  const text = input.value.trim() || (
    input.value.trim() === '' && $('welcome').style.display !== 'none'
      && input.dataset.suggestion === 'yes'
      ? 'Analise notes.txt, leia o arquivo e descreva os pontos encontrados.' : ''
  );
  if (!text) {
    input.focus();
    return;
  }
  input.value = '';
  input.dataset.suggestion = '';
  $('welcome').style.display = 'none';
  message('user', text);
  await dispatchSubmission(text);
}

async function inspectWorkspace(kind) {
  if (busy) return;
  $('welcome').style.display = 'none';
  const label = {files:'Consultar arquivos', tasks:'Consultar estado da sessão', tools:'Consultar ferramentas'}[kind];
  message('user', label);
  await dispatchSubmission(label, false, () => api().inspect(kind));
}

async function cancelActive() {
  if (!activeRequestId) return;
  const requestId = activeRequestId;
  $('cancel').disabled = true;
  $('run-status').textContent = 'Cancelamento solicitado…';
  try {
    const result = await api().cancel(requestId);
    if (activeRequestId !== requestId) return;
    if (!result.accepted) {
      toast(result.message || 'Não foi possível solicitar o cancelamento.');
      $('cancel').disabled = false;
    }
  } catch (error) {
    if (activeRequestId !== requestId) return;
    message('agent', `Falha ao solicitar cancelamento: ${String(error)}`);
    $('cancel').disabled = false;
  }
}

async function runDemoRound() {
  if (!initialized || !demoRoundsRemaining || busy) return;
  $('welcome').style.display = 'none';
  message('user', demoPrompt);
  await dispatchSubmission(demoPrompt, true);
}

function streamDelta(item) {
  if (streamedRequestId !== item.request_id) {
    streamedRequestId = item.request_id;
    streamedBubble = message('agent', '').querySelector('.bubble');
  }
  if (!streamedBubble) return;
  const current = streamedBubble.textContent;
  const remaining = Math.max(0, MAX_STREAM_CHARS - current.length);
  streamedBubble.textContent = current + item.text.slice(0, remaining);
  if ((item.truncated || item.text.length > remaining) && !streamedBubble.dataset.truncated) {
    streamedBubble.dataset.truncated = 'yes';
    streamedBubble.textContent += '\n[Prévia em fluxo limitada; a resposta final substituirá esta prévia.]';
    eventRow('Prévia de streaming truncada', item.request_id);
  }
  $('run-status').textContent = 'Recebendo resposta…';
  $('conversation').scrollTop = $('conversation').scrollHeight;
}

function expireArtifact(token) {
  if (latestRef?.token === token) {
    latestRef = null;
    $('artifact-link').disabled = true;
  }
  document.querySelector(`[data-artifact-token="${CSS.escape(token)}"]`)?.remove();
  if (panelRef?.token === token) {
    closePanel();
    toast('Esta referência de relatório expirou na sessão.');
  }
}

function consume(events) {
  for (const item of events) {
    if (item.type === 'delta') {
      streamDelta(item);
    } else if (item.type === 'cancel_requested') {
      eventRow('Cancelamento solicitado', item.request_id);
    } else if (item.type === 'artifact_expired') {
      expireArtifact(item.token);
    } else if (item.type === 'settled') {
      if (!finishSubmission(item.request_id)) continue;
      const observation = item.observation;
      const answer = observation.answer || observation.summary || observation.error
        || 'A aplicação não forneceu resposta textual.';
      const wasCancelled = observation.reason_code === 'INTERACTION_CANCELLED';
      eventRow(
        wasCancelled ? 'Execução cancelada' : `Execução ${observation.status || 'sem estado informado'}`,
        observation.reason_code || '',
      );
      if (streamedRequestId === item.request_id && streamedBubble) {
        streamedBubble.textContent = answer;
      } else {
        message('agent', answer);
      }
      streamedRequestId = null;
      streamedBubble = null;
      if (item.reference) {
        const ref = item.reference;
        latestRef = ref;
        $('artifact-count').textContent = '1';
        $('artifact-link').disabled = false;
        $('run-status').textContent = ref.kind && ref.kind !== 'report' ? 'Inspeção disponível' : 'Relatório disponível';
        const offer = document.createElement('button');
        offer.className = 'suggestion artifact-offer';
        offer.dataset.artifactToken = ref.token;
        offer.textContent = `▤  Abrir relatório · ${ref.title}`;
        offer.addEventListener('click', () => openPanel(ref));
        $('messages').append(offer);
        trimHistory();
      }
      if (wasCancelled) {
        $('run-status').textContent = item.reference
          ? 'Execução cancelada · Relatório disponível' : 'Execução cancelada';
      }
      // A campaign advances only on confirmed success. Failure, blockage or
      // cancellation requires a new explicit submission, regardless of cause.
      if (observation.status !== 'succeeded') {
        demoRoundsRemaining = 0;
      } else if (demoRoundsRemaining > 1) {
        demoRoundsRemaining -= 1;
        setTimeout(runDemoRound, 0);
      } else if (demoRoundsRemaining === 1) {
        demoRoundsRemaining = 0;
      }
    } else if (item.type === 'error') {
      if (!finishSubmission(item.request_id)) continue;
      demoRoundsRemaining = 0;
      message('agent', item.message);
    }
  }
}

async function pollEvents() {
  let failures = 0;
  while (initialized) {
    try {
      const events = await api().wait_events(1000);
      consume(events);
      if (events.some(e => e.type === "settled" || e.type === "error") && api().availability) renderAvailability(await api().availability());
      lastBridgeError = '';
      failures = 0;
    } catch (error) {
      const detail = String(error);
      if (detail !== lastBridgeError) {
        eventRow('Erro de comunicação com a bridge', detail);
        lastBridgeError = detail;
      }
      await new Promise(resolve => setTimeout(resolve, Math.min(30000, 1000 * 2 ** Math.min(++failures, 5))));
    }
  }
}

function renderSessionInfo(info) {
  $('workspace-name').textContent = info.workspace_name;
  $('workspace-name-status').textContent = info.workspace_name;
  $('report-workspace').textContent = info.workspace_name;
  offlineSession = info.execution_mode === 'offline_fixture';
  const configured = info.execution_mode === 'configured_endpoint';
  for (const [id, kind] of [['nav-files','files'], ['nav-tasks','tasks'], ['nav-tools','tools']]) {
    const available = Array.isArray(info.inspection_kinds) && info.inspection_kinds.includes(kind);
    $(id).dataset.available = available ? 'yes' : 'no';
    $(id).disabled = busy || !available;
    $(id).title = available ? `Consultar ${kind === 'files' ? 'arquivos' : kind === 'tasks' ? 'estado da sessão' : 'inventário de ferramentas'} pelos serviços compartilhados`
      : 'Inspeção indisponível neste modo de sessão';
    $(`${id}-availability`).textContent = available ? '' : 'Indisponível';
  }
  $('session-mode').textContent = offlineSession ? 'Teste offline · READ_ONLY'
    : configured ? 'Sessão real · READ_ONLY' : 'Modo não informado';
  $('model-availability').textContent = offlineSession ? 'Fixture offline · sem modelo real'
    : configured ? 'Endpoint do CLI · não verificado' : 'Modo de execução não informado';
  $('model-availability').title = offlineSession
    ? 'Fixture offline para testes; não utiliza endpoint de modelo real'
    : configured ? 'Usa a configuração e o perfil do CLI. A disponibilidade não foi verificada; a solicitação usará o endpoint configurado.'
      : 'A bridge não informou o modo de execução';
  $('model-name').textContent = offlineSession ? 'offline de teste'
    : configured ? 'configuração do CLI' : 'não informado';
}

window.addEventListener('pywebviewready', async () => {
  try {
    const info = await api().initialize();
    renderSessionInfo(info);
    if (api().availability) renderAvailability(await api().availability());
    initialized = true;
    pollEvents();
    if (info.demo_prompt) {
      demoPrompt = info.demo_prompt;
      demoRoundsRemaining = info.demo_rounds || 1;
      setTimeout(runDemoRound, info.demo_delay_ms || 0);
    }
  } catch (error) {
    message('agent', `Falha ao inicializar sessão: ${String(error)}`);
    setBusy(false);
  }
});

$('send').addEventListener('click', submit);
$('cancel').addEventListener('click', cancelActive);
$('nav-files').addEventListener('click', () => inspectWorkspace('files'));
$('nav-tasks').addEventListener('click', () => inspectWorkspace('tasks'));
$('nav-tools').addEventListener('click', () => inspectWorkspace('tools'));
$('nav-chat').addEventListener('click', () => $('prompt').focus());
$('shortcuts').addEventListener('click', () => toast('Ctrl+Enter: enviar. Enter: nova linha.'));
$('prompt').addEventListener('keydown', event => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    submit();
  }
});
$('theme-toggle').addEventListener('click', () => {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
});
$('suggestion').addEventListener('click', () => {
  $('prompt').value = 'Analise notes.txt, leia o arquivo e descreva os pontos encontrados.';
  $('prompt').dataset.suggestion = 'yes';
  submit();
});
$('collapse-nav').addEventListener('click', () => $('shell').classList.toggle('nav-collapsed'));
$('close-panel').addEventListener('click', closePanel);
$('artifact-link').addEventListener('click', () => {
  if (latestRef) openPanel(latestRef);
});
$('copy-report').addEventListener('click', async () => {
  if ($('copy-report').disabled) return;
  const text = $('copy-report').dataset.text;
  if (text) {
    try {
      await navigator.clipboard.writeText(text);
      toast('Relatório copiado');
    } catch (error) {
      toast(`Falha ao copiar relatório: ${String(error)}`);
    }
  }
});

const grip = $('resize-grip');
let drag = false;
grip.addEventListener('pointerdown', event => {
  drag = true;
  grip.classList.add('dragging');
  grip.setPointerCapture(event.pointerId);
});
grip.addEventListener('pointermove', event => {
  if (!drag) return;
  const width = Math.max(300, Math.min(600, window.innerWidth - event.clientX));
  $('inspector').style.width = `${width}px`;
  $('inspector').style.flexBasis = `${width}px`;
});
grip.addEventListener('pointerup', () => {
  drag = false;
  grip.classList.remove('dragging');
});

const availabilityLabels = {available:'Dispon\u00edvel',busy:'Ocupado',not_configured:'N\u00e3o configurado',not_implemented:'N\u00e3o implementado',forbidden:'Sem permiss\u00e3o',temporarily_unavailable:'Temporariamente indispon\u00edvel',unavailable:'Indispon\u00edvel',unverified:'Conex\u00e3o n\u00e3o verificada'};
function renderAvailability(projection) {
  if (!projection) return;
  const backend = projection.backend;
  const connectivityLabel = backend.connectivity === 'verified' ? 'Conectividade verificada' : 'Conectividade n\u00e3o verificada';
  const readinessLabel = backend.model_readiness === 'listed' ? 'Modelo listado; prontid\u00e3o para infer\u00eancia n\u00e3o verificada' : 'Prontid\u00e3o do modelo n\u00e3o verificada';
  $('model-availability').textContent = `${availabilityLabels[backend.state]} \u00b7 Testar conex\u00e3o`;
  $('model-availability').title = `${connectivityLabel}. ${readinessLabel}. ${backend.reason}. Consulta \u00fanica ao cat\u00e1logo, sem infer\u00eancia.`;
  $('model-availability').disabled = false;
  for (const [id, kind] of [['nav-files','files'],['nav-tasks','tasks'],['nav-tools','tools']]) {
    $(id).title = `${availabilityLabels[projection.resources[kind]]}. Consultar recurso local.`;
    if (kind === 'tools') {
      $(id).dataset.available = projection.resources[kind] === 'not_configured' ? 'no' : 'yes';
      $(id).disabled = busy || $(id).dataset.available === 'no';
    }
    $(`${id}-availability`).textContent = availabilityLabels[projection.resources[kind]];
  }
}
$('model-availability').addEventListener('click', async () => {
  $('model-availability').disabled = true;
  try { renderAvailability(await api().test_connection()); }
  catch (error) { toast(`Teste de conex\u00e3o: ${String(error)}`); }
  finally { $('model-availability').disabled = false; }
});
