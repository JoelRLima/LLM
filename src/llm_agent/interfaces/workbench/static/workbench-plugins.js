// Explicit local administration. No model calls, automatic grants or plugin execution here.
let pluginsSnapshot = null;
let pluginsGeneration = 0;
let pluginsOperation = false;
let pluginsOpener = null;

function syncPluginControls() {
  const blocked = busy || sessionEnded || pluginsOperation || !pluginsSnapshot
    || pluginsSnapshot.workspace_id !== sessionInfo?.workspace_id;
  for (const button of document.querySelectorAll('#plugins-items button')) {
    button.disabled = blocked || button.dataset.available !== 'yes';
  }
  $('plugins-browse').disabled = busy || sessionEnded || pluginsOperation;
  $('plugins-register').disabled = blocked || !$('plugins-manifest').value.trim();
  $('plugins-clear').disabled = blocked || !pluginsSnapshot?.authorized_ids?.length;
  $('plugins-refresh').disabled = busy || sessionEnded || pluginsOperation;
  $('open-plugins').disabled = busy || sessionEnded;
}

function closePlugins() {
  pluginsGeneration += 1;
  pluginsSnapshot = null;
  if ($('plugins-dialog').open) $('plugins-dialog').close();
  pluginsOpener?.focus();
  pluginsOpener = null;
}

function pluginButton(row, label, action, item, value = '', available = true) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.dataset.available = available ? 'yes' : 'no';
  button.dataset.pluginAction = action;
  button.addEventListener('click', () => actOnPlugin(action, item, value));
  row.append(button);
}

function renderPlugins(state) {
  pluginsSnapshot = state;
  const host = $('plugins-items');
  host.replaceChildren();
  const statuses = {ready:"Configura\u00e7\u00e3o pronta", blocked:'Bloqueado', disabled:'Desabilitado', not_enabled:'Registrado'};
  for (const item of state.items.slice(0, 200)) {
    const row = document.createElement('article');
    row.className = 'plugin-card';
    const name = document.createElement('h3');
    name.textContent = item.extension_id;
    const status = document.createElement('p');
    status.textContent = `${statuses[item.activation_status] || item.activation_status} · ${item.session_authorized ? 'Autorizado nesta sessão' : 'Sem autorização nesta sessão'}`;
    row.append(name, status);
    const execution = item.execution;
    if (execution) {
      const warning = document.createElement('p');
      warning.textContent = execution.warning;
      row.append(warning);
    }
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'Origem, fingerprint e diagnóstico';
    const text = document.createElement('pre');
    text.textContent = `Manifest: ${item.manifest_path || 'Ausente'}\nSHA-256: ${item.manifest_sha256 || 'Ausente'}\nObservado: ${item.observed_sha256 || 'Ausente'}\nEstado: ${item.manifest_status}\nDiagnóstico: ${(item.diagnostic_codes || []).join(', ') || 'Nenhum'}`;
    details.append(summary, text);
    if (execution) {
      const process = document.createElement('pre');
      process.textContent = `Comando (argv): ${execution.command || 'Indisponível; confira o manifest'}\nDiretório: ${execution.cwd}\nIdentidade: usuário atual do sistema\nIsolamento: sem sandbox\nFingerprint: apenas manifest; executável e dependências não estão fixados\nRevisão: ${execution.review_status}\nModo: ${execution.session_mode || state.mode}\nGrants: ${(item.grants || []).join(', ') || 'Nenhum'}`;
      details.append(process);
    }
    row.append(details);
    pluginButton(row, item.enabled ? 'Desabilitar' : 'Habilitar', item.enabled ? 'disable' : 'enable', item, '',
      item.enabled || item.manifest_status === 'unchanged');
    const grants = document.createElement('div');
    const required = Array.isArray(item.required_capabilities) ? item.required_capabilities : [];
    for (const capability of [...new Set([...required, ...(item.grants || [])])]) {
      const granted = item.grants.includes(capability);
      pluginButton(grants, `${granted ? 'Revogar' : 'Conceder'} ${capability}`,
        granted ? 'revoke' : 'grant', item, capability, granted || (item.enabled && item.manifest_status === 'unchanged'));
    }
    row.append(grants);
    pluginButton(row, item.session_authorized ? 'Retirar da sessão' : 'Autorizar nesta sessão',
      item.session_authorized ? 'deauthorize' : 'authorize', item, '', item.session_authorized || item.activation_status === 'ready');
    if (item.manifest_status === 'changed' && item.observed_sha256) pluginButton(row, "Aceitar manifest revisado", 'replace', item, item.observed_sha256);
    pluginButton(row, item.manifest_sha256 ? "Remover do cat\u00e1logo global" : "Esquecer configura\u00e7\u00e3o \u00f3rf\u00e3", item.manifest_sha256 ? 'remove' : 'forget', item);
    host.append(row);
  }
  $('plugins-status').textContent = state.items.length
    ? `${state.items.length} plugin(s) · modo atual: ${state.mode}. Autorizar não executa o plugin.`
    : 'Nenhum plugin registrado. Informe o caminho de um manifest local para começar.';
  syncPluginControls();
}

function validPluginState(state) {
  return state?.accepted === true && state.workspace_id === sessionInfo?.workspace_id
    && Array.isArray(state.items) && Array.isArray(state.authorized_ids)
    && state.items.every(item => item && typeof item.extension_id === 'string'
      && Array.isArray(item.grants) && Array.isArray(item.required_capabilities));
}

async function refreshPlugins() {
  if (busy || sessionEnded || pluginsOperation) return;
  const generation = ++pluginsGeneration;
  pluginsSnapshot = null;
  syncPluginControls();
  $('plugins-items').replaceChildren();
  $('plugins-status').textContent = 'Lendo catálogo e permissões locais…';
  try {
    const state = await api().plugin_state();
    if (generation !== pluginsGeneration || !$('plugins-dialog').open) return;
    if (!validPluginState(state)) throw new Error(state?.message || 'Lista indisponível ou projeto alterado.');
    renderPlugins(state);
  } catch (error) {
    if (generation === pluginsGeneration) $('plugins-status').textContent = String(error);
  }
}

async function openPlugins() {
  if (busy || sessionEnded || !initialized) return;
  if (typeof api().plugin_state !== 'function') { toast('Gerenciador indisponível nesta versão.'); return; }
  pluginsOpener = document.activeElement;
  $('plugins-dialog').showModal();
  await refreshPlugins();
}

async function pickPluginManifest() {
  if (busy || sessionEnded || pluginsOperation || !$('plugins-dialog').open) return;
  if (typeof api().plugin_pick_manifest !== 'function') { toast('Seletor de arquivos indisponível nesta versão.'); return; }
  const workspaceId = sessionInfo?.workspace_id;
  const generation = ++pluginsGeneration;
  pluginsOperation = true; busyActionInFlight = true; setBusy(true); syncPluginControls();
  $('plugins-status').textContent = 'Escolha o manifest JSON local…';
  try {
    const result = await api().plugin_pick_manifest(workspaceId);
    if (generation !== pluginsGeneration || !$('plugins-dialog').open) return;
    if (!result?.accepted) throw new Error(result?.message || 'Não foi possível escolher o manifest.');
    if (!result.cancelled && result.path) {
      $('plugins-manifest').value = result.path;
      $('plugins-status').textContent = 'Arquivo selecionado. Revise o caminho e registre o manifest para continuar.';
    }
  } catch (error) {
    if (generation === pluginsGeneration) $('plugins-status').textContent = String(error);
  } finally {
    pluginsOperation = false; busyActionInFlight = false; setBusy(false); syncPluginControls();
    $('run-status').textContent = 'Pronto';
  }
}

async function actOnPlugin(action, item = null, value = '') {
  if (busy || sessionEnded || pluginsOperation || !pluginsSnapshot || !$('plugins-dialog').open) return;
  const workspaceId = pluginsSnapshot.workspace_id;
  if (workspaceId !== sessionInfo?.workspace_id) return;
  if (item && !pluginsSnapshot.items.includes(item)) return;
  if (action === 'authorize') {
    const execution = item?.execution;
    if (!execution || execution.review_status !== 'matched') { toast('Atualize a lista e revise o manifest antes de autorizar.'); return; }
    if (!window.confirm(`Autorizar processo local confiável nesta sessão?\n\nComando (argv): ${execution.command}\nDiretório: ${execution.cwd}\nManifest SHA-256: ${item.manifest_sha256}\nGrants: ${(item.grants || []).join(', ')}\n\n${execution.warning}`)) return;
  }
  if (action === 'register' && !value.trim()) { $('plugins-manifest').focus(); return; }
  const generation = ++pluginsGeneration;
  pluginsOperation = true;
  busyActionInFlight = true;
  setBusy(true);
  syncPluginControls();
  $('run-status').textContent = 'Atualizando plugins locais…';
  try {
    const state = await api().plugin_action(workspaceId, action, item?.extension_id || '', value, item?.manifest_sha256 || '');
    if (generation !== pluginsGeneration || !$('plugins-dialog').open) return;
    if (!validPluginState(state)) throw new Error(state?.message || 'Alteração não confirmada. Atualize a lista.');
    renderPlugins(state);
    if (action === 'register') $('plugins-manifest').value = '';
  } catch (error) {
    if (generation === pluginsGeneration) {
      pluginsSnapshot = null;
      $('plugins-items').replaceChildren();
      $('plugins-status').textContent = `${String(error)} Atualize a lista antes de continuar.`;
    }
  } finally {
    pluginsOperation = false;
    busyActionInFlight = false;
    setBusy(false);
    syncPluginControls();
    $('run-status').textContent = 'Pronto';
    if (!sessionEnded && api().initialize) {
      const requestId = ++availabilityRequestId;
      try { renderSessionInfo(await api().initialize(), requestId); }
      catch (_error) { toast("N\u00e3o foi poss\u00edvel atualizar o resumo da sess\u00e3o. Reabra Plugins."); }
    }
  }
}

$('open-plugins').addEventListener('click', openPlugins);
$('plugins-close').addEventListener('click', closePlugins);
$('plugins-browse').addEventListener('click', pickPluginManifest);
$('plugins-refresh').addEventListener('click', refreshPlugins);
$('plugins-manifest').addEventListener('input', syncPluginControls);
$('plugins-register').addEventListener('click', () => actOnPlugin('register', null, $('plugins-manifest').value.trim()));
$('plugins-clear').addEventListener('click', () => actOnPlugin('clear_authority'));
$('plugins-dialog').addEventListener('cancel', event => { event.preventDefault(); closePlugins(); });
