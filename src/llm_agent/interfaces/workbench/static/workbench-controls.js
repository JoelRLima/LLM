// Small local controls over the existing session, query and command owners.
let fileReference = null;

function requestReviewGuidance(observation) {
  const status = String(observation.status || '').toLowerCase();
  if (['cancelled', 'canceled'].includes(status) || observation.reason_code === 'INTERACTION_CANCELLED') {
    return 'A execução foi interrompida. Confira o resultado parcial antes de continuar.';
  }
  if (['unverified', 'partial'].includes(status)) {
    return 'O resultado ainda não está confirmado. Confira o relatório antes de pedir outra ação.';
  }
  const reasons = {
    MODEL_CONNECTION_ERROR:'Não foi possível manter a conexão com o modelo. Confira a sessão e o resultado parcial antes de reenviar.',
    MODEL_TIMEOUT_ERROR:'O modelo não respondeu dentro do prazo. Confira Tarefas e o resultado parcial antes de reenviar.',
    MODEL_OUTPUT_INCOMPLETE:'A resposta do modelo foi cortada pelo limite de saída. Confira o resultado parcial e revise o pedido.',
    SUBMISSION_UNCONFIRMED:'Não foi possível confirmar o envio. Confira Tarefas antes de enviar o pedido novamente.',
  };
  return Object.hasOwn(reasons, observation.reason_code) ? reasons[observation.reason_code]
    : 'O pedido não foi concluído. Confira o relatório, quando disponível, e revise a mensagem antes de enviar novamente.';
}

function reviewRequestDraft(submission) {
  if (busy || sessionEnded) return;
  if (!submission.workspaceKey || submission.workspaceKey !== draftWorkspaceKey()) {
    toast('Este pedido pertence a outro projeto. Volte ao projeto original para revisá-lo.');
    return;
  }
  const input = $('prompt');
  if (input.value && input.value !== submission.reviewText) {
    const preserved = rememberDisplacedDraft(submission.workspaceKey, {text:input.value, placeholder:input.placeholder});
    if (!preserved) {
      toast('Seu rascunho foi mantido. Salve o texto atual antes de recuperar este pedido.');
      return;
    }
  }
  input.value = submission.reviewText;
  delete input.dataset.commandDraft;
  delete input.dataset.commandValue;
  delete input.dataset.commandPlaceholder;
  $('welcome').style.display = 'none';
  updateDraftRecoveryButton();
  input.focus();
  toast('Pedido recuperado para revisão. Nada foi reenviado.');
}

function offerRequestReview(submission, observation) {
  if (!submission?.reviewText || !submission.workspaceKey) return;
  const view = conversationMutationSnapshot();
  const card = document.createElement('div');
  card.className = 'request-recovery';
  const explanation = document.createElement('p');
  explanation.textContent = requestReviewGuidance(observation);
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'request-review-button';
  button.textContent = 'Revisar pedido';
  button.disabled = busy || sessionEnded;
  button.addEventListener('click', () => reviewRequestDraft(submission));
  const note = document.createElement('small');
  note.textContent = 'Preenche a mensagem para você editar. O envio continua sendo sua escolha.';
  card.append(explanation, button, note);
  $('messages').append(card);
  trimHistory();
  finishConversationMutation(view);
}

function setFileReference(path, workspaceId = null) {
  fileReference = path ? {path, workspaceId} : null;
  syncFileReference();
}

function syncFileReference() {
  $('use-file-in-chat').disabled = !fileReference || busy || sessionEnded;
  $('analyze-file-locally').disabled = !fileReference || busy || sessionEnded;
  $('attach-file').disabled = busy || sessionEnded;
}

async function addWorkspaceFile() {
  if (!initialized || busy || sessionEnded) return;
  await openFilesPanel();
  if (panelMode !== 'files' || sessionEnded) return;
  $('query-kind').value = 'read';
  $('query-path').value = '';
  updateQueryControls();
  $('workspace-query-help').textContent = 'Abra um arquivo textual para adicionar o caminho à sua mensagem.';
  $('query-path').focus();
}

function useFileInChat() {
  if (!fileReference || busy || sessionEnded) return;
  if (fileReference.workspaceId && fileReference.workspaceId !== sessionInfo?.workspace_id) {
    setFileReference(null);
    toast('Escolha um arquivo do workspace atual.');
    return;
  }
  const instruction = `Leia o arquivo ${JSON.stringify(fileReference.path)}.`;
  const input = $('prompt');
  input.value = input.value ? `${input.value}\n${instruction}` : instruction;
  if (typeof showMainView === 'function') showMainView('chat');
  closePanel();
  $('welcome').style.display = 'none';
  input.focus();
  toast('Referência adicionada. Revise a mensagem antes de enviar.');
}

async function analyzeFileLocally() {
  if (!fileReference || busy || sessionEnded) return;
  if (fileReference.workspaceId && fileReference.workspaceId !== sessionInfo?.workspace_id) {
    setFileReference(null); toast('Escolha um arquivo do projeto atual.'); return;
  }
  const path = fileReference.path;
  const target = path.startsWith('-') ? `./${path}` : path;
  const command = `/code analyze ${JSON.stringify(target)}`;
  if (typeof showMainView === 'function') showMainView('chat');
  closePanel();
  $('welcome').style.display = 'none';
  message('user', `Análise estática sem LLM: ${path}`);
  await dispatchSubmission(command, () => api().submit(command));
}

function toggleReportWrap() {
  const nowrap = $('inspector').classList.toggle('no-wrap');
  $('report-options').setAttribute('aria-pressed', String(!nowrap));
  toast(nowrap ? 'Quebra de linhas desativada' : 'Quebra de linhas ativada');
}

function toggleNavigation() {
  const collapsed = $('shell').classList.toggle('nav-collapsed');
  $('collapse-nav').textContent = collapsed ? '›' : '‹';
  $('collapse-nav').title = collapsed ? 'Expandir navegação' : 'Recolher navegação';
  $('collapse-nav').setAttribute('aria-expanded', String(!collapsed));
}

function openScopedCommands(title, scope) {
  if (sessionEnded) return;
  openCommandPalette();
  if ($('command-palette-backdrop').hidden) return;
  $('command-palette-title').textContent = title;
  commandScope = scope;
  renderCommandPalette();
}

function openSessionCommands() {
  openScopedCommands('Sessão e perfis', item => item.action_id?.startsWith('model.')
    || item.action_id?.startsWith('workspace.') || item.action_id?.startsWith('configuration.mode'));
}

let conversationFollowing = true;
const CONVERSATION_END_TOLERANCE = 48;

function isConversationAtEnd() {
  const scroller = $('conversation');
  const height = Number(scroller.scrollHeight);
  const viewport = Number(scroller.clientHeight);
  const top = Number(scroller.scrollTop);
  if (!Number.isFinite(height) || !Number.isFinite(viewport) || !Number.isFinite(top) || viewport <= 0) {
    return conversationFollowing;
  }
  if (height <= viewport + CONVERSATION_END_TOLERANCE) return true;
  return height - top - viewport <= CONVERSATION_END_TOLERANCE;
}

function syncScrollLatest() {
  conversationFollowing = isConversationAtEnd();
  $('scroll-latest').hidden = conversationFollowing;
}

function visibleConversationAnchor() {
  const scroller = $('conversation');
  const host = $('messages');
  if (typeof scroller.getBoundingClientRect !== 'function') return null;
  const viewport = scroller.getBoundingClientRect();
  for (const row of Array.from(host.children || [])) {
    if (typeof row.getBoundingClientRect !== 'function') continue;
    const rect = row.getBoundingClientRect();
    if (rect.bottom > viewport.top && rect.top < viewport.bottom) return {row, top: rect.top - viewport.top};
  }
  return null;
}

function captureConversationState() {
  const scroller = $('conversation');
  const following = isConversationAtEnd();
  conversationFollowing = following;
  $('scroll-latest').hidden = following;
  return {
    following,
    scrollTop: Number(scroller.scrollTop) || 0,
    anchor: following ? null : visibleConversationAnchor(),
  };
}

function completeConversationMutation(snapshot) {
  const scroller = $('conversation');
  if (snapshot?.following) {
    followConversation();
    return;
  }
  if (snapshot?.anchor?.row?.isConnected && typeof scroller.getBoundingClientRect === 'function') {
    const viewport = scroller.getBoundingClientRect();
    const current = snapshot.anchor.row.getBoundingClientRect();
    const delta = current.top - viewport.top - snapshot.anchor.top;
    if (Number.isFinite(delta)) scroller.scrollTop = Math.max(0, Number(scroller.scrollTop || 0) + delta);
  } else if (snapshot) {
    const maxScroll = Math.max(0, Number(scroller.scrollHeight || 0) - Number(scroller.clientHeight || 0));
    scroller.scrollTop = Math.min(snapshot.scrollTop, maxScroll);
  }
  conversationFollowing = isConversationAtEnd();
  $('scroll-latest').hidden = conversationFollowing;
}

function followConversation() {
  const scroller = $('conversation');
  conversationFollowing = true;
  scroller.scrollTop = Math.max(0, (Number(scroller.scrollHeight) || 0) - (Number(scroller.clientHeight) || 0));
  $('scroll-latest').hidden = true;
}

function jumpToLatestConversation() {
  const scroller = $('conversation');
  if (scroller.getAttribute('tabindex') === null) scroller.setAttribute('tabindex', '-1');
  scroller.focus({preventScroll:true});
  followConversation();
}

function openWorkspaceCommands() {
  if (typeof window.pywebview?.api?.navigation_options === 'function') { openNavigationChoice('workspace'); return; }
  openScopedCommands('Escolher projeto', item => item.action_id?.startsWith('workspace.'));
}

let sessionDialogOpener = null;

function sessionText(value, fallback = 'Não informado') {
  return typeof value === 'string' && value.trim() ? value : fallback;
}

function sessionObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function sessionStatusLabel(value) {
  const labels = {
    available: 'Disponível', busy: 'Em uso', not_configured: 'Não configurado',
    not_implemented: 'Não implementado', forbidden: 'Sem permissão',
    temporarily_unavailable: 'Temporariamente indisponível', unavailable: 'Indisponível',
    unverified: 'Não verificado', verified: 'Verificado', failed: 'Falha reportada',
    listed: 'Listado no catálogo',
  };
  return typeof value === 'string' ? labels[value] || 'Desconhecido' : 'Desconhecido';
}

function sessionTechnicalStatus(value) {
  if (typeof value !== 'string' || !/^[a-z][a-z0-9_]{0,60}$/.test(value)) return 'Desconhecido';
  return `${sessionStatusLabel(value)} (${value})`;
}

function renderSessionSummary() {
  const info = sessionObject(sessionInfo);
  const profile = sessionObject(info.profile);
  const availability = sessionObject(info.availability);
  const backend = sessionObject(availability.backend);
  const resources = sessionObject(availability.resources);
  const setText = (id, value) => { $(id).textContent = value; };

  const workspaceName = sessionText(info.workspace_name);
  const workspaceRoot = sessionText(info.workspace_root, 'Pasta não informada');
  setText('session-workspace-name', workspaceName);
  setText('session-workspace-root', workspaceRoot);

  const profileName = sessionText(profile.name, backend.configured === false ? 'Não configurado' : 'Perfil não informado');
  const provider = sessionText(profile.provider, 'Provedor não informado');
  const model = sessionText(profile.model, 'Modelo não informado');
  setText('session-profile-name', profileName);
  setText('session-profile-description', `${provider} · ${model}`);

  const rawMode = typeof info.mode === 'string' ? info.mode : '';
  const normalizedMode = rawMode.trim().replaceAll('_', ' ').toUpperCase();
  const modeLabels = {'READ ONLY': 'Somente leitura', READ_ONLY: 'Somente leitura', EDITOR: 'Editor', FULL: 'Full'};
  setText('session-mode-value', modeLabels[normalizedMode] || 'Desconhecido');
  setText('session-mode-note', 'O modo limita as ações; cada mudança continua sujeita às permissões, aprovações e limites do projeto.');

  const configured = backend.configured;
  const connectivity = backend.connectivity;
  const readiness = backend.model_readiness;
  setText('session-connectivity', configured === false ? 'Modelo não configurado'
    : connectivity === 'verified' ? 'Catálogo acessível; isso não verifica geração'
    : connectivity === 'failed' ? 'Falha de conectividade reportada'
    : connectivity === 'unverified' ? 'Não verificada' : 'Desconhecida');
  setText('session-inference', configured === false ? 'Modelo não configurado'
    : readiness === 'listed' ? 'Modelo listado no catálogo; geração não verificada'
    : 'Inferência não verificada');

  setText('session-tech-workspace-id', sessionText(info.workspace_id));
  setText('session-tech-mode', sessionText(rawMode));
  setText('session-tech-configured', typeof configured === 'boolean' ? configured ? 'Sim (true)' : 'Não (false)' : 'Desconhecido');
  setText('session-tech-backend-state', sessionTechnicalStatus(backend.state));
  setText('session-tech-connectivity', sessionTechnicalStatus(connectivity));
  setText('session-tech-readiness', sessionTechnicalStatus(readiness));
  setText('session-tech-reason', typeof backend.reason === 'string' && /^[A-Z][A-Z0-9_]{0,80}$/.test(backend.reason)
    ? backend.reason : 'Código não informado');
  setText('session-tech-generation', typeof backend.generation === 'number' && Number.isFinite(backend.generation)
    ? String(backend.generation) : 'Não informada');
  const resourceNames = [['model', 'modelo'], ['files', 'arquivos'], ['tasks', 'tarefas'], ['tools', 'ferramentas'], ['approval', 'aprovações'], ['attachments', 'anexos']];
  const knownResources = resourceNames.filter(([key]) => Object.hasOwn(resources, key));
  setText('session-tech-resources', knownResources.length
    ? knownResources.map(([key, label]) => `${label}: ${sessionTechnicalStatus(resources[key])}`).join(' · ')
    : 'Não informado');

  for (const id of ['session-choose-workspace', 'session-profile-model', 'session-work-mode']) {
    $(id).disabled = sessionEnded;
  }
}

function returnSessionDialogFocus() {
  const opener = sessionDialogOpener;
  sessionDialogOpener = null;
  opener?.focus();
}

function closeSessionDialog() {
  const dialog = $('session-dialog');
  if (dialog.open) dialog.close();
  returnSessionDialogFocus();
}

function openSessionDialog(event) {
  renderSessionSummary();
  sessionDialogOpener = event?.currentTarget || $('session-open');
  $('session-dialog').showModal();
}

function openSessionAction(scope) {
  closeSessionDialog();
  if (scope === 'workspace') openWorkspaceCommands();
  else if (scope === 'profile') {
    if (typeof window.pywebview?.api?.navigation_options === 'function') openNavigationChoice('profile');
    else openScopedCommands('Perfil e modelo', item => item.action_id?.startsWith('model.'));
  }
  else if (scope === 'mode') openScopedCommands('Modo de trabalho', item => item.action_id?.startsWith('configuration.mode'));
}

function taskSummaryText(value, fallback = '') {
  if (typeof value !== 'string' || !value.trim()) return fallback;
  const text = value.trim();
  return text.length > 280 ? `${text.slice(0, 277)}…` : text;
}

function taskSummaryCount(value) {
  return Number.isInteger(value) && value >= 0 ? String(value) : 'não informado';
}

function taskStateLabel(snapshot) {
  const state = typeof snapshot.state === 'string' ? snapshot.state.trim().toUpperCase() : '';
  const controllerState = typeof snapshot.controller_state === 'string'
    ? snapshot.controller_state.trim().toUpperCase() : '';
  if (state === 'RUNNING' && controllerState === 'CANCELLING') {
    return 'Cancelamento solicitado; aguardando término da execução.';
  }
  if (state === 'RUNNING' && controllerState === 'STOPPING') {
    return 'Parada solicitada; aguardando término da execução.';
  }
  if (controllerState === 'SETTLING' && ['RUNNING', 'CANCELLING', 'STOPPING'].includes(state)) {
    return 'Finalizando a execução; aguardando o resultado.';
  }
  const labels = {
    IDLE: 'Nenhuma execução ativa.',
    RUNNING: 'Execução em andamento.',
    WAITING_ATTENTION: 'Execução aguardando sua atenção.',
    CANCELLING: 'Cancelamento solicitado; aguardando término da execução.',
    STOPPING: 'Parada solicitada; aguardando término da execução.',
    SETTLING: 'Finalizando a execução; aguardando o resultado.',
    TERMINAL: 'Execução encerrada; veja o resultado reportado abaixo.',
  };
  return labels[state] || 'Estado não informado.';
}

function taskOutcomeSummary(value) {
  const outcome = taskSummaryText(value);
  if (!outcome) return '';
  const normalized = outcome.toLowerCase();
  if (['succeeded', 'success', 'completed'].includes(normalized)) {
    return `Resultado reportado pela execução: ${outcome}. Isso não confirma inferência do modelo.`;
  }
  if (['failed', 'failure', 'error'].includes(normalized) || normalized.startsWith('error:')) {
    return `Falha reportada pela execução: ${outcome}.`;
  }
  if (['cancelled', 'canceled'].includes(normalized)) {
    return `Cancelamento reportado pela execução: ${outcome}.`;
  }
  return `Resultado reportado pela execução: ${outcome}.`;
}

function renderCurrentTaskSummary(snapshot) {
  const current = snapshot && typeof snapshot === 'object' && !Array.isArray(snapshot) ? snapshot : {};
  const lines = [taskStateLabel(current)];
  const tool = taskSummaryText(current.current_tool);
  const step = taskSummaryText(current.current_step);
  lines.push(tool || step
    ? `Atividade atual: ${[tool && `Ferramenta: ${tool}`, step && `Etapa: ${step}`].filter(Boolean).join(' · ')}`
    : 'Atividade atual: não informada.');
  lines.push(`Última atividade reportada: ${taskSummaryText(current.last_activity_at, 'não informada')}.`);

  lines.push(typeof current.attention_pending === 'boolean'
    ? current.attention_pending ? 'Atenção pendente; revise a solicitação antes de prosseguir.' : 'Nenhuma atenção pendente reportada.'
    : 'Estado da atenção não informado.');

  if (Number.isInteger(current.pending_count) && current.pending_count >= 0) {
    lines.push(current.pending_count > 0
      ? `Na fila: ${current.pending_count}. Veja os pedidos na fila abaixo; nenhum é enviado automaticamente.`
      : 'Na fila: 0.');
  } else {
    lines.push('Contagem da fila não informada.');
  }
  lines.push(`Avisos: ${taskSummaryCount(current.warning_count)} · erros: ${taskSummaryCount(current.error_count)}.`);

  const outcome = taskOutcomeSummary(current.terminal_outcome);
  if (outcome) lines.push(outcome);
  else if (String(current.state || '').trim().toUpperCase() === 'TERMINAL') lines.push('Resultado terminal não informado.');

  const summary = lines.join('\n');
  const target = $('task-current-summary');
  if (target.textContent !== summary) target.textContent = summary;
}

function setActiveNav(kind) {
  for (const name of ['home', 'chat', 'files', 'tasks', 'tools']) {
    const button = $(`nav-${name}`);
    button.classList.toggle('selected', name === kind);
    if (name === kind) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  $('artifact-link').classList.toggle('selected', kind === 'artifact' || kind === 'artifacts');
}

async function copyReportContent() {
  if ($('copy-report').disabled) return;
  const text = $('copy-report').dataset.text;
  if (typeof text !== 'string') return;
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard API unavailable');
    await navigator.clipboard.writeText(text);
    toast('Conteúdo copiado');
  } catch (_error) {
    const field = document.createElement('textarea');
    field.value = text;
    field.className = 'clipboard-fallback';
    field.setAttribute('aria-hidden', 'true');
    document.body.append(field);
    field.select();
    try {
      if (!document.execCommand('copy')) throw new Error('Cópia indisponível');
      toast('Conteúdo copiado');
    } catch (error) {
      toast(`Não foi possível copiar: ${String(error)}`);
    } finally {
      field.remove();
      $('copy-report').focus();
    }
  }
}

$('profile-menu').addEventListener('click', openSessionCommands);
$('workspace-switch').addEventListener('click', openWorkspaceCommands);
$('session-open').addEventListener('click', openSessionDialog);
$('session-dialog').addEventListener('close', returnSessionDialogFocus);
$('session-dialog').addEventListener('keydown', event => {
  const commandShortcut = ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') || event.key === 'F2';
  if (!commandShortcut) return;
  event.preventDefault();
  event.stopPropagation();
  closeSessionDialog();
  openCommandPalette();
});
$('session-close').addEventListener('click', closeSessionDialog);
$('session-choose-workspace').addEventListener('click', () => openSessionAction('workspace'));
$('session-profile-model').addEventListener('click', () => openSessionAction('profile'));
$('session-work-mode').addEventListener('click', () => openSessionAction('mode'));
$('conversation').addEventListener('scroll', syncScrollLatest, {passive:true});
$('scroll-latest').addEventListener('click', jumpToLatestConversation);
$('attach-file').addEventListener('click', addWorkspaceFile);
$('use-file-in-chat').addEventListener('click', useFileInChat);
$('analyze-file-locally').addEventListener('click', analyzeFileLocally);
$('report-options').addEventListener('click', toggleReportWrap);

let llmTogglePending = false;
function syncLlmControls() {
  const enabled = sessionInfo?.llm_enabled !== false;
  const configured = sessionInfo?.availability?.backend?.configured === true || Boolean(sessionInfo?.profile?.name);
  const toggle = $('llm-toggle');
  toggle.textContent = enabled ? 'Desativar LLM' : 'Ativar LLM';
  toggle.setAttribute('aria-pressed', String(enabled));
  toggle.dataset.enabled = String(enabled);
  toggle.disabled = !initialized || sessionEnded || busy || llmTogglePending || typeof api()?.set_llm_enabled !== 'function';
  toggle.title = enabled ? 'Voltar ao uso local. Aguarde a tarefa atual terminar.' : 'Liberar conversa e tarefas com o modelo configurado. Ativar não envia pedidos ao modelo.';
  $('llm-mode-note').textContent = enabled
    ? 'LLM ativada. Conversas e tarefas podem usar o modelo configurado. As funções locais continuam disponíveis; /exec sempre executa somente o comando que você escreveu.'
    : 'Uso local: comandos pontuais do shell, arquivos, análise estática e preparação de testes. Cada comando inicia um processo novo, usa sua conta e não tem sandbox. Ative a LLM para conversar e executar tarefas com IA.';
  $('home-llm-state').textContent = enabled
    ? 'Uso com LLM permitido. O estado do servidor aparece no topo; uma chamada s\u00f3 come\u00e7a ap\u00f3s voc\u00ea enviar uma solicita\u00e7\u00e3o.'
    : configured
      ? 'Uso local ativo. Ative a LLM no topo para liberar conversa e tarefas com IA.'
      : 'Uso local ativo. Escolha um perfil configurado antes de ativar a conversa com IA.';
  $('home-configure-profile').hidden = enabled || configured;
  $('prompt').placeholder = enabled ? 'O que você precisa fazer neste projeto? Use /exec para executar um comando local.' : 'Comando local, /ls, /find ou /git status…';
  $('send').title = enabled ? 'Enviar pedido ou comando /exec.' : 'Envia um comando local para execução. Para conversar, ative a LLM.';
}
async function toggleLlm() {
  if (llmTogglePending || busy || sessionEnded || !initialized) return;
  const enabled = sessionInfo?.llm_enabled !== false;
  const configured = sessionInfo?.availability?.backend?.configured === true || Boolean(sessionInfo?.profile?.name);
  if (!enabled && !configured) {
    toast('Escolha um perfil de modelo configurado para ativar a LLM.');
    openSessionAction('profile');
    return;
  }
  const workspace = sessionInfo?.workspace_id;
  llmTogglePending = true; syncLlmControls();
  try {
    const result = await api().set_llm_enabled(!enabled, workspace);
    if (sessionEnded || workspace !== sessionInfo?.workspace_id) return;
    if (!result.accepted) {
      $('llm-mode-note').textContent = result.message || 'Não foi possível mudar o uso da LLM.';
      toast(result.message || 'Não foi possível mudar o uso da LLM.');
      return;
    }
    renderSessionInfo(result.session);
    toast(result.session.llm_enabled ? 'LLM ativada. Nenhum pedido enviado ao modelo.' : 'Uso local ativado.');
  } catch (error) {
    toast('Não foi possível mudar o uso da LLM: ' + String(error));
  } finally {
    llmTogglePending = false;
    // Preserve activation errors until the next user action.
    const note = $('llm-mode-note').textContent;
    syncLlmControls();
    if (sessionInfo?.llm_enabled === false && note.includes('NOT_CONFIGURED')) $('llm-mode-note').textContent = note;
    if (typeof syncTestsControls === 'function') syncTestsControls();
  }
}
$('llm-toggle').addEventListener('click', toggleLlm);
$('tests-model-toggle').addEventListener('click', toggleLlm);
$('home-configure-profile').addEventListener('click', () => openSessionAction('profile'));
