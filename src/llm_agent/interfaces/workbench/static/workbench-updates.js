// Local maintenance is explicit and never submits a model task.
(() => {
  const dialog = document.getElementById('updates-dialog');
  const button = document.createElement('button');
  button.id = 'workbench-updates';
  button.type = 'button';
  button.className = 'offline-pill';
  button.textContent = 'Atualizações';
  button.title = 'Verificar mudanças no código local e gerenciar a instalação';
  document.querySelector('.top-actions').prepend(button);
  const statusText = document.getElementById('updates-message');
  const check = document.getElementById('updates-check');
  const apply = document.getElementById('updates-apply');
  const restart = document.getElementById('updates-restart');
  let checking = false;
  let applying = false;
  let restarting = false;
  let lastCheck = 0;

  function syncControls() {
    check.disabled = checking || applying || restarting;
    apply.disabled = checking || applying || restarting;
    restart.disabled = checking || applying || restarting;
  }

  function render(status) {
    document.getElementById('updates-version').textContent = status.version
      ? `Versão ${status.version} · Código ${status.revision}` : 'Instalação local';
    statusText.textContent = status.restart_required
      ? 'Uma versão já está preparada. Reinicie para usá-la.' : status.message;
    button.textContent = status.restart_required ? 'Reiniciar para atualizar'
      : status.available ? 'Atualização disponível' : 'Atualizações';
    apply.hidden = !status.available || status.state !== 'managed';
    restart.hidden = !status.restart_required;
  }

  async function refresh() {
    if (checking || applying || restarting || !window.pywebview?.api?.update_status) return;
    checking = true;
    syncControls();
    try { render(await window.pywebview.api.update_status()); lastCheck = Date.now(); }
    catch (error) { statusText.textContent = `Não foi possível verificar: ${String(error)}`; }
    finally { checking = false; syncControls(); }
  }

  button.addEventListener('click', async () => {
    dialog.showModal();
    await refresh();
  });
  document.getElementById('updates-close').addEventListener('click', () => dialog.close());
  check.addEventListener('click', refresh);
  apply.addEventListener('click', async () => {
    if (checking || applying || restarting) return;
    applying = true;
    syncControls();
    statusText.textContent = 'Preparando e validando a atualização. A janela atual continuará disponível…';
    try {
      const result = await window.pywebview.api.apply_update();
      if (!result.accepted) throw new Error(result.message);
      render(result);
    } catch (error) { statusText.textContent = `Atualização não concluída: ${String(error)}`; }
    finally { applying = false; syncControls(); }
  });
  restart.addEventListener('click', async () => {
    if (checking || applying || restarting) return;
    restarting = true;
    syncControls();
    try {
      const result = await window.pywebview.api.restart_workbench();
      if (!result.accepted) throw new Error(result.message);
    } catch (error) {
      statusText.textContent = String(error);
      restarting = false;
      syncControls();
    }
  });
  window.addEventListener('pywebviewready', refresh);
  window.addEventListener('focus', () => { if (Date.now() - lastCheck > 30000) refresh(); });
  setInterval(() => { if (!document.hidden) refresh(); }, 60000);
})();
