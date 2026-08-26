(() => {
  'use strict';

  const sessionToken = sessionStorage.getItem('galkaLiveSession') || '';
  let statusCache = null;
  let busy = false;

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
  }

  function ensureCard() {
    let card = document.getElementById('galkaAgentApiCard');
    if (card) return card;
    const anchor = document.querySelector('.control-center-entry');
    if (!anchor) return null;
    card = document.createElement('div');
    card.id = 'galkaAgentApiCard';
    card.className = 'agent-api-card';
    card.innerHTML = `
      <div class="agent-api-head"><b>API для агента</b><span id="galkaAgentApiBadge" class="agent-api-badge off">проверка</span></div>
      <div id="galkaAgentApiUrl" class="agent-api-url">Отдельный read-only канал для OpenClaw / Детектива.</div>
      <div class="agent-api-actions">
        <button id="galkaAgentApiRefresh" type="button">Проверить</button>
        <button id="galkaAgentApiCopy" type="button" disabled>Скопировать подключение</button>
      </div>
      <div id="galkaAgentApiNote" class="agent-api-note">Токен этого API не даёт права выставлять, снимать или закрывать ордера.</div>
    `;
    anchor.insertAdjacentElement('afterend', card);
    card.querySelector('#galkaAgentApiRefresh')?.addEventListener('click', () => refresh(true));
    card.querySelector('#galkaAgentApiCopy')?.addEventListener('click', copyConnection);
    return card;
  }

  async function requestStatus(secret = false) {
    if (!sessionToken) throw new Error('Нет локальной LIVE-сессии');
    const response = await fetch(`/api/live/agent-api/status${secret ? '?secret=1' : ''}`, {
      headers: { 'X-Galka-Session': sessionToken },
      credentials: 'same-origin',
      cache: 'no-store',
    });
    const payload = await response.json();
    if (!response.ok || payload?.ok === false) throw new Error(payload?.error || `HTTP ${response.status}`);
    return payload.data || {};
  }

  function render(status) {
    const card = ensureCard();
    if (!card) return;
    const badge = card.querySelector('#galkaAgentApiBadge');
    const url = card.querySelector('#galkaAgentApiUrl');
    const copy = card.querySelector('#galkaAgentApiCopy');
    const note = card.querySelector('#galkaAgentApiNote');
    const running = !!status?.running;
    badge.textContent = running ? 'READ ONLY · ON' : 'OFF';
    badge.className = 'agent-api-badge' + (running ? '' : ' off');
    url.innerHTML = running
      ? `Для агента: <b>${esc(status.preferredBaseUrl || status.localBaseUrl || '—')}</b>`
      : esc(status?.error || 'API агента сейчас не запущен.');
    copy.disabled = !running;
    const tail = Array.isArray(status?.tailscaleBaseUrls) && status.tailscaleBaseUrls.length
      ? 'Tailscale найден автоматически — можно подключать OpenClaw с другого устройства.'
      : 'Если OpenClaw находится на другом устройстве, нужен Tailscale-адрес телефона.';
    note.textContent = `Отдельный read-only порт. ${tail}`;
  }

  async function refresh(force = false) {
    if (busy || (!force && statusCache)) {
      if (statusCache) render(statusCache);
      return;
    }
    busy = true;
    try {
      statusCache = await requestStatus(false);
      render(statusCache);
    } catch (error) {
      render({ running: false, error: error.message });
    } finally {
      busy = false;
    }
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      const area = document.createElement('textarea');
      area.value = text;
      area.className = 'agent-api-copy-fallback';
      document.body.appendChild(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
      area.remove();
      return ok;
    }
  }

  async function copyConnection() {
    const card = ensureCard();
    const button = card?.querySelector('#galkaAgentApiCopy');
    if (!button || busy) return;
    busy = true;
    const old = button.textContent;
    button.textContent = 'Копирую…';
    try {
      const status = await requestStatus(true);
      const base = status.preferredBaseUrl || status.localBaseUrl;
      const token = status.token;
      if (!base || !token) throw new Error('Не удалось получить адрес или read-only токен');
      const text = [
        'GALKA Agent API — READ ONLY',
        `Base URL: ${base}`,
        `Authorization: Bearer ${token}`,
        `Schema: ${base}/schema`,
        `Snapshot: ${base}/snapshot?coin=BTC`,
        'Рекомендуемый polling: 10 секунд.',
        'API не умеет выставлять, отменять или закрывать ордера.',
      ].join('\n');
      const ok = await copyText(text);
      button.textContent = ok ? 'Скопировано ✓' : 'Не скопировано';
    } catch (error) {
      button.textContent = 'Ошибка';
      const note = card?.querySelector('#galkaAgentApiNote');
      if (note) note.textContent = error.message;
    } finally {
      busy = false;
      setTimeout(() => { button.textContent = old; }, 2200);
    }
  }

  function boot() {
    ensureCard();
    refresh(true);
    const drawer = document.getElementById('drawer');
    if (drawer) {
      new MutationObserver(() => {
        if (!drawer.classList.contains('hidden')) refresh(true);
      }).observe(drawer, { attributes: true, attributeFilter: ['class'] });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
