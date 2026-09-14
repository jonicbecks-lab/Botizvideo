(() => {
  'use strict';

  const CHECK_INTERVAL_MS = 5 * 60 * 1000;
  const POLL_MS = 900;
  let busyPoll = null;
  let autoCheckTimer = null;
  let latest = null;

  function authHeaders(json = false) {
    const headers = {};
    const token = sessionStorage.getItem('galkaLiveSession') || '';
    if (token) headers['X-Galka-Session'] = token;
    if (json) headers['Content-Type'] = 'application/json';
    return headers;
  }

  async function apiGet(path) {
    const response = await fetch(path, {
      method: 'GET',
      headers: authHeaders(false),
      credentials: 'same-origin',
      cache: 'no-store',
    });
    const payload = await response.json();
    if (!response.ok || payload?.ok === false) throw new Error(payload?.error || `HTTP ${response.status}`);
    return payload?.data;
  }

  async function apiPost(path, body = {}) {
    const response = await fetch(path, {
      method: 'POST',
      headers: authHeaders(true),
      credentials: 'same-origin',
      cache: 'no-store',
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok || payload?.ok === false) throw new Error(payload?.error || `HTTP ${response.status}`);
    return payload?.data;
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;');
  }

  function short(value) {
    const text = String(value || '');
    return text ? text.slice(0, 7) : '—';
  }

  function operationLabel(state) {
    const map = {
      starting: 'Подготовка…',
      running: 'Выполняется…',
      rolling_back: 'Ошибка — выполняется автоматический откат…',
      success: 'Готово',
      failed: 'Ошибка',
      failed_rolled_back: 'Ошибка · предыдущая версия восстановлена',
      failed_rollback_failed: 'Критическая ошибка отката',
      checked: 'Проверено',
    };
    return map[state] || state || '';
  }

  function ensureUi() {
    if (document.getElementById('galkaUpdaterCard')) return;
    const drawer = document.getElementById('drawer');
    if (!drawer) return;

    const head = document.createElement('div');
    head.className = 'section-head galka-update-head';
    head.innerHTML = '<b>Обновление GALKA</b><span class="galka-update-badge" id="galkaUpdateBadge">не проверено</span>';

    const card = document.createElement('section');
    card.className = 'galka-update-card';
    card.id = 'galkaUpdaterCard';
    card.innerHTML = `
      <div class="galka-update-version">
        <span><small>Установлено</small><b id="galkaUpdateInstalled">—</b></span>
        <span class="arrow">→</span>
        <span><small>GitHub</small><b id="galkaUpdateLatest">—</b></span>
      </div>
      <div class="galka-update-kind" id="galkaUpdateKind">Проверка ещё не выполнялась.</div>
      <div class="galka-update-actions">
        <button type="button" id="galkaUpdateCheck">Проверить</button>
        <button type="button" class="primary" id="galkaUpdateInstall" disabled>Обновить</button>
        <button type="button" id="galkaUpdateRestart">Перезапустить</button>
        <button type="button" id="galkaUpdateRollback" disabled>Откатить последнее</button>
      </div>
      <div class="galka-update-log" id="galkaUpdateLog">—</div>
    `;

    const eventsHead = Array.from(drawer.querySelectorAll('.section-head')).find(node => node.textContent.includes('Последние события'));
    if (eventsHead) {
      drawer.insertBefore(head, eventsHead);
      drawer.insertBefore(card, eventsHead);
    } else {
      drawer.append(head, card);
    }

    const workspace = document.querySelector('.workspace');
    if (workspace) {
      const floating = document.createElement('button');
      floating.type = 'button';
      floating.id = 'galkaUpdateFloat';
      floating.className = 'galka-update-float hidden';
      floating.textContent = '● UPDATE';
      floating.addEventListener('click', () => {
        document.getElementById('detailsButton')?.click();
        setTimeout(() => card.scrollIntoView({ behavior: 'smooth', block: 'center' }), 120);
      });
      workspace.append(floating);
    }

    document.getElementById('galkaUpdateCheck')?.addEventListener('click', () => checkNow(true));
    document.getElementById('galkaUpdateInstall')?.addEventListener('click', installUpdate);
    document.getElementById('galkaUpdateRestart')?.addEventListener('click', restartGalka);
    document.getElementById('galkaUpdateRollback')?.addEventListener('click', rollbackUpdate);
  }

  function setButtonsDisabled(disabled) {
    const check = document.getElementById('galkaUpdateCheck');
    const install = document.getElementById('galkaUpdateInstall');
    const restart = document.getElementById('galkaUpdateRestart');
    const rollback = document.getElementById('galkaUpdateRollback');
    if (check) check.disabled = disabled;
    if (restart) restart.disabled = disabled;
    if (install) install.disabled = disabled || !latest?.available || !!latest?.blockedReason || latest?.worktreeClean === false;
    if (rollback) rollback.disabled = disabled || !latest?.rollbackAvailable;
  }

  function renderSteps(operation) {
    const log = document.getElementById('galkaUpdateLog');
    if (!log) return;
    if (!operation || !Object.keys(operation).length) {
      log.textContent = '—';
      return;
    }
    const lines = [];
    if (operation.state) {
      const stateClass = operation.state === 'success' ? 'ok' : operation.state?.includes('fail') ? 'error' : operation.state === 'running' || operation.state === 'rolling_back' ? 'running' : '';
      lines.push(`<div class="${stateClass}"><b>${escapeHtml(operationLabel(operation.state))}</b>${operation.elapsedMs != null ? ` · ${(Number(operation.elapsedMs) / 1000).toFixed(1)} сек` : ''}</div>`);
    }
    for (const step of operation.steps || []) {
      const icon = step.state === 'ok' ? '✓' : step.state === 'error' ? '✕' : '…';
      const cls = step.state === 'ok' ? 'ok' : step.state === 'error' ? 'error' : 'running';
      lines.push(`<div class="${cls}">${icon} ${escapeHtml(step.name)}${step.ms != null ? ` · ${Number(step.ms).toFixed(0)} ms` : ''}</div>`);
    }
    if (operation.message) lines.push(`<div>${escapeHtml(operation.message)}</div>`);
    if (operation.error) lines.push(`<div class="error">${escapeHtml(operation.error)}</div>`);
    if (operation.rollbackError) lines.push(`<div class="error">Откат: ${escapeHtml(operation.rollbackError)}</div>`);
    log.innerHTML = lines.join('') || '—';
  }

  function render(data) {
    if (!data) return;
    latest = data;
    const installed = document.getElementById('galkaUpdateInstalled');
    const remote = document.getElementById('galkaUpdateLatest');
    const kind = document.getElementById('galkaUpdateKind');
    const badge = document.getElementById('galkaUpdateBadge');
    const floating = document.getElementById('galkaUpdateFloat');
    if (installed) installed.textContent = data.installedShort || short(data.installedSha);
    if (remote) remote.textContent = data.latestShort || short(data.latestSha);

    let description = 'Установлена последняя версия.';
    if (data.available) {
      description = data.updateType === 'ui'
        ? `UI-патч · ${data.changedFileCount || 0} файл(ов) · перезапуск сервера не нужен.`
        : `Backend-патч · ${data.changedFileCount || 0} файл(ов) · перед установкой будет проверена биржа и выполнен безопасный перезапуск.`;
    }
    if (data.blockedReason) description = data.blockedReason;
    if (data.worktreeClean === false) description = 'Есть локальные изменения Git — автообновление заблокировано.';
    if (data.activeCampaigns?.length && data.updateType === 'backend' && data.available) {
      description += ' Сейчас есть активная GALKA: backend-обновление будет заблокировано до полного завершения/снятия.';
    }
    if (kind) kind.textContent = description;

    const busy = !!data.busy || ['starting', 'running', 'rolling_back'].includes(data.lastOperation?.state);
    if (badge) {
      badge.className = `galka-update-badge${busy ? ' busy' : data.available ? ' available' : ''}`;
      badge.textContent = busy ? 'обновляется…' : data.available ? 'доступно' : 'актуально';
    }
    if (floating) floating.classList.toggle('hidden', !data.available || busy);
    renderSteps(data.lastOperation);
    setButtonsDisabled(busy);
  }

  async function readStatus() {
    try {
      const data = await apiGet('/api/live/updater/status');
      render(data);
      return data;
    } catch (_) {
      return null;
    }
  }

  async function checkNow(userTriggered = false) {
    ensureUi();
    setButtonsDisabled(true);
    const badge = document.getElementById('galkaUpdateBadge');
    if (badge) {
      badge.className = 'galka-update-badge busy';
      badge.textContent = 'проверка…';
    }
    try {
      const data = await apiPost('/api/live/updater/check', {});
      latest = { ...(latest || {}), ...data, lastOperation: { state: 'checked', ...data } };
      render(latest);
    } catch (error) {
      const log = document.getElementById('galkaUpdateLog');
      if (log) log.innerHTML = `<div class="error">${escapeHtml(error.message || 'Не удалось проверить обновление')}</div>`;
      if (userTriggered && badge) {
        badge.className = 'galka-update-badge';
        badge.textContent = 'ошибка';
      }
      setButtonsDisabled(false);
    }
  }

  function startPolling() {
    if (busyPoll) clearInterval(busyPoll);
    busyPoll = setInterval(async () => {
      const data = await readStatus();
      if (!data) return;
      const state = data.lastOperation?.state;
      if (!data.busy && !['starting', 'running', 'rolling_back'].includes(state)) {
        clearInterval(busyPoll);
        busyPoll = null;
        if (state === 'success' && data.lastOperation?.requiresReload) {
          setTimeout(() => location.reload(), 900);
        }
      }
    }, POLL_MS);
  }

  async function installUpdate() {
    setButtonsDisabled(true);
    try {
      const result = await apiPost('/api/live/updater/update', { confirmation: 'INSTALL_GALKA_UPDATE' });
      if (!result?.started) {
        await checkNow(false);
        return;
      }
      renderSteps({ state: 'starting', steps: [], message: `${result.updateType === 'ui' ? 'UI' : 'Backend'}: ${result.fromShort} → ${result.targetShort}` });
      startPolling();
    } catch (error) {
      renderSteps({ state: 'failed', error: error.message });
      setButtonsDisabled(false);
      await readStatus();
    }
  }

  async function restartGalka() {
    setButtonsDisabled(true);
    try {
      const result = await apiPost('/api/live/updater/restart', { confirmation: 'RESTART_GALKA' });
      if (result?.started) startPolling();
    } catch (error) {
      renderSteps({ state: 'failed', error: error.message });
      setButtonsDisabled(false);
    }
  }

  async function rollbackUpdate() {
    setButtonsDisabled(true);
    try {
      const result = await apiPost('/api/live/updater/rollback', { confirmation: 'ROLLBACK_GALKA_UPDATE' });
      if (result?.started) startPolling();
    } catch (error) {
      renderSteps({ state: 'failed', error: error.message });
      setButtonsDisabled(false);
    }
  }

  function boot() {
    ensureUi();
    setTimeout(() => checkNow(false), 2200);
    autoCheckTimer = setInterval(() => {
      if (!document.hidden && !busyPoll) checkNow(false);
    }, CHECK_INTERVAL_MS);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && !busyPoll) readStatus();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
