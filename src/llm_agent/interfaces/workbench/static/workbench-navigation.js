// Guided inputs over canonical idle-only session commands, without editing chat drafts.
let navigationGeneration = 0;
let navigationChoice = null;
let navigationOpener = null;

function navigationValue(value) {
  return typeof value === 'string' && value.trim().length > 0
    && value.length <= 15000 && !/[\r\n\x00-\x1f\x7f]/.test(value);
}

function navigationCommand() {
  const choice = navigationChoice;
  if (!choice || choice.workspaceId !== sessionInfo?.workspace_id) return '';
  const value = $('navigation-value').value.trim();
  if (!navigationValue(value)) return '';
  if (choice.kind === 'profile' && !choice.profiles.some(item => item.name === value)) return '';
  const [action, prefix] = choice.kind === 'workspace'
    ? ['workspace.switch', '/workspace switch'] : ['model.select', '/model select'];
  if (!commandEntries.some(item => item.action_id === action && item.command === prefix)) return '';
  return `${prefix} ${value}`;
}

function syncNavigationChoice() {
  $('navigation-add-profile').disabled = busy || sessionEnded || navigationChoice?.kind !== 'profile';
  const command = navigationCommand();
  $('navigation-command').textContent = command || 'Escolha uma opção válida.';
  $('navigation-apply').disabled = !command || busy || sessionEnded;
}

function closeNavigationChoice() {
  navigationGeneration += 1;
  navigationChoice = null;
  if ($('navigation-dialog').open) $('navigation-dialog').close();
  navigationOpener?.focus();
  navigationOpener = null;
}

function addNavigationOption(label, value) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'navigation-option';
  button.textContent = label;
  button.addEventListener('click', () => {
    if (!navigationChoice || busy || sessionEnded) return;
    $('navigation-value').value = value;
    syncNavigationChoice();
    $('navigation-apply').focus();
  });
  $('navigation-options').append(button);
}

async function openNavigationChoice(kind) {
  if (!['workspace', 'profile'].includes(kind) || sessionEnded) return;
  if (busy) { toast('Aguarde a execução terminar para trocar projeto ou perfil.'); return; }
  const generation = ++navigationGeneration;
  navigationOpener = document.activeElement;
  navigationChoice = null;
  const project = kind === 'workspace';
  $('navigation-title').textContent = project ? 'Escolher projeto' : 'Escolher perfil de modelo';
  $('navigation-add-profile').hidden = project;
  $('navigation-help').textContent = project
    ? 'Escolha um projeto recente ou informe uma pasta existente. Resolva os pedidos na fila antes de trocar de projeto.'
    : 'Escolha um perfil configurado ou crie um pelo botão Configurar perfil. A seleção atualiza o perfil padrão; conexão e geração continuam sem teste.';
  $('navigation-label').textContent = project ? 'Pasta do projeto' : 'Nome do perfil';
  $('navigation-value').value = project ? sessionInfo?.workspace_root || '' : sessionInfo?.profile?.name || '';
  $('navigation-value').readOnly = !project;
  $('navigation-value').placeholder = project ? 'Ex.: D:\\Projetos\\Meu projeto' : 'Selecione um perfil abaixo';
  $('navigation-options').replaceChildren();
  $('navigation-status').textContent = 'Carregando opções locais…';
  syncNavigationChoice();
  $('navigation-dialog').showModal();
  const workspaceId = sessionInfo?.workspace_id;
  try {
    const result = await api().navigation_options();
    if (generation !== navigationGeneration || !$('navigation-dialog').open) return;
    if (result?.accepted !== true) throw new Error(result?.message || 'Opções indisponíveis.');
    if (workspaceId !== sessionInfo?.workspace_id || result?.workspace_id !== workspaceId) {
      throw new Error('O projeto mudou. Feche e abra esta seleção novamente.');
    }
    if (!Array.isArray(result.profiles) || !Array.isArray(result.recent_workspaces)) {
      throw new Error(result?.message || 'Opções indisponíveis. Use Comandos para conferir a sessão.');
    }
    const profiles = result.profiles.slice(0, 100).filter(item => item && navigationValue(item.name));
    navigationChoice = {kind, workspaceId, profiles};
    const options = project ? result.recent_workspaces.slice(0, 20).filter(navigationValue) : profiles;
    for (const item of options) {
      if (project) addNavigationOption(item, item);
      else addNavigationOption([item.name, item.provider, item.model].filter(value => typeof value === 'string').join(' · '), item.name);
    }
    $('navigation-status').textContent = options.length
      ? project ? 'Projetos recentes' : 'Perfis configurados'
      : project ? 'Nenhum projeto recente. Informe a pasta acima.' : 'Nenhum perfil configurado. Use Configurar perfil para adicionar um endpoint.';
    syncNavigationChoice();
    if (project) $('navigation-value').focus();
    else $('navigation-options').querySelector('button')?.focus();
  } catch (error) {
    if (generation !== navigationGeneration) return;
    navigationChoice = null;
    $('navigation-status').textContent = String(error);
    syncNavigationChoice();
  }
}

async function applyNavigationChoice() {
  if (busy || sessionEnded || !$('navigation-dialog').open) return;
  const command = navigationCommand();
  if (!command) { syncNavigationChoice(); return; }
  closeNavigationChoice();
  message('user', command);
  await dispatchSubmission(command);
}

function openProfileSetup() {
  if (busy || sessionEnded || navigationChoice?.kind !== 'profile') return;
  $('profile-setup-status').textContent = 'Salvar não testa conexão nem geração. Chaves de API não são armazenadas por este formulário.';
  $('profile-setup-dialog').showModal();
  $('profile-setup-name').focus();
}

async function saveProfileSetup() {
  if (busy || sessionEnded || $('profile-setup-save').disabled) return;
  const workspaceId = sessionInfo?.workspace_id;
  const values = {
    name: $('profile-setup-name').value.trim(),
    model: $('profile-setup-model').value.trim(),
    base_url: $('profile-setup-endpoint').value.trim(),
  };
  if (!workspaceId || !values.name || !values.model || !values.base_url) {
    $('profile-setup-status').textContent = 'Preencha o nome, o modelo e a URL base.';
    return;
  }
  $('profile-setup-save').disabled = true;
  $('profile-setup-status').textContent = 'Validando e salvando o perfil local…';
  try {
    const result = await api().configure_profile(workspaceId, values.name, values.model, values.base_url);
    if (!result?.accepted || workspaceId !== sessionInfo?.workspace_id) {
      throw new Error(result?.message || 'O perfil não foi salvo.');
    }
    renderSessionInfo(result.session);
    $('profile-setup-dialog').close();
    closeNavigationChoice();
    toast('Perfil salvo e selecionado. A conexão ainda não foi testada.');
  } catch (error) {
    $('profile-setup-status').textContent = String(error);
  } finally {
    $('profile-setup-save').disabled = false;
  }
}

$('navigation-value').addEventListener('input', syncNavigationChoice);
$('navigation-apply').addEventListener('click', applyNavigationChoice);
$('navigation-close').addEventListener('click', closeNavigationChoice);
$('navigation-dialog').addEventListener('cancel', event => { event.preventDefault(); closeNavigationChoice(); });
$('navigation-add-profile').addEventListener('click', openProfileSetup);
$('profile-setup-close').addEventListener('click', () => $('profile-setup-dialog').close());
$('profile-setup-save').addEventListener('click', saveProfileSetup);
$('profile-setup-dialog').addEventListener('cancel', event => { event.preventDefault(); $('profile-setup-dialog').close(); });
