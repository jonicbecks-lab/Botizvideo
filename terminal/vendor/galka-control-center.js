(() => {
  'use strict';

  const openButton = document.getElementById('openControlCenter');
  const panel = document.getElementById('controlCenterPanel');
  const closeButton = document.getElementById('closeControlCenter');
  const content = document.getElementById('controlCenterContent');
  if (!openButton || !panel || !closeButton || !content) return;

  let current = null;
  let loading = false;
  let checking = false;

  const STATUS = {
    working: 'Работает',
    problem: 'Проблема',
    partial: 'Частично',
    disconnected: 'Не подключено',
    manual: 'Не проверено',
  };

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    })[char]);
  }

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

  async function apiPost(path, body) {
    const response = await fetch(path, {
      method: 'POST',
      headers: authHeaders(true),
      credentials: 'same-origin',
      cache: 'no-store',
      body: JSON.stringify(body || {}),
    });
    const payload = await response.json();
    if (!response.ok || payload?.ok === false) throw new Error(payload?.error || `HTTP ${response.status}`);
    return payload?.data;
  }

  function statusBadge(status) {
    const normalized = STATUS[status] ? status : 'manual';
    return `<span class="cc-status ${normalized}">${esc(STATUS[normalized])}</span>`;
  }

  function formatTime(ms) {
    const number = Number(ms);
    if (!Number.isFinite(number) || number <= 0) return '—';
    try {
      return new Date(number).toLocaleString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
    } catch (_) {
      return '—';
    }
  }

  function renderDrift(audit) {
    if (!audit?.warning && !(audit?.items || []).length) return '';
    const items = (audit.items || []).map((item) => `
      <div class="cc-drift-item">
        <b>${esc(item.title)}</b>
        <span>${esc(item.text)}</span>
      </div>
    `).join('');
    return `
      <section class="cc-section">
        <div class="cc-section-head"><h3>Проверка логики</h3>${statusBadge('partial')}</div>
        <div class="cc-drift">
          <div class="cc-drift-title">Сейчас приложение работает не полностью так, как описано в старой концепции проекта</div>
          <p>${esc(audit.warning || 'Найдены расхождения документации и текущей реализации.')}</p>
          ${items}
        </div>
      </section>
    `;
  }

  function renderFlow(flow) {
    const primary = (flow || []).filter((row) => !row.parallel);
    const parallel = (flow || []).filter((row) => row.parallel);
    const primaryHtml = primary.map((row, index) => `
      <article class="cc-flow-card" data-flow-card="${esc(row.id)}">
        <button class="cc-flow-main" type="button" data-flow-toggle="${esc(row.id)}" aria-expanded="false">
          <span class="cc-flow-index">${index + 1}</span>
          <span class="cc-flow-copy"><b>${esc(row.title)}</b><span>${esc(row.summary)}</span></span>
          <span class="cc-chevron">›</span>
        </button>
        <div class="cc-flow-detail">
          <div><strong>Получает:</strong> ${esc(row.receives)}</div>
          <div><strong>Делает:</strong> ${esc(row.does)}</div>
          <div><strong>Передаёт дальше:</strong> ${esc(row.outputs)}</div>
        </div>
      </article>
      ${index < primary.length - 1 ? '<div class="cc-flow-link" aria-hidden="true"></div>' : ''}
    `).join('');
    const parallelHtml = parallel.map((row) => `
      <div class="cc-flow-parallel">ПАРАЛЛЕЛЬНО · НЕ УПРАВЛЯЕТ СДЕЛКОЙ</div>
      <article class="cc-flow-card parallel" data-flow-card="${esc(row.id)}">
        <button class="cc-flow-main" type="button" data-flow-toggle="${esc(row.id)}" aria-expanded="false">
          <span class="cc-flow-index">R</span>
          <span class="cc-flow-copy"><b>${esc(row.title)}</b><span>${esc(row.summary)}</span></span>
          <span class="cc-chevron">›</span>
        </button>
        <div class="cc-flow-detail">
          <div><strong>Получает:</strong> ${esc(row.receives)}</div>
          <div><strong>Делает:</strong> ${esc(row.does)}</div>
          <div><strong>Результат:</strong> ${esc(row.outputs)}</div>
        </div>
      </article>
    `).join('');
    return `
      <section class="cc-section">
        <div class="cc-section-head"><h3>Как работает GALKA LIVE</h3></div>
        <p class="cc-section-note">Нажми на этап, чтобы увидеть, что он получает, делает и передаёт дальше.</p>
        <div class="cc-flow">${primaryHtml}${parallelHtml}</div>
      </section>
    `;
  }

  function renderCards(title, rows, note = '') {
    const cards = (rows || []).map((row) => `
      <article class="cc-card">
        <div class="cc-card-head"><b>${esc(row.title)}</b>${statusBadge(row.status)}</div>
        <p>${esc(row.detail || row.purpose || '')}${row.note ? ` ${esc(row.note)}` : ''}</p>
      </article>
    `).join('');
    return `
      <section class="cc-section">
        <div class="cc-section-head"><h3>${esc(title)}</h3></div>
        ${note ? `<p class="cc-section-note">${esc(note)}</p>` : ''}
        <div class="cc-grid">${cards}</div>
      </section>
    `;
  }

  function renderScope(scope) {
    const groups = [
      ['Работает сейчас', 'working'],
      ['Сделано частично', 'partial'],
      ['Запланировано', 'planned'],
      ['Известные проблемы/расхождения', 'known'],
    ];
    const boxes = groups.map(([title, key]) => {
      const items = (scope?.[key] || []).map((text) => `<li>${esc(text)}</li>`).join('');
      return `<div class="cc-scope-box"><h4>${esc(title)}</h4><ul>${items || '<li>Нет записей.</li>'}</ul></div>`;
    }).join('');
    return `
      <section class="cc-section">
        <div class="cc-section-head"><h3>Что уже есть / что ещё не сделано</h3></div>
        <div class="cc-scope">${boxes}</div>
      </section>
    `;
  }

  function renderStrategy(strategy, project) {
    const depths = (strategy?.depthsPct || []).map((value) => `${Number(value).toFixed(2)}%`).join(' · ');
    const weights = (strategy?.weightsPct || []).map((value) => `${Number(value).toFixed(0)}%`).join(' · ');
    return `
      <section class="cc-section">
        <div class="cc-section-head"><h3>Зафиксированная торговая логика</h3></div>
        <p class="cc-section-note">Это краткая контрольная карточка, чтобы будущие изменения не подменяли основную идею незаметно.</p>
        <div class="cc-strategy">
          <div><small>Монеты production</small><b>${esc((project?.coins || []).join(' / '))}</b></div>
          <div><small>Режим</small><b>${esc(`${project?.leverage || '—'}x ${project?.isolated ? 'isolated' : ''}`)}</b></div>
          <div><small>Глубины L1–L8</small><b>${esc(depths)}</b></div>
          <div><small>Распределение</small><b>${esc(weights)}</b></div>
          <div><small>Завершение</small><b>${esc(strategy?.campaignCompletion || '—')}</b></div>
          <div><small>Research влияет на ордера?</small><b>${strategy?.researchAffectsTrading ? 'Да' : 'Нет'}</b></div>
          <div><small>Автоматический stop-loss</small><b>${project?.automaticStopLoss ? 'Есть' : 'Нет'}</b></div>
          <div><small>Лимит маржи</small><b>${Number(project?.maxMarginFraction || 0) * 100}% капитала</b></div>
        </div>
      </section>
    `;
  }

  function renderCheck(check) {
    const rows = (check?.steps || []).map((row) => `
      <div class="cc-check-row">
        ${statusBadge(row.status)}
        <div><b>${esc(row.title)}${row.ms != null ? ` · ${Number(row.ms).toFixed(0)} ms` : ''}</b><span>${esc(row.detail)}</span></div>
      </div>
    `).join('');
    const meta = check?.checkedAtMs
      ? `Последняя проверка: ${formatTime(check.checkedAtMs)} · ${(Number(check.elapsedMs || 0) / 1000).toFixed(1)} сек`
      : 'Ещё не запускалась';
    return `
      <section class="cc-section" id="ccCheckSection">
        <div class="cc-section-head"><h3>Проверить ключевую цепочку</h3></div>
        <div class="cc-check-box">
          <div class="cc-check-actions">
            <button class="cc-check-button" id="ccRunCheck" type="button" ${checking ? 'disabled' : ''}>${checking ? 'Проверяем…' : 'Проверить сейчас'}</button>
            <span class="cc-check-meta">${esc(meta)}</span>
          </div>
          <div class="cc-check-results">${rows || '<div class="cc-section-note">Будут проверены LIVE-монитор, цена, свечи, счёт, ордера, согласованность с биржей, research и updater. Торговые команды не отправляются.</div>'}</div>
          <div class="cc-check-note">${esc(check?.note || 'Проверка read-only. Если этап нельзя безопасно проверить автоматически, он будет отмечен «Не проверено», а не зелёным.')}</div>
        </div>
      </section>
    `;
  }

  function render(data) {
    current = data;
    const project = data?.project || {};
    const safeStatus = project.safeMode ? 'problem' : 'working';
    content.innerHTML = `
      <section class="cc-hero">
        <div class="cc-hero-title"><h2>${esc(project.title || 'GALKA LIVE')}</h2>${statusBadge(safeStatus)}</div>
        <p>${esc(project.concept || '')}</p>
        <div class="cc-facts">
          <span class="cc-fact"><strong>${esc(project.network || '—')}</strong></span>
          <span class="cc-fact">Версия <strong>${esc(data?.version?.short || '—')}</strong></span>
          <span class="cc-fact"><strong>${esc((project.coins || []).join(' / '))}</strong></span>
          <span class="cc-fact">Активных GALKA: <strong>${Number(project.activeCampaigns || 0)}</strong></span>
          <span class="cc-fact">LIVE: <strong>${project.liveEnabled ? 'включён' : 'выключен'}</strong></span>
          ${project.safeMode ? `<span class="cc-fact">SAFE MODE: <strong>${esc(project.safeModeReason || 'включён')}</strong></span>` : ''}
        </div>
      </section>

      ${renderDrift(data.logicAudit)}
      ${renderFlow(data.flow)}
      ${renderCards('Состояние системы', data.system)}
      ${renderCards('Подключения', data.connections, 'Зелёный статус показывается только там, где runtime действительно может подтвердить соединение. Best-effort процессы не получают ложный зелёный статус.')}
      ${renderStrategy(data.strategy, project)}
      ${renderScope(data.scope)}
      ${renderCheck(data.lastCheck)}

      <section class="cc-section">
        <button class="cc-refresh" id="ccRefreshOverview" type="button">Обновить состояние без глубокой проверки</button>
      </section>
    `;
  }

  async function loadOverview({ preserveScroll = false } = {}) {
    if (loading) return;
    loading = true;
    const top = panel.scrollTop;
    if (!current) content.innerHTML = '<div class="control-center-loading">Загрузка реального состояния проекта…</div>';
    try {
      const data = await apiGet('/api/live/control-center');
      render(data);
      if (preserveScroll) panel.scrollTop = top;
    } catch (error) {
      content.innerHTML = `<div class="control-center-error">Не удалось открыть Центр управления: ${esc(error?.message || error)}</div>`;
    } finally {
      loading = false;
    }
  }

  async function runCheck() {
    if (checking) return;
    checking = true;
    if (current) render(current);
    try {
      const coin = String(document.getElementById('symbolSelect')?.value || 'BTC').toUpperCase();
      const check = await apiPost('/api/live/control-center/check', { coin });
      if (!current) current = {};
      current.lastCheck = check;
      render(current);
      document.getElementById('ccCheckSection')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      window.setTimeout(() => loadOverview({ preserveScroll: true }), 300);
    } catch (error) {
      if (!current) current = {};
      current.lastCheck = {
        checkedAtMs: Date.now(),
        overall: 'problem',
        steps: [{ id: 'check-error', title: 'Проверка', status: 'problem', detail: error?.message || String(error) }],
        note: 'Проверка остановилась с ошибкой; торговые команды не выполнялись.',
      };
      render(current);
    } finally {
      checking = false;
      if (current) render(current);
    }
  }

  function openCenter() {
    document.getElementById('closeDrawer')?.click();
    panel.classList.remove('hidden');
    panel.setAttribute('aria-hidden', 'false');
    document.body.classList.add('control-center-open');
    panel.scrollTop = 0;
    loadOverview();
  }

  function closeCenter() {
    panel.classList.add('hidden');
    panel.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('control-center-open');
  }

  content.addEventListener('click', (event) => {
    const toggle = event.target.closest('[data-flow-toggle]');
    if (toggle) {
      const id = toggle.getAttribute('data-flow-toggle');
      const card = content.querySelector(`[data-flow-card="${CSS.escape(id || '')}"]`);
      const expanded = !card?.classList.contains('expanded');
      card?.classList.toggle('expanded', expanded);
      toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      return;
    }
    if (event.target.closest('#ccRunCheck')) {
      runCheck();
      return;
    }
    if (event.target.closest('#ccRefreshOverview')) loadOverview({ preserveScroll: true });
  });

  openButton.addEventListener('click', openCenter);
  closeButton.addEventListener('click', closeCenter);
  panel.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeCenter();
  });
})();
