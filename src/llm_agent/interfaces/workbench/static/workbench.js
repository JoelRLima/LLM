const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;
const MAX_VISIBLE_ITEMS = 500;
const MAX_STREAM_CHARS = 64_000;
let panelRef = null;
let panelGeneration = 0;
let latestRef = null;
let panelMode = 'artifact';
let mainView = 'home';
let homeResultNotice = '';
let homeResultTarget = 'assistant';
let workspaceQueryBusy = false;
let busy = false;
let activeRequestId = null;
let submissionGeneration = 0;
let currentSubmission = null;
let streamedRequestId = null;
let streamedBubble = null;
let initialized = false;
let lastBridgeError = '';
let busyActionInFlight = false;
let sessionInfo = null;
let availabilityRequestId = 0;
let appliedAvailabilityRequestId = 0;
let connectionProbeInFlight = false;
let commandEntries = [];
let commandScope = null;
let approvalIdentity = null;
let approvalResolving = false;
let sessionEnded = false;
const displacedDrafts = new Map();
const MAX_DRAFT_RECOVERY_WORKSPACES = 4;
const MAX_DRAFT_RECOVERY_VERSIONS = 4;
const MAX_DRAFT_RECOVERY_CHARS = 16_000;

function toast(text) {
  const box = $('toast');
  box.textContent = text;
  box.classList.add('show');
  setTimeout(() => box.classList.remove('show'), 2200);
}

function trimHistory() {
  const host = $('messages');
  const rows = [...host.querySelectorAll('.message,.event,.artifact-offer,.request-recovery')];
  while (rows.length > MAX_VISIBLE_ITEMS) rows.shift().remove();
}

function conversationMutationSnapshot() {
  return typeof captureConversationState === 'function' ? captureConversationState() : null;
}

function finishConversationMutation(snapshot) {
  if (snapshot && typeof completeConversationMutation === 'function') completeConversationMutation(snapshot);
}

function message(kind, text) {
  const conversationView = conversationMutationSnapshot();
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
  finishConversationMutation(conversationView);
  return row;
}

function eventRow(text, detail = '', severity = 'neutral') {
  const conversationView = conversationMutationSnapshot();
  const host = $('messages');
  const row = document.createElement('div');
  row.className = 'event';
  row.dataset.severity = severity;
  const icon = document.createElement('i');
  icon.textContent = severity === 'success' ? '\u2713' : severity === 'error' ? '\u00d7' : severity === 'warning' ? '!' : '\u00b7';
  icon.dataset.severity = severity;
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
  finishConversationMutation(conversationView);
}

function setBusy(value, requestId = null) {
  busy = value;
  activeRequestId = value ? requestId : null;
  $('send').disabled = sessionEnded || busyActionInFlight;
  $('cancel').hidden = !value || !requestId;
  $('cancel').disabled = false;
  if (value) homeResultNotice = '';
  syncHomeActivity();
  $('nav-files').disabled = sessionEnded || value || $('nav-files').dataset.available !== 'yes';
  // Tasks is a read-only projection and stays available during live work.
  $('nav-tasks').disabled = sessionEnded || $('nav-tasks').dataset.available !== 'yes';
  $('nav-tools').disabled = sessionEnded || value || $('nav-tools').dataset.available !== 'yes';
  $('run-workspace-query').disabled = sessionEnded || value || $('nav-files').dataset.available !== 'yes';
  $('model-availability').disabled = sessionEnded || value || connectionProbeInFlight || $('model-availability').dataset.configured !== 'yes';
  updateDraftRecoveryButton();
  for (const button of document.querySelectorAll('.request-review-button')) button.disabled = sessionEnded || value;
  if (value) $('run-status').textContent = 'Executando solicita\u00e7\u00e3o\u2026';
  if (typeof syncFileReference === 'function') syncFileReference();
  if (typeof syncPendingQueueActions === 'function') syncPendingQueueActions();
  if (typeof syncNavigationChoice === 'function') syncNavigationChoice();
  if (typeof syncPluginControls === 'function') syncPluginControls();
  if (typeof syncTestsControls === 'function') syncTestsControls();
  if (typeof syncLlmControls === 'function') syncLlmControls();
}

function restoreDraftIfEmpty(draft) {
  const input = $('prompt');
  if (!sessionEnded && typeof draft === 'string' && input.value === '') input.value = draft;
}

function draftWorkspaceKey(info = sessionInfo) {
  const id = typeof info?.workspace_id === 'string' ? info.workspace_id.trim() : '';
  if (id) return `id:${id}`;
  const root = typeof info?.workspace_root === 'string' ? info.workspace_root.trim() : '';
  return root ? `root:${root.toLocaleLowerCase()}` : null;
}

function updateDraftRecoveryButton() {
  const button = $('restore-draft');
  if (!button) return;
  const drafts = displacedDrafts.get(draftWorkspaceKey())?.drafts;
  const available = Array.isArray(drafts) && drafts.length > 0;
  button.hidden = !available;
  button.disabled = !available || busy || sessionEnded;
}

function clearDraftRecovery() {
  displacedDrafts.clear();
  updateDraftRecoveryButton();
}

function rememberDisplacedDraft(workspaceKey, draft) {
  if (!workspaceKey || !draft.text || draft.text.length + draft.placeholder.length > MAX_DRAFT_RECOVERY_CHARS) return false;
  let entry = displacedDrafts.get(workspaceKey);
  if (!entry) {
    if (displacedDrafts.size >= MAX_DRAFT_RECOVERY_WORKSPACES) displacedDrafts.delete(displacedDrafts.keys().next().value);
    entry = {drafts:[], cursor:0};
    displacedDrafts.set(workspaceKey, entry);
  }
  const last = entry.drafts.at(-1);
  if (last?.text !== draft.text || last?.placeholder !== draft.placeholder) entry.drafts.push(draft);
  if (entry.drafts.length > MAX_DRAFT_RECOVERY_VERSIONS) entry.drafts.shift();
  entry.cursor = entry.drafts.length - 1;
  return true;
}

function recoverDisplacedDraft() {
  if (busy || sessionEnded) return;
  const entry = displacedDrafts.get(draftWorkspaceKey());
  if (!entry?.drafts.length) return;
  const input = $('prompt');
  const current = {text:input.value, placeholder:input.placeholder};
  if (current.text.length + current.placeholder.length > MAX_DRAFT_RECOVERY_CHARS) {
    toast('Este rascunho excede o limite de troca; ele foi mantido no campo e a recupera\u00e7\u00e3o continua dispon\u00edvel.');
    return;
  }
  const index = entry.cursor;
  const recovered = entry.drafts[index];
  entry.drafts[index] = current;
  entry.cursor = (index - 1 + entry.drafts.length) % entry.drafts.length;
  input.value = recovered.text;
  input.placeholder = recovered.placeholder;
  delete input.dataset.commandDraft;
  delete input.dataset.commandValue;
  delete input.dataset.commandPlaceholder;
  input.focus();
  updateDraftRecoveryButton();
}

async function dispatchSubmission(text, operation = null, restoreDraft = null, resultTarget = 'assistant') {
  const reviewText = typeof restoreDraft === 'string' ? restoreDraft : text;
  const submission = {generation: ++submissionGeneration, requestId: null, terminal: false,
    resultTarget: resultTarget === 'tests' ? 'tests' : 'assistant',
    reviewText: !operation && !text.trim().startsWith('/') && reviewText.length <= MAX_DRAFT_RECOVERY_CHARS ? reviewText : null,
    workspaceKey: draftWorkspaceKey()};
  currentSubmission = submission;
  const isCurrent = () => currentSubmission === submission
    && submission.generation === submissionGeneration && !submission.terminal;
  setBusy(true);
  $('run-status').textContent = 'Enviando solicitação…';
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
    restoreDraftIfEmpty(restoreDraft);
    message('agent', `Não foi possível enviar a solicitação: ${String(error)}`);
    setBusy(false);
    $('run-status').textContent = 'Pronto';
    if (typeof offerRequestReview === 'function') offerRequestReview(submission, {reason_code:'SUBMISSION_UNCONFIRMED'});
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
  $('task-runs').replaceChildren();
  $('task-run-detail').replaceChildren();
  $('task-run-detail').hidden = true;
}

function showMainView(view, {focus = false} = {}) {
  mainView = view === 'chat' ? 'chat' : 'home';
  const isChat = mainView === 'chat';
  $('home-dashboard').hidden = isChat;
  $('conversation').hidden = !isChat;
  $('composer-wrap').hidden = !isChat;
  $('main-view-title').textContent = isChat ? 'Assistente' : 'Visão geral';
  $('scroll-latest').hidden = true;
  syncHomeActivity();
  if (typeof setActiveNav === 'function') setActiveNav(mainView);
  if (focus && isChat) $('prompt').focus();
}

function syncHomeActivity() {
  const label = $('home-active-run-label');
  const cancel = $('home-cancel-request');
  const openResult = $('home-open-result');
  $('home-active-run').hidden = !busy && !homeResultNotice;
  if (homeResultNotice) {
    label.textContent = homeResultNotice;
    cancel.hidden = true;
    openResult.hidden = false;
    openResult.textContent = homeResultTarget === 'tests' ? 'Revisar bateria' : 'Abrir resultado';
  } else {
    label.textContent = 'Solicitação em andamento.';
    cancel.hidden = !busy || !activeRequestId;
    cancel.disabled = !activeRequestId;
    openResult.hidden = true;
  }
}

function setHomeResultNotice(messageText, target = 'assistant') {
  homeResultTarget = target === 'tests' ? 'tests' : 'assistant';
  homeResultNotice = messageText;
  syncHomeActivity();
}

function openHomeResult() {
  if (homeResultTarget === 'tests' && typeof openTests === 'function') {
    void openTests();
    return;
  }
  $('nav-chat').click();
}

function triggerHomeAction(targetId, label) {
  const target = $(targetId);
  if (target.disabled) {
    toast(target.title || `${label} não está disponível nesta sessão.`);
    return;
  }
  target.click();
}

function closePanel() {
  if (typeof setFileReference === 'function') setFileReference(null);
  if (typeof setActiveNav === 'function') setActiveNav(mainView);
  panelGeneration += 1;
  panelRef = null;
  panelMode = 'artifact';
  clearPanelContent();
  $('workspace-query').hidden = true;
  $('task-history').hidden = true;
  $('report-content').hidden = false;
  $('inspector').hidden = true;
  $('resize-grip').hidden = true;
}

function addFileEntries(text) {
  const rows = text.split(/\r?\n/).flatMap(line => {
    const separator = line.indexOf(' ');
    if (separator < 0) return [];
    const type = line.slice(0, separator);
    return type === 'dir' || type === 'file' ? [{type, path: line.slice(separator + 1)}] : [];
  });
  if (!rows.length) return;
  const list = document.createElement('div');
  list.className = 'workspace-file-entries';
  for (const row of rows) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'workspace-file-entry';
    button.textContent = `${row.type === 'dir' ? '▸' : '▤'}  ${row.path}`;
    button.title = row.type === 'dir' ? `Abrir diretório ${row.path}` : `Abrir arquivo ${row.path}`;
    button.addEventListener('click', () => {
      $('query-path').value = row.path;
      $('query-kind').value = row.type === 'dir' ? 'list_files' : 'read';
      updateQueryControls();
      runWorkspaceQuery();
    });
    list.append(button);
  }
  $('report-body').append(list);
}

async function loadArtifactContent(ref, generation, offset = 0) {
  const isCurrent = () => generation === panelGeneration && panelRef?.token === ref.token;
  try {
    const result = await api().read_report(ref.token, offset);
    if (!isCurrent()) return;
    if (result.token !== ref.token || result.reference.workspace_id !== ref.workspace_id
        || result.reference.run_id !== ref.run_id || result.reference.artifact_id !== ref.artifact_id) {
      throw new Error('Identidade do artefato divergente.');
    }
    renderReport(result.text);
    if (ref.operation?.query === 'list_files') addFileEntries(result.text);
    $('copy-report').disabled = false;
    $('copy-report').dataset.text = result.text;
    $('report-integrity').textContent = [
      result.truncated ? 'Conteúdo mostrado: truncado' : 'Conteúdo mostrado: leitura sem truncamento',
      `Renderer: ${result.renderer}`,
      result.next_offset == null ? '' : `Próximo offset: ${result.next_offset}`,
      result.sha256 ? `SHA-256 calculado: ${result.sha256}` : 'Digest não disponível',
      'Integridade não verificada contra digest esperado',
    ].filter(Boolean).join(' · ');
    if (result.next_offset != null) {
      const next = document.createElement('button');
      next.type = 'button';
      next.className = 'next-artifact-chunk';
      next.textContent = 'Próximo trecho';
      next.addEventListener('click', () => loadArtifactContent(ref, generation, result.next_offset));
      $('report-body').append(next);
    }
  } catch (error) {
    if (!isCurrent()) return;
    clearPanelContent();
    $('report-integrity').textContent = 'Falha de leitura; integridade não verificada';
    renderReport(`Não foi possível carregar este artefato.\n${String(error)}`);
  }
}

function openPanel(ref) {
  const generation = ++panelGeneration;
  panelRef = ref;
  panelMode = ref.operation?.query ? 'files' : 'artifact';
  if (typeof setActiveNav === 'function') setActiveNav(panelMode);
  clearPanelContent();
  $('inspector').hidden = false;
  $('resize-grip').hidden = false;
  $('workspace-query').hidden = panelMode !== 'files';
  $('task-history').hidden = true;
  $('report-content').hidden = false;
  $('tab-title').textContent = ref.title || 'Artefato';
  $('report-heading').textContent = ref.title || 'Artefato do Agent';
  $('panel-kind-label').textContent = panelMode === 'files' ? 'CONSULTA LOCAL DO WORKSPACE'
    : ref.kind && ref.kind !== 'report' ? 'INSPEÇÃO DO WORKSPACE' : 'RELATÓRIO DE EXECUÇÃO';
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
  if (ref.operation?.query) $('query-kind').value = ref.operation.query;
  updateQueryControls();
  renderReport('Carregando artefato…');
  loadArtifactContent(ref, generation);
}

async function submit() {
  const input = $('prompt');
  const originalDraft = input.value;
  const text = input.value.trim() || (
    input.value.trim() === '' && $('welcome').style.display !== 'none'
      && input.dataset.suggestion === 'yes'
      ? 'Analise notes.txt, leia o arquivo e descreva os pontos encontrados.' : ''
  );
  if (!text) {
    input.focus();
    return;
  }
  if (sessionInfo?.llm_enabled === false && !text.startsWith('/')) {
    toast('Ative a LLM para conversar. Arquivos e Testes continuam disponíveis no uso local.');
    input.focus(); return;
  }
  if (busy) {
    if (busyActionInFlight) return;
    input.value = '';
    input.dataset.suggestion = '';
    message('user', text);
    if (typeof followConversation === 'function') followConversation();
    await submitWhileBusy(text, originalDraft || text);
    return;
  }
  input.value = '';
  input.dataset.suggestion = '';
  $('welcome').style.display = 'none';
  message('user', text);
  if (typeof followConversation === 'function') followConversation();
  await dispatchSubmission(text, null, originalDraft || text);
}

function isLocalControl(text) {
  const input = text.trim().toLocaleLowerCase();
  if (/^\/pending\s+send(?:\s|$)/i.test(input)) return false;
  if (commandEntries.length) {
    const paths = commandEntries.filter(item => item.local_control === true)
      .flatMap(item => [item.command, ...(Array.isArray(item.aliases) ? item.aliases : [])])
      .filter(path => typeof path === 'string' && path.startsWith('/'))
      .sort((a, b) => b.length - a.length);
    return paths.some(path => input === path.toLocaleLowerCase()
      || input.startsWith(`${path.toLocaleLowerCase()} `));
  }
  // Compatibility fallback for older bridges that do not return a catalog.
  return /^\/(?:status|where|timeline|events|details|cancel|exit|attention(?:\s+(?:details?|full|diff|approve|aprovar|yes|sim|deny|negar|no|nao))?|pending(?:\s+(?:edit|discard))?)\b/i.test(input);
}

async function finishExit() {
  if (sessionEnded) return;
  sessionEnded = true;
  clearDraftRecovery();
  initialized = false;
  setBusy(false);
  $('prompt').disabled = true;
  $('send').disabled = true;
  $('cancel').disabled = true;
  $('open-command-palette').disabled = true;
  for (const id of ['nav-files', 'nav-tasks', 'nav-tools', 'run-workspace-query']) $(id).disabled = true;
  $('run-status').textContent = 'Sessão encerrada';
  message('event', 'A sessão foi encerrada. Feche esta janela para sair.');
  try { Promise.resolve(api().close()).catch(error => toast(`Falha ao encerrar a sessão: ${String(error)}`)); }
  catch (error) { toast(`Falha ao encerrar a sessão: ${String(error)}`); }
}

async function submitWhileBusy(text, restoreDraft = text) {
  if (/^\/exit\b/i.test(text.trim())) {
    await finishExit();
    return;
  }
  busyActionInFlight = true;
  $('send').disabled = true;
  try {
    if (isLocalControl(text)) {
      const result = await api().control(text);
      const observation = result?.observation || result;
      if (typeof result?.draft === 'string' && $('prompt').value === '') {
        $('prompt').value = result.draft;
        $('prompt').focus();
      }
      message('agent', observation?.answer || result?.message || 'Comando concluído.');
      const rejected = result?.accepted === false || observation?.status === 'failed';
      if (rejected) {
        toast(result.message || observation.answer || 'Comando não concluído.');
        restoreDraftIfEmpty(restoreDraft);
      }
      if (/^\/cancel\b/i.test(text.trim()) && result?.accepted !== false) $('run-status').textContent = 'Cancelamento solicitado…';
      if (result?.session_exit || observation?.session_exit) {
        await finishExit();
      }
    } else {
      const result = await api().enqueue(text);
      const observation = result?.observation || result;
      if (result?.accepted === false || observation?.status === 'failed') {
        message('agent', observation?.answer || result.message || 'Não foi possível enfileirar a mensagem.');
        restoreDraftIfEmpty(restoreDraft);
      } else {
        message('event', typeof renderPendingQueue === 'function'
          ? 'Pedido guardado na fila de Tarefas. Lá você pode conferir ou remover; o envio fica disponível quando a execução atual terminar.'
          : observation?.answer || result?.message || 'Pedido adicionado à fila.');
      }
    }
  } catch (error) {
    message('agent', `Falha ao processar durante a execução: ${String(error)}`);
    restoreDraftIfEmpty(restoreDraft);
  } finally {
    busyActionInFlight = false;
    $('send').disabled = sessionEnded;
    if (busy && !sessionEnded) $('prompt').focus();
  }
}

function updateQueryControls() {
  const kind = $('query-kind').value;
  $('query-path-row').hidden = kind === 'git_status' || kind === 'diff';
  $('query-pattern-row').hidden = kind !== 'find';
  $('query-diff-row').hidden = kind !== 'diff';
  $('query-path').placeholder = kind === 'list_files' ? 'Diretório dentro do workspace' : 'Caminho do arquivo dentro do workspace';
}

function showPanel(mode, title, heading, label) {
  if (typeof setActiveNav === 'function') setActiveNav(mode);
  panelGeneration += 1;
  panelRef = null;
  panelMode = mode;
  clearPanelContent();
  $('inspector').hidden = false;
  $('resize-grip').hidden = false;
  $('tab-title').textContent = title;
  $('report-heading').textContent = heading;
  $('panel-kind-label').textContent = label;
  $('report-source').textContent = 'serviços compartilhados da aplicação';
  $('report-run').textContent = 'não associada a uma execução de modelo';
  $('report-workspace').textContent = $('workspace-name').textContent;
  $('completeness-label').textContent = 'PROJEÇÃO';
  $('report-state').textContent = 'Dados apresentados conforme disponíveis';
  $('workspace-query').hidden = mode !== 'files';
  $('task-history').hidden = mode !== 'tasks';
  $('report-content').hidden = mode === 'tasks';
  $('copy-report').disabled = true;
}

async function openFilesPanel() {
  if (busy) {
    toast('Arquivos: ocupado; aguarde o settlement da operação atual.');
    return;
  }
  if (mainView === 'chat') $('welcome').style.display = 'none';
  showPanel('files', 'Arquivos', 'Consultar workspace', 'CONSULTAS LOCAIS READ_ONLY');
  $('query-kind').value = 'list_files';
  $('query-path').value = '.';
  $('query-pattern').value = '';
  $('query-diff-paths').value = '';
  updateQueryControls();
  $('report-body').textContent = 'Escolha uma consulta para começar.';
  await runWorkspaceQuery();
}

async function runWorkspaceQuery() {
  if (busy || workspaceQueryBusy) return;
  const generation = panelGeneration;
  const kind = $('query-kind').value;
  const path = $('query-path').value.trim() || '.';
  if (typeof setFileReference === 'function') setFileReference(null);
  let argumentsValue = {};
  if (kind === 'list_files') argumentsValue = {path};
  else if (kind === 'read') argumentsValue = {file_path: path};
  else if (kind === 'find') argumentsValue = {path, pattern: $('query-pattern').value};
  else if (kind === 'diff') {
    const paths = $('query-diff-paths').value.split(/[\r\n,]+/).map(value => value.trim()).filter(Boolean);
    if (paths.length > 50) {
      toast('O diff aceita no máximo 50 caminhos por consulta.');
      return;
    }
    argumentsValue = {paths, detail: true};
  }
  workspaceQueryBusy = true;
  setBusy(true);
  $('run-status').textContent = 'Consultando workspace…';
  $('report-integrity').textContent = 'Consulta local em andamento';
  try {
    const result = await api().query_workspace(kind, argumentsValue);
    if (generation !== panelGeneration) return;
    for (const token of result.expired || []) expireArtifact(token);
    if (!result.accepted) {
      $('report-content').hidden = false;
      $('report-body').textContent = result.message || result.observation?.answer || 'Consulta indisponível.';
      $('report-state').textContent = result.observation?.reason_code || 'Consulta não concluída';
      $('report-integrity').textContent = result.observation?.status || 'Falha de consulta';
      return;
    }
    openPanel(result.reference);
    if (kind === 'read' && typeof setFileReference === 'function') {
      setFileReference(path, result.reference.workspace_id);
    }
    $('run-status').textContent = 'Consulta local concluída';
  } catch (error) {
    if (generation !== panelGeneration) return;
    $('report-content').hidden = false;
    $('report-body').textContent = `Falha de consulta: ${String(error)}`;
    $('report-integrity').textContent = 'Serviço de consulta indisponível';
  } finally {
    workspaceQueryBusy = false;
    setBusy(false);
    if (generation === panelGeneration && panelMode === 'files') $('run-status').textContent = 'Consulta de arquivos disponível';
  }
}

function currentTaskProjection(snapshot) {
  const fields = ['state', 'owner', 'run_id', 'started_at', 'last_activity_at', 'model_active',
    'current_tool', 'current_invocation_id', 'current_step', 'attention_pending', 'pending_count',
    'warning_count', 'error_count', 'terminal_outcome', 'controller_state'];
  return Object.fromEntries(fields.filter(key => key in (snapshot || {})).map(key => [key, snapshot[key]]));
}

function renderTaskStatus(snapshot) {
  if (panelMode !== 'tasks') return;
  if (typeof renderPendingQueue === 'function') renderPendingQueue(snapshot);
  const projection = currentTaskProjection(snapshot);
  $('task-current-state').textContent = JSON.stringify(projection, null, 2);
  if (typeof renderCurrentTaskSummary === 'function') renderCurrentTaskSummary(projection);
}

let taskHistoryOffset = 0;
let taskHistoryRuns = new Set();
let taskHistoryGeneration = 0;
let taskDetailGeneration = 0;

async function loadTasks(append = false) {
  const generation = append ? taskHistoryGeneration : ++taskHistoryGeneration;
  if (!append) taskDetailGeneration += 1;
  const offset = append ? taskHistoryOffset : 0;
  const result = await api().tasks(offset);
  if (generation !== taskHistoryGeneration || panelMode !== 'tasks') return;
  renderTaskStatus(result.current);
  if (!append) {
    taskHistoryOffset = 0;
    taskHistoryRuns = new Set();
    $('task-runs').replaceChildren();
  }
  const prior = $('task-runs').querySelector('#load-older-tasks');
  prior?.remove();
  const runs = Array.isArray(result.runs) ? result.runs : [];
  for (const run of runs) {
    if (!run?.run_id || taskHistoryRuns.has(run.run_id)) continue;
    taskHistoryRuns.add(run.run_id);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'task-run-entry';
    button.textContent = `${run.active ? 'Ativa' : run.status} \u00b7 ${run.run_id} \u00b7 ${run.completeness}`;
    button.title = `Abrir registro ${run.run_id} pelo servi\u00e7o de inspe\u00e7\u00e3o`;
    button.addEventListener('click', async () => {
      const detailGeneration = ++taskDetailGeneration;
      const historyGeneration = taskHistoryGeneration;
      $('task-run-detail').hidden = false;
      $('task-run-detail').textContent = 'Carregando registro\u2026';
      try {
        const detail = await api().task_details(run.run_id);
        if (detailGeneration !== taskDetailGeneration || historyGeneration !== taskHistoryGeneration || panelMode !== 'tasks') return;
        $('task-run-detail').textContent = JSON.stringify(detail, null, 2).slice(0, MAX_STREAM_CHARS);
      } catch (error) {
        if (detailGeneration !== taskDetailGeneration || historyGeneration !== taskHistoryGeneration || panelMode !== 'tasks') return;
        $('task-run-detail').textContent = `Registro indispon\u00edvel: ${String(error)}`;
      }
    });
    $('task-runs').append(button);
  }
  if (!taskHistoryRuns.size) {
    const empty = document.createElement('p');
    empty.textContent = result.history_state === 'available'
      ? 'Nenhuma execu\u00e7\u00e3o recuper\u00e1vel.' : result.history_state;
    $('task-runs').append(empty);
  }
  taskHistoryOffset = result.next_offset ?? offset + runs.length;
  if (result.next_offset != null) {
    const more = document.createElement('button');
    more.type = 'button';
    more.id = 'load-older-tasks';
    more.className = 'task-run-entry';
    more.textContent = 'Carregar execu\u00e7\u00f5es anteriores';
    more.disabled = !runs.length;
    more.addEventListener('click', () => {
      more.disabled = true;
      loadTasks(true).catch(error => {
        if (generation !== taskHistoryGeneration || panelMode !== 'tasks') return;
        more.disabled = false;
        $('task-history-note').textContent = `Falha ao carregar execu\u00e7\u00f5es anteriores: ${String(error)}`;
      });
    });
    $('task-runs').append(more);
  }
  $('task-history-note').textContent = `${result.history_state}. ${result.historical_reports_state}. Relat\u00f3rios desta sess\u00e3o ficam em Artefatos.`;
}

async function openTasksPanel() {
  if (mainView === 'chat') $('welcome').style.display = 'none';
  showPanel('tasks', 'Tarefas', 'Execuções e atividade', 'ESTADO OPERACIONAL');
  const generation = panelGeneration;
  $('task-current-summary').textContent = 'Consultando estado da execução…';
  $('task-current-state').textContent = 'Consultando estado da execução…';
  $('report-integrity').textContent = 'Estado corrente e registros locais recuperáveis';
  const taskLoad = loadTasks();
  const historyGeneration = taskHistoryGeneration;
  try {
    await taskLoad;
  } catch (error) {
    if (generation !== panelGeneration || panelMode !== 'tasks' || historyGeneration !== taskHistoryGeneration) return;
    const detail = String(error);
    $('task-current-summary').textContent = `Não foi possível consultar o estado da execução. ${detail}`;
    $('task-current-state').textContent = `Estado indisponível: ${detail}`;
  }
}

async function showArtifacts() {
  showPanel('artifacts', 'Artefatos', 'Relatórios e outputs', 'ARTEFATOS DO WORKSPACE');
  const generation = panelGeneration;
  const isCurrent = () => generation === panelGeneration && panelMode === 'artifacts';
  $('report-integrity').textContent = 'Referências originais; leituras sob demanda';
  $('report-body').replaceChildren();
  const list = document.createElement('div');
  list.className = 'workspace-file-entries';
  const add = (ref) => {
    if (!ref) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'workspace-file-entry';
    button.textContent = `${ref.title} · ${ref.kind} · ${ref.completeness}`;
    button.addEventListener('click', () => openPanel(ref));
    list.append(button);
  };
  if (latestRef) add(latestRef);
  try {
    const result = await api().outputs();
    if (!isCurrent()) return;
    for (const token of result.expired || []) expireArtifact(token);
    for (const ref of result.items || []) add(ref);
    $('artifact-count').textContent = String((latestRef ? 1 : 0) + (result.items || []).length);
    $('report-state').textContent = result.state === 'available' ? 'Outputs existentes consultados pelo OutputService' : result.state;
    if (result.message) $('report-integrity').textContent = result.message;
    if (!latestRef && !result.items?.length) {
      const empty = document.createElement('p');
      empty.textContent = result.state === 'available' ? 'Nenhum artefato disponível.' : `Artefatos: ${result.state}`;
      list.append(empty);
    }
  } catch (error) {
    if (!isCurrent()) return;
    $('report-integrity').textContent = `Falha ao consultar outputs: ${String(error)}`;
  }
  if (!isCurrent()) return;
  $('report-body').append(list);
}

async function inspectWorkspace(kind) {
  if (kind === 'files') return openFilesPanel();
  if (kind === 'tasks') return openTasksPanel();
  if (busy) return;
  showMainView('chat');
  closePanel();
  $('welcome').style.display = 'none';
  const label = 'Consultar inventário de ferramentas';
  message('user', label);
  await dispatchSubmission(label, () => api().inspect('tools'));
}

async function cancelActive() {
  if (!activeRequestId) return;
  const requestId = activeRequestId;
  $('cancel').disabled = true;
  $('home-cancel-request').disabled = true;
  $('run-status').textContent = 'Cancelamento solicitado…';
  try {
    const result = await api().cancel(requestId);
    if (activeRequestId !== requestId) return;
    if (!result.accepted) {
      toast(result.message || 'Não foi possível solicitar o cancelamento.');
      $('cancel').disabled = false;
      $('home-cancel-request').disabled = false;
    }
  } catch (error) {
    if (activeRequestId !== requestId) return;
    const detail = `Falha ao solicitar cancelamento: ${String(error)}`;
    if (mainView === 'chat') message('agent', detail);
    else toast(detail);
    $('cancel').disabled = false;
    $('home-cancel-request').disabled = false;
  }
}

function streamDelta(item) {
  if (streamedRequestId !== item.request_id) {
    streamedRequestId = item.request_id;
    streamedBubble = message('agent', '').querySelector('.bubble');
  }
  if (!streamedBubble) return;
  const conversationView = conversationMutationSnapshot();
  const current = streamedBubble.textContent;
  const remaining = Math.max(0, MAX_STREAM_CHARS - current.length);
  streamedBubble.textContent = current + item.text.slice(0, remaining);
  if ((item.truncated || item.text.length > remaining) && !streamedBubble.dataset.truncated) {
    streamedBubble.dataset.truncated = 'yes';
    streamedBubble.textContent += '\n[Prévia em fluxo limitada; a resposta final substituirá esta prévia.]';
    eventRow('Prévia de streaming truncada', item.request_id);
  }
  $('run-status').textContent = 'Recebendo resposta…';
  finishConversationMutation(conversationView);
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

function structurallyUsableReference(reference) {
  if (!reference || typeof reference !== 'object' || Array.isArray(reference)) return false;
  return ['token','title','workspace_id','artifact_id','kind']
    .every(key => typeof reference[key] === 'string' && reference[key].length > 0)
    && (reference.run_id === null || (typeof reference.run_id === 'string' && reference.run_id.length > 0));
}

function offerArtifactReference(reference, partial = false) {
  latestRef = reference;
  $('artifact-count').textContent = '1';
  $('artifact-link').disabled = false;
  const conversationView = conversationMutationSnapshot();
  const offer = document.createElement('button');
  offer.className = 'suggestion artifact-offer';
  offer.dataset.artifactToken = reference.token;
  offer.textContent = `${reference.kind !== 'report' ? '\u25ab' : '\u25a4'}  ${partial ? 'Abrir resultado parcial' : 'Abrir relat\u00f3rio'} \u00b7 ${reference.title}`;
  offer.addEventListener('click', () => openPanel(reference));
  $('messages').append(offer);
  trimHistory();
  finishConversationMutation(conversationView);
}

function partialObservationText(observation) {
  if (!observation || typeof observation !== 'object' || Array.isArray(observation)) return '';
  return [...new Set(['answer','summary'].map(key => observation[key])
    .filter(value => typeof value === 'string' && value.trim()))].join('\n\n');
}

function executionStatusLabel(status) {
  const labels = {succeeded:'Execução concluída', failed:'Execução não concluída', failure:'Execução não concluída',
    error:'Falha na execução', rejected:'Solicitação não aceita', blocked:'Execução bloqueada',
    cancelled:'Execução cancelada', canceled:'Execução cancelada', unverified:'Resultado não verificado',
    partial:'Resultado parcial'};
  return Object.hasOwn(labels, status) ? labels[status] : 'Execução encerrada';
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
      if (typeof observation.draft === 'string' && $('prompt').value === '') {
        $('prompt').value = observation.draft;
        if (mainView === 'chat') $('prompt').focus();
      }
      const answer = observation.answer || observation.summary || observation.error
        || 'A aplicação não forneceu resposta textual.';
      const rawStatus = typeof observation.status === 'string' ? observation.status : '';
      const statusCode = rawStatus.toLocaleLowerCase();
      const wasCancelled = observation.reason_code === 'INTERACTION_CANCELLED'
        || ['cancelled', 'canceled'].includes(statusCode);
      const failed = !wasCancelled && (Boolean(observation.error)
        || ['failed', 'failure', 'error', 'rejected', 'blocked'].includes(statusCode));
      const successful = !wasCancelled && !failed && statusCode === 'succeeded';
      const statusLabel = failed && !['failed','failure','error','rejected','blocked'].includes(statusCode)
        ? 'Falha na execução' : executionStatusLabel(statusCode);
      eventRow(
        wasCancelled ? 'Execu\u00e7\u00e3o cancelada' : statusLabel,
        [rawStatus && `status=${rawStatus}`, observation.reason_code].filter(Boolean).join(' · '),
        wasCancelled ? 'warning' : failed ? 'error' : ['unverified','partial'].includes(statusCode) ? 'warning' : successful ? 'success' : 'neutral',
      );
      if (streamedRequestId === item.request_id && streamedBubble) {
        streamedBubble.textContent = answer;
      } else {
        message('agent', answer);
      }
      streamedRequestId = null;
      streamedBubble = null;
      const usableReference = structurallyUsableReference(item.reference) ? item.reference : null;
      if (usableReference) {
        $('run-status').textContent = usableReference.kind && usableReference.kind !== 'report' ? 'Inspe\u00e7\u00e3o dispon\u00edvel' : 'Relat\u00f3rio dispon\u00edvel';
        offerArtifactReference(usableReference);
      }
      if (wasCancelled) {
        $('run-status').textContent = usableReference
          ? 'Execu\u00e7\u00e3o cancelada \u00b7 Relat\u00f3rio dispon\u00edvel' : 'Execu\u00e7\u00e3o cancelada';
      } else if (failed) {
        $('run-status').textContent = `${statusLabel}${usableReference ? ' \u00b7 relat\u00f3rio dispon\u00edvel' : ''}`;
      } else if (['unverified','partial'].includes(statusCode)) {
        $('run-status').textContent = `${executionStatusLabel(statusCode)}${usableReference ? ' · relatório disponível' : ''}`;
      } else if (!usableReference) {
        $('run-status').textContent = rawStatus ? executionStatusLabel(statusCode) : 'Pronto';
      }
      const resultTarget = currentSubmission?.resultTarget || 'assistant';
      setHomeResultNotice(resultTarget === 'tests'
        ? `Bateria encerrada. Estado reportado: ${rawStatus || 'não informado'}. Revise as verificações no painel Testes.`
        : `Execução encerrada. Estado reportado: ${rawStatus || 'não informado'}. Abra o Assistente para revisar o conteúdo e o relatório.`, resultTarget);
      if ((wasCancelled || failed || ['unverified','partial'].includes(statusCode)) && typeof offerRequestReview === 'function') {
        offerRequestReview(currentSubmission, observation);
      }
      if (observation.session_exit) void finishExit();
    } else if (item.type === 'error') {
      if (!finishSubmission(item.request_id)) continue;
      const settlement = item.settlement && typeof item.settlement === 'object' && !Array.isArray(item.settlement)
        ? item.settlement : {};
      const observation = settlement.observation && typeof settlement.observation === 'object' && !Array.isArray(settlement.observation)
        ? settlement.observation : null;
      const terminalDetails = [
        typeof observation?.status === 'string' ? `status=${observation.status}` : '',
        typeof observation?.reason_code === 'string' ? `reason_code=${observation.reason_code}` : '',
      ].filter(Boolean).join(' \u00b7 ');
      eventRow('Falha na execu\u00e7\u00e3o', terminalDetails, 'error');
      const partialText = partialObservationText(observation);
      const streamText = streamedRequestId === item.request_id && typeof streamedBubble?.textContent === 'string'
        ? streamedBubble.textContent : '';
      if (partialText || streamText.trim()) {
        const label = partialText && streamText.trim() ? 'Pr\u00e9via de fluxo e resultado parcial preservados'
          : partialText ? 'Resultado parcial preservado' : 'Pr\u00e9via de fluxo preservada como parcial';
        eventRow(label, item.request_id, 'warning');
      }
      if (partialText && streamText !== partialText) message('agent', partialText);
      if (typeof item.message === 'string' && item.message && item.message !== partialText && item.message !== streamText) message('agent', item.message);
      const reference = structurallyUsableReference(settlement.reference) ? settlement.reference : null;
      if (reference) offerArtifactReference(reference, true);
      if (typeof offerRequestReview === 'function') offerRequestReview(currentSubmission, observation || {});
      $('run-status').textContent = reference
        ? 'Falha na execu\u00e7\u00e3o \u00b7 resultado parcial dispon\u00edvel'
        : 'Falha na execu\u00e7\u00e3o';
      const resultTarget = currentSubmission?.resultTarget || 'assistant';
      setHomeResultNotice(resultTarget === 'tests'
        ? 'A bateria teve uma falha. Reabra Testes para revisar o estado e as evidências.'
        : 'Falha na execução. Abra o Assistente para revisar a mensagem e possíveis resultados parciais.', resultTarget);
      if (streamedRequestId === item.request_id) {
        streamedRequestId = null;
        streamedBubble = null;
      }
    }
  }
}

async function pollEvents() {
  let failures = 0;
  while (initialized) {
    try {
      const events = await api().wait_events(1000);
      consume(events);
      if (busy && activeRequestId) await pollAttention();
      if (events.some(e => e.type === "settled" || e.type === "error") && api().initialize) {
        const requestId = ++availabilityRequestId;
        renderSessionInfo(await api().initialize(), requestId);
        await pollAttention();
      }
      if (events.some(e => e.type === "settled" || e.type === "error") && api().availability) await refreshAvailability();
      if (panelMode === 'tasks' && api().task_status) renderTaskStatus(await api().task_status());
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

function renderSessionInfo(info, requestId = ++availabilityRequestId) {
  const previousWorkspace = sessionInfo?.workspace_id;
  const previousDraftWorkspace = draftWorkspaceKey();
  const staleAvailability = requestId < appliedAvailabilityRequestId;
  sessionInfo = {...(info || {}), availability: staleAvailability
    ? sessionInfo?.availability : (info?.availability || sessionInfo?.availability)};
  info = sessionInfo;
  if (previousDraftWorkspace && draftWorkspaceKey(info) !== previousDraftWorkspace) clearDraftRecovery();
  commandEntries = Array.isArray(info?.commands) ? info.commands : [];
  if (typeof syncNavigationChoice === 'function') syncNavigationChoice();
  if (typeof syncPluginControls === 'function') syncPluginControls();
  if (previousWorkspace && info?.workspace_id && previousWorkspace !== info.workspace_id) {
    latestRef = null;
    $('messages').querySelectorAll('.artifact-offer').forEach(item => item.remove());
    $('artifact-count').textContent = '0';
    closePanel();
  }
  $('workspace-name').textContent = info.workspace_name;
  $('workspace-name-status').textContent = info.workspace_name;
  $('report-workspace').textContent = info.workspace_name;
  $('home-workspace-name').textContent = info.workspace_name || 'Projeto sem nome';
  $('home-workspace-root').textContent = info.workspace_root || 'Pasta do projeto não informada';
  $('home-workspace-root').title = info.workspace_root || '';
  const mode = typeof info.mode === 'string' && info.mode ? info.mode : null;
  const modeLabels = {READ_ONLY:'Somente leitura', configured_endpoint:'Modelo configurado'};
  const modeLabel = mode ? (modeLabels[mode] || mode.replaceAll('_', ' ')) : 'Modo não informado';
  $('session-mode-label').textContent = modeLabel;
  $('session-mode-label').title = `Modo da sessao: ${mode || 'sem código'}`;
  const configured = Boolean(info.availability?.backend?.configured);
  $('model-availability').dataset.configured = configured ? 'yes' : 'no';
  for (const [id, kind] of [['nav-files','files'], ['nav-tasks','tasks'], ['nav-tools','tools']]) {
    const supported = Array.isArray(info.inspection_kinds) && info.inspection_kinds.includes(kind);
    const available = supported && (kind !== 'tools' || configured);
    $(id).dataset.available = available ? 'yes' : 'no';
    $(id).disabled = sessionEnded || !available || (busy && kind !== 'tasks');
    $(id).title = available ? `Consultar ${kind === 'files' ? 'arquivos' : kind === 'tasks' ? 'execuções' : 'inventário de ferramentas'} pelos serviços compartilhados`
      : kind === 'tools' && !configured ? 'Não configurado: o inventário do runtime precisa de um perfil existente.' : 'Não implementado nesta sessão.';
    $(`${id}-availability`).textContent = available ? 'Disponível' : kind === 'tools' && !configured ? 'Não configurado' : 'Não implementado';
  }
  $('session-mode').textContent = `${modeLabel} · ${configured ? 'perfil carregado' : 'sem modelo configurado'}`;
  const profile = info.profile;
  $('profile-menu').disabled = sessionEnded;
  $('profile-menu').title = profile
    ? `Perfil de modelo: ${profile.name} \u00b7 ${profile.provider} \u00b7 ${profile.model}. Abrir op\u00e7\u00f5es da sess\u00e3o.`
    : 'Nenhum perfil de modelo configurado. Abrir op\u00e7\u00f5es da sess\u00e3o.';
  $('workspace-switch').disabled = sessionEnded;
  $('workspace-switch').setAttribute('aria-controls', typeof api().navigation_options === 'function'
    ? 'navigation-dialog' : 'command-palette-backdrop');
  $('model-name').textContent = profile?.model || 'não configurado';
  $('model-availability').textContent = configured ? 'Conectividade não verificada · Testar conexão' : 'Modelo não configurado';
  $('model-availability').title = configured
    ? `Perfil ${profile.name} (${profile.provider}); modelo ${profile.model}. Teste manual consulta somente /models.`
    : 'Não configurado: testar conexão não fará operação de rede.';
  $('artifact-link').disabled = sessionEnded;
  const availabilityProjection = info.availability || {backend:{},resources:{}};
  renderAvailability(availabilityProjection, staleAvailability ? appliedAvailabilityRequestId : requestId);
  if (typeof syncTestsControls === 'function') syncTestsControls();
  if (typeof syncLlmControls === 'function') syncLlmControls();
  updateDraftRecoveryButton();
  if (typeof syncFileReference === 'function') syncFileReference();
  updateDraftRecoveryButton();
}

function openCommandPalette() {
  if (sessionEnded) return;
  if (!commandEntries.length) {
    toast('Nenhum comando está disponível nesta sessão.');
    return;
  }
  $('command-palette-backdrop').hidden = false;
  $('command-palette-title').textContent = 'Comandos';
  commandScope = null;
  $('command-filter').value = '';
  renderCommandPalette();
  $('command-filter').focus();
}

function closeCommandPalette() {
  $('command-palette-backdrop').hidden = true;
  if (mainView === 'chat') $('prompt').focus();
  else $('nav-home').focus();
}

function renderCommandPalette() {
  const host = $('command-list');
  const query = $('command-filter').value.trim().toLocaleLowerCase();
  host.replaceChildren();
  const matches = commandEntries.filter(item => {
    if (commandScope && !commandScope(item)) return false;
    const text = [item.command, item.title, item.description, item.payload_hint, item.action_id].filter(Boolean).join(' ').toLocaleLowerCase();
    return text.includes(query);
  });
  for (const item of matches.slice(0, 100)) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'command-option';
    const command = document.createElement('code');
    command.textContent = item.command || item.action_id || '';
    const title = document.createElement('b');
    title.textContent = item.title || item.action_id || item.command || 'Comando';
    const description = document.createElement('small');
    const modelHint = item.action_id === 'interaction.code_submit' ? 'Análise e revisão estáticas: sem LLM. Alterações: com LLM.'
      : item.model_use === 'NEVER' ? 'Não usa LLM.' : item.model_use === 'AGENTIC_ONLY' ? 'Fluxo agêntico; consulte os requisitos da ação.' : '';
    description.textContent = [item.description || item.payload_hint || item.busy_policy || '',modelHint].filter(Boolean).join(' ');
    button.append(command, title, description);
    button.addEventListener('click', () => {
      const prefix = String(item.command || '');
      const input = $('prompt');
      const replacement = prefix ? `${prefix}${item.payload_hint ? ' ' : ''}` : '';
      const replacementPlaceholder = item.payload_hint || 'Pe\u00e7a uma an\u00e1lise ou descreva o trabalho\u2026';
      const displaced = {text:input.value, placeholder:input.placeholder};
      const untouchedCommand = input.dataset.commandDraft === 'yes'
        && input.value === input.dataset.commandValue && input.placeholder === input.dataset.commandPlaceholder;
      if (displaced.text && (displaced.text !== replacement || displaced.placeholder !== replacementPlaceholder) && !untouchedCommand) {
        const workspaceKey = draftWorkspaceKey();
        if (!workspaceKey) {
          toast('Escolha um workspace antes de substituir este rascunho.');
          return;
        }
        if (!rememberDisplacedDraft(workspaceKey, displaced)) {
          toast('Este rascunho excede o limite de recupera\u00e7\u00e3o; ele foi mantido no campo.');
          return;
        }
      }
      showMainView('chat');
      input.value = replacement;
      input.placeholder = replacementPlaceholder;
      input.dataset.commandDraft = 'yes';
      input.dataset.commandValue = input.value;
      input.dataset.commandPlaceholder = input.placeholder;
      updateDraftRecoveryButton();
      closeCommandPalette();
    });
    host.append(button);
  }
  if (!matches.length) {
    const empty = document.createElement('small');
    empty.textContent = 'Nenhum comando corresponde ao filtro.';
    host.append(empty);
  }
}

function attentionKey(item) {
  if (!item) return '';
  return `${item.attention_id}:${item.run_generation}:${item.request_fingerprint}`;
}

function renderAttention(value) {
  const card = $('approval-card');
  if (!value || value.state === 'none' || value.pending === false || value.available === false) {
    card.hidden = true;
    approvalIdentity = null;
    approvalResolving = false;
    $('approval-diff').hidden = true;
    return;
  }
  const item = value.attention || value.current || value;
  const key = attentionKey(item);
  if (item.attention_id == null || !item.request_fingerprint || item.run_generation == null) {
    card.hidden = true;
    approvalIdentity = null;
    approvalResolving = false;
    return;
  }
  if (approvalIdentity && attentionKey(approvalIdentity) !== key) {
    approvalResolving = false;
    $('approval-diff').hidden = true;
  }
  approvalIdentity = item;
  const metadata = item.metadata || {};
  $('approval-action').textContent = item.action || 'Ação aguardando aprovação';
  $('approval-resource').textContent = item.resource || '';
  $('approval-prompt').textContent = item.prompt || '';
  $('approval-diff').textContent = metadata.proposed_diff
    ? `${metadata.proposed_diff}${metadata.proposed_diff_truncated ? '\n[Diff truncado; confira o digest original nos metadados.]' : ''}`
    : 'Nenhum diff foi anexado a esta solicitação.';
  $('approval-details').hidden = !metadata.proposed_diff;
  card.hidden = false;
  for (const id of ['approval-approve', 'approval-deny']) $(id).disabled = approvalResolving;
}

async function resolveAttention(decision) {
  if (!approvalIdentity || approvalResolving) return;
  const selected = approvalIdentity;
  const key = attentionKey(selected);
  approvalResolving = true;
  for (const id of ['approval-approve', 'approval-deny']) $(id).disabled = true;
  try {
    const result = await api().resolve_attention(selected.attention_id, decision,
      selected.run_generation, selected.request_fingerprint);
    if (!approvalIdentity || attentionKey(approvalIdentity) !== key) return;
    if (result?.accepted === false || result?.state === 'stale') {
      toast(result.message || 'Esta solicitação de aprovação já não está ativa.');
      approvalResolving = false;
      await pollAttention();
      return;
    }
    toast(decision === 'approve' ? 'Aprovação registrada.' : 'Solicitação negada.');
    await pollAttention();
  } catch (error) {
    if (approvalIdentity && attentionKey(approvalIdentity) === key) {
      approvalResolving = false;
      for (const id of ['approval-approve', 'approval-deny']) $(id).disabled = false;
      toast(`Falha ao registrar decisão: ${String(error)}`);
    }
  }
}

async function pollAttention() {
  if (!api().attention) return;
  try { renderAttention(await api().attention()); }
  catch (error) { $('approval-card').dataset.error = String(error); }
}

window.addEventListener('pywebviewready', async () => {
  try {
    const requestId = ++availabilityRequestId;
    const info = await api().initialize();
    renderSessionInfo(info, requestId);
    if (api().availability) await refreshAvailability();
    await pollAttention();
    initialized = true;
    if (typeof syncTestsControls === 'function') syncTestsControls();
  if (typeof syncLlmControls === 'function') syncLlmControls();
    pollEvents();
  } catch (error) {
    message('agent', `Falha ao inicializar sessão: ${String(error)}`);
    setBusy(false);
    $('run-status').textContent = 'Falha ao iniciar';
  }
});

$('send').addEventListener('click', submit);
$('open-command-palette').addEventListener('click', openCommandPalette);
$('restore-draft').addEventListener('click', recoverDisplacedDraft);
$('prompt').addEventListener('input', () => {
  delete $('prompt').dataset.commandDraft;
  delete $('prompt').dataset.commandValue;
  delete $('prompt').dataset.commandPlaceholder;
  updateDraftRecoveryButton();
});
$('command-filter').addEventListener('input', renderCommandPalette);
$('command-palette-backdrop').addEventListener('click', event => {
  if (event.target === $('command-palette-backdrop')) closeCommandPalette();
});
$('command-filter').addEventListener('keydown', event => {
  if (event.key === 'Escape') closeCommandPalette();
  if (event.key === 'Enter') $('command-list').querySelector('.command-option')?.click();
});
document.addEventListener('keydown', event => {
  if (((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') || event.key === 'F2') {
    event.preventDefault();
    openCommandPalette();
  }
  if (event.key === 'Escape' && !$('command-palette-backdrop').hidden) closeCommandPalette();
});
$('approval-details').addEventListener('click', () => {
  $('approval-diff').hidden = !$('approval-diff').hidden;
});
$('approval-approve').addEventListener('click', () => resolveAttention('approve'));
$('approval-deny').addEventListener('click', () => resolveAttention('deny'));
$('cancel').addEventListener('click', cancelActive);
$('home-cancel-request').addEventListener('click', cancelActive);
$('home-open-result').addEventListener('click', openHomeResult);
$('nav-home').addEventListener('click', () => { closePanel(); showMainView('home'); });
$('nav-files').addEventListener('click', () => inspectWorkspace('files'));
$('nav-tasks').addEventListener('click', () => inspectWorkspace('tasks'));
$('nav-tools').addEventListener('click', () => inspectWorkspace('tools'));
$('nav-chat').addEventListener('click', () => { closePanel(); showMainView('chat', {focus:true}); });
$('home-switch-project').addEventListener('click', () => triggerHomeAction('workspace-switch', 'Trocar projeto'));
$('home-open-files').addEventListener('click', () => triggerHomeAction('nav-files', 'Arquivos'));
$('home-open-tasks').addEventListener('click', () => triggerHomeAction('nav-tasks', 'Tarefas'));
$('home-open-tests').addEventListener('click', () => triggerHomeAction('open-tests', 'Baterias de testes'));
$('home-open-plugins').addEventListener('click', () => triggerHomeAction('open-plugins', 'Plugins'));
$('home-open-terminal').addEventListener('click', () => {
  closePanel();
  showMainView('chat');
  openScopedCommands('Comando local', item => item.action_id === 'process.exec');
});
$('home-open-assistant').addEventListener('click', () => $('nav-chat').click());
$('shortcuts').addEventListener('click', () => toast('Ctrl+Enter: enviar · Enter: nova linha · Ctrl+K ou F2: comandos · Esc: fechar comandos'));
$('prompt').addEventListener('keydown', event => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    if (event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    submit();
  }
});
$('theme-toggle').addEventListener('click', () => {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
});
$('suggestion').addEventListener('click', () => {
  openFilesPanel();
});
$('collapse-nav').addEventListener('click', () => toggleNavigation());
$('more-resources-toggle').addEventListener('click', () => {
  const expanded = $('more-resources-toggle').getAttribute('aria-expanded') !== 'true';
  $('more-resources-toggle').setAttribute('aria-expanded', String(expanded));
  $('more-resources-panel').hidden = !expanded;
});
$('close-panel').addEventListener('click', closePanel);
$('artifact-link').addEventListener('click', showArtifacts);
$('refresh-artifacts').addEventListener('click', showArtifacts);
$('refresh-tasks').addEventListener('click', () => loadTasks().catch(error => {
  $('task-history-note').textContent = `Falha ao atualizar execu\u00e7\u00f5es: ${String(error)}`;
}));
$('query-kind').addEventListener('change', updateQueryControls);
$('run-workspace-query').addEventListener('click', runWorkspaceQuery);
$('query-pattern').addEventListener('keydown', event => {
  if (event.key === 'Enter') runWorkspaceQuery();
});
$('query-path').addEventListener('keydown', event => {
  if (event.key === 'Enter') runWorkspaceQuery();
});
$('copy-report').addEventListener('click', () => copyReportContent());

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

const availabilityLabels = {available:'Dispon\u00edvel',busy:'Ocupado',not_configured:'N\u00e3o configurado',not_implemented:'N\u00e3o implementado',forbidden:'Sem permiss\u00e3o',temporarily_unavailable:'Temporariamente indispon\u00edvel',unavailable:'Indispon\u00edvel',unverified:'N\u00e3o verificado'};
const backendAvailabilityLabels = {available:'Cat\u00e1logo respondeu',busy:'Ocupado',not_configured:'Modelo n\u00e3o configurado',not_implemented:'N\u00e3o implementado',forbidden:'Sem permiss\u00e3o',temporarily_unavailable:'Tempo esgotado na \u00faltima execu\u00e7\u00e3o',unavailable:'Falha de conex\u00e3o na \u00faltima execu\u00e7\u00e3o',unverified:'Cat\u00e1logo n\u00e3o verificado'};
async function refreshAvailability() {
  if (!api().availability) return;
  const requestId = ++availabilityRequestId;
  renderAvailability(await api().availability(), requestId);
}
function renderAvailability(projection, requestId = ++availabilityRequestId) {
  if (!projection || typeof projection !== 'object') return;
  if (requestId < appliedAvailabilityRequestId) return;
  appliedAvailabilityRequestId = requestId;
  const previousAvailability = sessionInfo?.availability;
  const reportedBackend = projection.backend && typeof projection.backend === 'object' ? projection.backend : {};
  const previousConfigured = previousAvailability?.backend?.configured;
  const reportedConfigured = reportedBackend.configured;
  const configuredKnown = reportedConfigured === true || reportedConfigured === false || previousConfigured === true || previousConfigured === false;
  const configured = reportedConfigured === true || (reportedConfigured !== false && previousConfigured === true);
  const backend = {...(previousAvailability?.backend || {}), ...reportedBackend,
    ...(reportedConfigured === true || reportedConfigured === false ? {} : (previousConfigured === true || previousConfigured === false ? {configured:previousConfigured} : {}))};
  const effectiveProjection = {
    ...projection,
    backend,
    resources: {...(previousAvailability?.resources || {}), ...(projection.resources && typeof projection.resources === 'object' ? projection.resources : {})},
  };
  if (sessionInfo) sessionInfo.availability = effectiveProjection;
  const stateLabel = backendAvailabilityLabels[backend.state] || `Estado desconhecido (${backend.state || 'sem c\u00f3digo'})`;
  $('model-health').textContent = !configuredKnown || !configured ? 'Servidor n\u00e3o verificado'
    : backend.connectivity === 'verified' ? 'Servidor respondeu; gera\u00e7\u00e3o n\u00e3o testada'
    : backend.connectivity === 'failed' ? 'Falha de conex\u00e3o observada' : 'Servidor n\u00e3o verificado';
  $('model-health').title = 'A verifica\u00e7\u00e3o consulta o cat\u00e1logo; n\u00e3o gera texto e n\u00e3o prova que uma gera\u00e7\u00e3o funcionaria.';
  const connectivityLabel = backend.connectivity === 'verified' ? 'Cat\u00e1logo respondeu'
    : backend.connectivity === 'failed' ? 'Falha de conex\u00e3o observada na \u00faltima execu\u00e7\u00e3o' : 'Cat\u00e1logo n\u00e3o consultado';
  const readinessLabel = backend.model_readiness === 'listed' ? 'Modelo encontrado no cat\u00e1logo; gera\u00e7\u00e3o n\u00e3o testada'
    : 'Presen\u00e7a do modelo no cat\u00e1logo n\u00e3o confirmada';
  $('model-availability').dataset.configured = configured ? 'yes' : configuredKnown ? 'no' : 'unknown';
  $('model-availability').textContent = !configuredKnown ? 'Estado do modelo indispon\u00edvel'
    : backend.state === 'not_implemented' ? 'Teste do cat\u00e1logo indispon\u00edvel'
    : configured ? `${stateLabel} \u00b7 Consultar cat\u00e1logo` : 'Modelo n\u00e3o configurado';
  $('model-availability').title = configured
    ? `${connectivityLabel}. ${readinessLabel}. A verifica\u00e7\u00e3o consulta /models em segundo plano, sem gerar texto. C\u00f3digos: estado=${backend.state || 'ausente'}, conectividade=${backend.connectivity || 'ausente'}, prontid\u00e3o=${backend.model_readiness || 'ausente'}, motivo=${backend.reason || 'ausente'}, gera\u00e7\u00e3o=${backend.generation ?? 'ausente'}. Consultar cat\u00e1logo faz uma solicita\u00e7\u00e3o /models; n\u00e3o testa gera\u00e7\u00e3o.`
    : !configuredKnown ? `A bridge n\u00e3o informou se h\u00e1 um perfil. C\u00f3digo: ${backend.reason || backend.state || 'ausente'}.` : `Modelo n\u00e3o configurado. C\u00f3digo: estado=${backend.state || 'ausente'}, motivo=${backend.reason || 'ausente'}.`;
  $('model-availability').setAttribute('aria-label', `${$('model-availability').textContent}. ${$('model-availability').title}`);
  $('model-availability').disabled = sessionEnded || !configured || backend.state === 'not_implemented' || busy || connectionProbeInFlight;
  const resources = effectiveProjection.resources;
  for (const [id, kind] of [['nav-files','files'],['nav-tasks','tasks'],['nav-tools','tools']]) {
    const reported = resources[kind];
    const state = typeof reported === 'string' ? reported : null;
    if (typeof state !== 'string') {
      $(id).dataset.available ||= 'unknown';
      $(id).title = 'Estado de disponibilidade n\u00e3o informado pela bridge.';
      $(`${id}-availability`).textContent = 'Estado desconhecido';
      $(id).disabled = sessionEnded || $(id).dataset.available === 'no' || (busy && kind !== 'tasks');
      continue;
    }
    const available = ['available', 'busy', 'unverified'].includes(state);
    $(id).dataset.available = available ? 'yes' : 'no';
    $(id).title = `${availabilityLabels[state] || `Estado desconhecido (${state})`}. ${kind === 'tools' && state === 'not_configured'
      ? 'Configure um perfil para consultar o invent\u00e1rio do runtime.' : 'Recurso local pelos servi\u00e7os compartilhados.'}`;
    $(id).disabled = sessionEnded || !available || (busy && kind !== 'tasks');
    $(`${id}-availability`).textContent = availabilityLabels[state] || 'Estado desconhecido';
  }
  $('run-workspace-query').disabled = sessionEnded || busy || $('nav-files').dataset.available !== 'yes';
  if (typeof renderSessionSummary === 'function' && $('session-dialog')?.open) renderSessionSummary();
}
$('model-availability').addEventListener('click', async () => {
  if (!initialized || busy || connectionProbeInFlight) return;
  connectionProbeInFlight = true;
  const requestId = ++availabilityRequestId;
  $('model-availability').disabled = true;
  try { renderAvailability(await api().test_connection(), requestId); }
  catch (error) { toast(`Teste de conex\u00e3o: ${String(error)}`); }
  finally {
    connectionProbeInFlight = false;
    if (busy) {
      $('model-availability').disabled = true;
    } else {
      try {
        if (api().availability) await refreshAvailability();
        else if (sessionInfo?.availability) renderAvailability(sessionInfo.availability, ++availabilityRequestId);
        else $('model-availability').disabled = sessionEnded || $('model-availability').dataset.configured !== 'yes';
      } catch (error) {
        toast(`N\u00e3o foi poss\u00edvel atualizar o estado do modelo: ${String(error)}. O \u00faltimo resultado conhecido foi mantido.`);
        if (sessionInfo?.availability) renderAvailability(sessionInfo.availability, ++availabilityRequestId);
        else $('model-availability').disabled = sessionEnded || $('model-availability').dataset.configured !== 'yes';
      }
    }
  }
});
