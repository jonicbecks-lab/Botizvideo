(() => {
  'use strict';

  const STRATEGY = 'galka-v2-fibo-100';
  const ACTIVE = new Set(['placing', 'waiting', 'open', 'closing', 'canceling', 'emergency', 'recovery']);
  const FIBS = [0.50, 0.618, 0.705, 0.786];
  const LOWER = [0.15, 0.30, 0.45, 0.60];
  const state = {
    chart: null,
    series: null,
    legacyLines: new Set(),
    draftLines: [],
    activeLines: [],
    latestStatus: null,
    activeCampaignId: null,
    draftSignature: '',
    activeSignature: '',
    renderingDetails: false,
  };

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
  }

  function money(value) {
    return '$' + Number(value || 0).toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function fmtPrice(value) {
    return Number(value || 0).toFixed(2);
  }

  function installStyles() {
    const style = document.createElement('style');
    style.textContent = `
      .galka-v2-preview-backdrop{position:fixed;inset:0;z-index:10050;background:rgba(2,5,9,.78);display:flex;align-items:flex-end;justify-content:center;padding:12px}
      .galka-v2-preview-backdrop.hidden{display:none}
      .galka-v2-preview{width:min(620px,100%);max-height:88vh;overflow:auto;background:#0d121a;border:1px solid #2a3442;border-radius:18px;padding:16px;color:#eef3f8;box-shadow:0 20px 70px rgba(0,0,0,.55)}
      .galka-v2-preview h2{font-size:18px;margin:0 0 4px}.galka-v2-preview p{margin:0 0 12px;color:#9aa8b8;font-size:13px}
      .galka-v2-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:10px 0 14px}
      .galka-v2-stat{background:#121a24;border:1px solid #26303e;border-radius:10px;padding:9px}.galka-v2-stat small{display:block;color:#8795a6;font-size:11px}.galka-v2-stat b{font-size:15px}
      .galka-v2-level{display:grid;grid-template-columns:70px 1fr auto;gap:8px;align-items:center;padding:8px 4px;border-top:1px solid #202936;font-size:13px}.galka-v2-level small{color:#8d9baa}.galka-v2-level.upper b{color:#42c9e8}.galka-v2-level.lower b{color:#aab5c3}.galka-v2-level.galka b{color:#ffb547}
      .galka-v2-actions{display:flex;gap:10px;position:sticky;bottom:-16px;background:#0d121a;padding:14px 0 2px}.galka-v2-actions button{flex:1;min-height:44px;border:0;border-radius:11px;font-weight:700}.galka-v2-cancel{background:#202936;color:#d7dee7}.galka-v2-confirm{background:#16a36a;color:white}.galka-v2-confirm:disabled{opacity:.38}
      .galka-v2-warning{padding:9px 10px;border-radius:10px;background:#2a1b10;color:#ffc787;font-size:12px;margin:8px 0}
      .galka-v2-chip{display:inline-flex;align-items:center;gap:5px;padding:3px 7px;border:1px solid #314052;border-radius:999px;color:#9fdcf0;font-size:11px;margin-left:6px}
    `;
    document.head.appendChild(style);
  }

  function captureChart() {
    const charts = window.LightweightCharts;
    if (!charts?.createChart || charts.createChart.__galkaV2Wrapped) return;
    const originalCreate = charts.createChart.bind(charts);

    function wrappedCreate(container, options) {
      const chart = originalCreate(container, options);
      if (container?.id === 'chart') {
        state.chart = chart;
        const originalAdd = chart.addSeries?.bind(chart);
        if (originalAdd) {
          chart.addSeries = function wrappedAdd(seriesType, seriesOptions) {
            const series = originalAdd(seriesType, seriesOptions);
            if (!state.series) {
              state.series = series;
              const originalCreateLine = series.createPriceLine?.bind(series);
              if (originalCreateLine) {
                series.createPriceLine = function wrappedCreateLine(lineOptions) {
                  const priceLine = originalCreateLine(lineOptions);
                  if (/^L[1-8]$/.test(String(lineOptions?.title || ''))) {
                    state.legacyLines.add(priceLine);
                  }
                  return priceLine;
                };
              }
            }
            return series;
          };
        }
      }
      return chart;
    }

    wrappedCreate.__galkaV2Wrapped = true;

    // galka-structure-v3 deliberately freezes the LightweightCharts facade.
    // Never mutate a property on that frozen object: replace the facade with a
    // new frozen object that preserves every existing wrapper and adds ours.
    window.LightweightCharts = Object.freeze({
      ...charts,
      createChart: wrappedCreate,
    });
  }

  function removeLines(lines) {
    if (!state.series) return;
    for (const priceLine of lines) {
      try { state.series.removePriceLine(priceLine); } catch (_) {}
    }
    lines.length = 0;
  }

  function removeLegacyLines() {
    if (!state.series) return;
    for (const priceLine of state.legacyLines) {
      try { state.series.removePriceLine(priceLine); } catch (_) {}
    }
    state.legacyLines.clear();
  }

  function priceAtY(y) {
    const chart = state.chart;
    if (!chart) return 0;
    const geometry = chart.geometry?.();
    const rows = chart.lastRows?.length ? chart.lastRows : chart.visibleWindow?.().rows;
    const range = chart.currentPriceRange?.(rows || []);
    if (!geometry || !range || !(range.span > 0)) return 0;
    const clamped = Math.max(geometry.top, Math.min(geometry.bottom, Number(y)));
    return range.max - ((clamped - geometry.top) / geometry.plotHeight) * range.span;
  }

  function line(price, title, color, width = 1) {
    if (!state.series || !(Number(price) > 0)) return null;
    try {
      return state.series.createPriceLine({
        price: Number(price),
        color,
        lineWidth: width,
        lineStyle: window.LightweightCharts?.LineStyle?.Dashed ?? 2,
        axisLabelVisible: true,
        title,
      });
    } catch (_) {
      return null;
    }
  }

  function renderDraftLevels() {
    if (!state.series || state.activeCampaignId) {
      if (state.draftLines.length) removeLines(state.draftLines);
      state.draftSignature = '';
      return;
    }
    const right = document.querySelector('.galka-structure-boundary-right');
    const overlay = document.querySelector('.galka-structure-v3-overlay');
    const input = document.getElementById('galkaInput');
    if (!right || !overlay || overlay.classList.contains('hidden') || right.getAttribute('visibility') === 'hidden') {
      if (state.draftLines.length) removeLines(state.draftLines);
      state.draftSignature = '';
      return;
    }
    const match = String(right.getAttribute('transform') || '').match(/translate\(([-\d.]+)[ ,]([-\d.]+)\)/);
    const galka = Number(input?.value || 0);
    const high = match ? priceAtY(Number(match[2])) : 0;
    if (!(galka > 0 && high > galka)) {
      if (state.draftLines.length) removeLines(state.draftLines);
      state.draftSignature = '';
      return;
    }
    const signature = `${galka.toFixed(8)}|${high.toFixed(8)}`;
    if (signature === state.draftSignature) return;
    state.draftSignature = signature;
    removeLines(state.draftLines);
    const span = high - galka;
    FIBS.forEach((fib) => {
      const value = high - fib * span;
      const created = line(value, `F${fib}`, '#33bfdc');
      if (created) state.draftLines.push(created);
    });
    const galkaLine = line(galka, 'GALKA', '#ffad3d', 2);
    if (galkaLine) state.draftLines.push(galkaLine);
    LOWER.forEach((depth, index) => {
      const created = line(galka * (1 - depth / 100), `D${index + 1}`, '#7f8a99');
      if (created) state.draftLines.push(created);
    });
  }

  function renderActiveLines(campaign) {
    const signature = campaign ? JSON.stringify({
      id: campaign.id,
      target: campaign.v2TargetPrice,
      levels: (campaign.levels || []).map((row) => [row.index, row.price, row.status, row.filledSize]),
    }) : '';
    if (signature === state.activeSignature) return;
    state.activeSignature = signature;
    removeLines(state.activeLines);
    if (!campaign || !state.series) return;
    removeLegacyLines();
    for (const level of campaign.levels || []) {
      const filled = Number(level.filledSize || 0) > 0;
      const color = filled ? '#16c784' : level.zone === 'upper' ? '#33bfdc' : '#7f8a99';
      const created = line(level.price, level.label || `V${level.index}`, color, level.label === 'GALKA' ? 2 : 1);
      if (created) state.activeLines.push(created);
    }
    if (Number(campaign.v2TargetPrice) > 0 && Math.abs(Number(campaign.v2TargetPrice) - Number(campaign.galkaPrice)) > Number(campaign.galkaPrice) * 1e-7) {
      const target = line(campaign.v2TargetPrice, 'TP +1% NET', '#b17cff', 2);
      if (target) state.activeLines.push(target);
    }
  }

  function previewModal() {
    let backdrop = document.getElementById('galkaV2PreviewBackdrop');
    if (backdrop) return backdrop;
    backdrop = document.createElement('div');
    backdrop.id = 'galkaV2PreviewBackdrop';
    backdrop.className = 'galka-v2-preview-backdrop hidden';
    backdrop.innerHTML = '<section class="galka-v2-preview" id="galkaV2PreviewCard"></section>';
    document.body.appendChild(backdrop);
    return backdrop;
  }

  function showV2Preview(data) {
    return new Promise((resolve) => {
      const backdrop = previewModal();
      const card = document.getElementById('galkaV2PreviewCard');
      const readOnly = !data.liveEnabled;
      const safe = !!state.latestStatus?.system?.safeMode;
      const canConfirm = !readOnly && !safe;
      const levels = (data.levels || []).map((level) => {
        const kind = level.label === 'GALKA' ? 'galka' : level.zone === 'upper' ? 'upper' : 'lower';
        return `<div class="galka-v2-level ${kind}"><b>${esc(level.label)}</b><span>${fmtPrice(level.price)}</span><small>${money(level.notional)}</small></div>`;
      }).join('');
      const warning = readOnly
        ? '<div class="galka-v2-warning">READ ONLY: расчёт проверяется, реальные ордера не отправляются.</div>'
        : safe
          ? `<div class="galka-v2-warning">SAFE MODE: ${esc(state.latestStatus?.system?.safeModeReason || 'нужна сверка')}</div>`
          : '<div class="galka-v2-warning">После подтверждения будут отправлены реальные ордера Hyperliquid.</div>';
      card.innerHTML = `
        <h2>GALKA V2 <span class="galka-v2-chip">$100 · 10x</span></h2>
        <p>Fibonacci по правой ноге + нижняя сетка под GALKA.</p>
        ${warning}
        <div class="galka-v2-grid">
          <div class="galka-v2-stat"><small>Верх</small><b>${(Number(data.upperShare || 0) * 100).toFixed(1)}%</b></div>
          <div class="galka-v2-stat"><small>Низ</small><b>${(Number(data.lowerShare || 0) * 100).toFixed(1)}%</b></div>
          <div class="galka-v2-stat"><small>Маржа</small><b>${money(data.requiredMargin)}</b></div>
          <div class="galka-v2-stat"><small>Полный набор → GALKA</small><b>${Number(data.fullFillNetAtGalka || 0) >= 0 ? '+' : ''}${money(data.fullFillNetAtGalka)}</b></div>
        </div>
        <div>${levels}</div>
        <div class="galka-v2-actions">
          <button class="galka-v2-cancel" type="button">${readOnly ? 'Закрыть' : 'Отмена'}</button>
          <button class="galka-v2-confirm" type="button" ${canConfirm ? '' : 'disabled'}>Выставить реальные лимитки</button>
        </div>`;
      backdrop.classList.remove('hidden');
      const finish = (value) => {
        backdrop.classList.add('hidden');
        resolve(value);
      };
      card.querySelector('.galka-v2-cancel').onclick = () => finish(false);
      card.querySelector('.galka-v2-confirm').onclick = () => finish(true);
      backdrop.onclick = (event) => { if (event.target === backdrop) finish(false); };
    });
  }

  function interceptCampaignSubmit() {
    if (window.fetch.__galkaV2Wrapped) return;
    const originalFetch = window.fetch.bind(window);
    async function wrappedFetch(input, init = {}) {
      const url = typeof input === 'string' ? input : input?.url || '';
      const method = String(init?.method || (typeof input !== 'string' ? input?.method : 'GET') || 'GET').toUpperCase();
      if (method === 'POST' && /\/api\/live\/campaign(?:\?|$)/.test(url) && typeof init?.body === 'string') {
        let body = null;
        try { body = JSON.parse(init.body); } catch (_) {}
        if (body?.researchSetup && body?.confirmation === 'PLACE_REAL_ORDERS') {
          const previewResponse = await originalFetch('/api/live/preview', {
            ...init,
            method: 'POST',
            body: JSON.stringify({
              coin: body.coin,
              galkaPrice: body.galkaPrice,
              researchSetup: body.researchSetup,
            }),
            credentials: 'same-origin',
            cache: 'no-store',
          });
          let previewPayload = null;
          try { previewPayload = await previewResponse.json(); } catch (_) {}
          if (!previewResponse.ok || previewPayload?.ok === false) {
            return new Response(JSON.stringify(previewPayload || { ok: false, error: 'Не удалось рассчитать GALKA V2' }), {
              status: previewResponse.status || 400,
              headers: { 'Content-Type': 'application/json; charset=utf-8' },
            });
          }
          const approved = await showV2Preview(previewPayload.data);
          if (!approved) {
            return new Response(JSON.stringify({
              ok: false,
              error: previewPayload.data?.liveEnabled
                ? 'GALKA V2 отменена — реальные ордера не отправлены'
                : 'READ ONLY: расчёт GALKA V2 проверен; реальные ордера не отправлены',
            }), {
              status: 409,
              headers: { 'Content-Type': 'application/json; charset=utf-8' },
            });
          }
        }
      }
      return originalFetch(input, init);
    }
    wrappedFetch.__galkaV2Wrapped = true;
    window.fetch = wrappedFetch;
  }

  function currentV2Campaign(status) {
    const coin = String(document.getElementById('symbolSelect')?.value || 'BTC').toUpperCase();
    const campaign = status?.campaigns?.[coin];
    return campaign && campaign.strategyVersion === STRATEGY && ACTIVE.has(campaign.status) ? campaign : null;
  }

  function renderV2Details(campaign, status) {
    if (!campaign) return;
    const coin = String(campaign.coin || '').toUpperCase();
    const position = status?.accountState?.positions?.[coin];
    const filled = (campaign.levels || []).filter((row) => Number(row.filledSize || 0) > 0).length;
    const statusEl = document.getElementById('campaignStatus');
    if (statusEl) {
      statusEl.textContent = position && Math.abs(Number(position.size || 0)) > 0
        ? `${coin} · V2 · ${filled}/9 · ${Number(position.unrealizedPnl || 0) >= 0 ? '+' : ''}${money(position.unrealizedPnl)}`
        : campaign.v2UpperDone
          ? `${coin} · V2 · верх закрыт · ждём низ`
          : `${coin} · V2 · ждём ${filled}/9`;
    }
    const riskMode = document.getElementById('riskMode');
    if (riskMode) riskMode.textContent = 'V2 · $100 · 10x';

    const details = document.getElementById('campaignDetails');
    if (!details || state.renderingDetails) return;
    const rows = (campaign.levels || []).map((level) => {
      const zone = level.zone === 'upper' ? 'FIB' : 'LOW';
      const filledText = Number(level.filledSize || 0) > 0 ? ` · filled ${Number(level.filledSize).toPrecision(4)}` : '';
      return `<div class="level-row"><span><b>${esc(level.label || `V${level.index}`)}</b> <small>${zone}</small></span><span><b>${fmtPrice(level.price)}</b> <small>${money(level.notional)} · ${esc(level.status)}${filledText}</small></span></div>`;
    }).join('');
    const target = campaign.v2TargetMode === 'upper_1pct_net'
      ? `TP верх: ${fmtPrice(campaign.v2TargetPrice)} (+1% net)`
      : campaign.v2TargetMode === 'galka'
        ? `Выход: GALKA ${fmtPrice(campaign.galkaPrice)}`
        : 'Target появится после первого fill';
    state.renderingDetails = true;
    details.innerHTML = `
      <div class="campaign-card">
        <div class="row"><span><small>GALKA V2</small><b>${fmtPrice(campaign.galkaPrice)}</b></span><span><small>Статус</small><b>${esc(campaign.status)}</b></span></div>
        <div class="row"><span><small>Верх / низ</small><b>${(Number(campaign.upperShare || 0) * 100).toFixed(1)}% / ${(Number(campaign.lowerShare || 0) * 100).toFixed(1)}%</b></span><span><small>Маржа</small><b>${money(campaign.requiredMargin || 100)}</b></span></div>
        <div class="row"><span><small>Режим выхода</small><b>${esc(target)}</b></span><span><small>Верх реализовано</small><b>${money(campaign.v2UpperRealizedPnl || 0)}</b></span></div>
      </div>${rows}`;
    state.renderingDetails = false;
  }

  async function pollStatus() {
    const token = sessionStorage.getItem('galkaLiveSession') || '';
    const headers = {};
    if (token) headers['X-Galka-Session'] = token;
    try {
      const response = await fetch('/api/live/status', {
        headers,
        cache: 'no-store',
        credentials: 'same-origin',
      });
      const payload = await response.json();
      if (!response.ok || payload?.ok === false) return;
      state.latestStatus = payload.data;
      const campaign = currentV2Campaign(payload.data);
      state.activeCampaignId = campaign?.id || null;
      if (campaign) {
        removeLines(state.draftLines);
        state.draftSignature = '';
        removeLegacyLines();
        renderActiveLines(campaign);
        renderV2Details(campaign, payload.data);
      } else {
        if (state.activeLines.length) removeLines(state.activeLines);
        state.activeSignature = '';
      }
    } catch (_) {}
  }

  installStyles();
  // Install fetch interception first so a chart decoration problem can never
  // bypass the V2 preview/safety confirmation path.
  interceptCampaignSubmit();
  captureChart();
  setInterval(renderDraftLevels, 140);
  setInterval(pollStatus, 900);
  window.addEventListener('load', () => { renderDraftLevels(); pollStatus(); });
})();
