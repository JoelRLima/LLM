// Presentation over the canonical pending store and existing command admission.
let pendingQueueSnapshot = null;
let pendingQueueAction = false;
let pendingQueueRefresh = 0;

function validPendingQueue(snapshot) {
  if (!snapshot || typeof snapshot.workspace_id !== 'string' || !snapshot.workspace_id
      || !Array.isArray(snapshot.pending_items) || snapshot.pending_items.length > 16) return false;
  const ids = new Set();
  return snapshot.pending_items.every(item => {
    if (!item || !Number.isSafeInteger(item.pending_id) || item.pending_id < 1 || ids.has(item.pending_id)
        || typeof item.text !== 'string' || !item.text.trim() || item.text.length > 16000) return false;
    ids.add(item.pending_id);
    return true;
  });
}

function pendingQueueMatches(workspaceId) {
  return Boolean(workspaceId && workspaceId === sessionInfo?.workspace_id);
}

function syncPendingQueueActions() {
  for (const button of document.querySelectorAll('#pending-queue-items button')) {
    button.disabled = sessionEnded || pendingQueueAction || !pendingQueueMatches(button.dataset.workspaceId)
      || (button.dataset.queueAction === 'send' && (busy || pendingQueueSnapshot?.controller_state !== 'IDLE'));
  }
}

function renderPendingQueue(snapshot) {
  if (pendingQueueAction) return;
  pendingQueueRefresh += 1;
  pendingQueueSnapshot = validPendingQueue(snapshot) && pendingQueueMatches(snapshot.workspace_id) ? snapshot : null;
  const host = $('pending-queue-items');
  host.replaceChildren();
  if (!pendingQueueSnapshot) {
    $('pending-queue-note').textContent = 'Fila não disponível nesta consulta. Atualize Tarefas; em sessões antigas, use /pending.';
    return;
  }
  const items = pendingQueueSnapshot.pending_items;
  $('pending-queue-note').textContent = items.length
    ? `${items.length} ${items.length === 1 ? 'pedido aguardando' : 'pedidos aguardando'}. Envie um por vez após a execução atual terminar.`
    : 'Nenhum pedido na fila. Mensagens enviadas durante uma execução aparecem aqui.';
  for (const item of items) {
    const row = document.createElement('article');
    row.className = 'pending-queue-item';
    const text = document.createElement('p');
    text.textContent = item.text.length > 240 ? `${item.text.slice(0, 240)}…` : item.text;
    row.append(text);
    const detail = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'Ver pedido completo e ID';
    const full = document.createElement('pre');
    full.textContent = `Pedido #${item.pending_id}\n${item.text}`;
    detail.append(summary, full);
    row.append(detail);
    for (const [action, label] of [['send','Enviar'], ['discard','Remover']]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.dataset.queueAction = action;
      button.dataset.workspaceId = snapshot.workspace_id;
      button.addEventListener('click', () => actOnPendingQueue(action, item, snapshot.workspace_id));
      row.append(button);
    }
    host.append(row);
  }
  syncPendingQueueActions();
}

async function refreshPendingQueue() {
  const generation = ++pendingQueueRefresh;
  const workspaceId = sessionInfo?.workspace_id;
  try {
    const snapshot = await api().task_status();
    if (generation === pendingQueueRefresh && pendingQueueMatches(workspaceId) && panelMode === 'tasks') renderPendingQueue(snapshot);
  } catch (_error) {
    if (generation === pendingQueueRefresh && pendingQueueMatches(workspaceId) && panelMode === 'tasks') {
      pendingQueueSnapshot = null;
      $('pending-queue-items').replaceChildren();
      $('pending-queue-note').textContent = 'Não foi possível atualizar a fila. Use Atualizar em Tarefas.';
    }
  }
}

async function actOnPendingQueue(action, item, workspaceId) {
  if (sessionEnded || pendingQueueAction || !pendingQueueMatches(workspaceId)) return;
  if (!['send','discard'].includes(action) || !pendingQueueSnapshot?.pending_items.some(
    current => current.pending_id === item.pending_id && current.text === item.text)) return;
  if (action === 'send' && (busy || pendingQueueSnapshot?.controller_state !== 'IDLE')) return;
  pendingQueueAction = true;
  syncPendingQueueActions();
  try {
    if (action === 'send') {
      message('user', item.text);
      await dispatchSubmission(`/pending send ${item.pending_id}`);
    } else if (action === 'discard') {
      const result = await api().control(`/pending discard ${item.pending_id}`);
      if (result?.accepted !== true || result.observation?.status !== 'succeeded') {
        throw new Error(result?.message || result?.observation?.answer || 'Remoção não confirmada.');
      }
    }
  } catch (error) {
    if (pendingQueueMatches(workspaceId)) toast(`Fila: ${String(error)}`);
  } finally {
    pendingQueueAction = false;
    if (pendingQueueMatches(workspaceId) && panelMode === 'tasks') await refreshPendingQueue();
    syncPendingQueueActions();
  }
}
