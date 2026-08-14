(() => {
  'use strict';

  const STORAGE_KEY = 'galka-live-trade-markers-v1';
  const MAX_MARKERS = 240;
  const VALID_COINS = new Set(['BTC', 'ETH', 'SOL']);
  const ENTRY_COLOR = '#16c784';
  const EXIT_COLOR = '#ff9800';
  const TEXT_COLOR = '#dce4ee';
  const LABEL_BG = 'rgba(11, 15, 21, 0.86)';

  let latestStatus = null;
  let chart = null;

  function finite(value, fallback = null) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function eventCoin(event) {
    const explicit = String(event?.meta?.coin || '').toUpperCase();
    if (VALID_COINS.has(explicit)) return explicit;
    const match = String(event?.message || '').match(/^(BTC|ETH|SOL):/);
    return match ? match[1] : '';
  }

  function eventSeconds(value) {
    const millis = Date.parse(String(value || ''));
    return Number.isFinite(millis) ? Math.floor(millis / 1000) : 0;
  }

  function loadStore() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  function saveStore(store) {
    try {
      for (const coin of VALID_COINS) {
        const rows = Object.values(store[coin] || {})
          .sort((left, right) => Number(left.time || 0) - Number(right.time || 0))
          .slice(-MAX_MARKERS);
        store[coin] = Object.fromEntries(rows.map((row) => [row.key, row]));
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
    } catch (_) {
      // Best-effort display state only.
    }
  }

  function campaignFromStatus(coin, campaignId) {
    const candidate = latestStatus?.campaigns?.[coin];
    if (!candidate) return null;
    return String(candidate.id || '') === String(campaignId || '') ? candidate : null;
  }

  function campaignNetPnl(campaign) {
    if (!campaign) return null;
    const final = finite(campaign.finalClosedPnl);
    if (final != null) return final;
    const gross = finite(campaign.cycleClosedPnl, 0) + finite(campaign.l1RealizedPnl, 0);
    const fees = finite(campaign.cycleFees ?? campaign.fees, 0);
    return gross - fees;
  }

  function upsert(store, coin, marker) {
    if (!VALID_COINS.has(coin) || !marker?.key || !(Number(marker.time) > 0)) return;
    store[coin] ||= {};
    const previous = store[coin][marker.key] || {};
    const next = { ...previous, ...marker, coin };
    if (!(Number(marker.price) > 0) && Number(previous.price) > 0) next.price = previous.price;
    store[coin][marker.key] = next;
  }

  function ingestStatus(status) {
    if (!status || typeof status !== 'object') return;
    latestStatus = status;
    const store = loadStore();

    for (const event of status.events || []) {
      const coin = eventCoin(event);
      if (!coin) continue;
      const message = String(event.message || '');
      const campaignId = String(event?.meta?.campaignId || '');
      const time = eventSeconds(event.time);
      if (!campaignId || !time) continue;

      const fillMatch = message.match(/L(\d+)\s+исполнена/);
      if (event.type === 'fill' && fillMatch) {
        const level = Number(fillMatch[1]);
        upsert(store, coin, {
          key: `${campaignId}:entry:${level}`,
          campaignId,
          kind: 'entry',
          level,
          time,
          price: finite(event?.meta?.price),
        });
      }

      const completed = /кампания завершена|recovery завершён flat|позиция фактически закрыта/i.test(message);
      if (completed) {
        const campaign = campaignFromStatus(coin, campaignId);
        const eventPnl = finite(event?.meta?.pnl);
        upsert(store, coin, {
          key: `${campaignId}:exit`,
          campaignId,
          kind: 'exit',
          time,
          price: finite(campaign?.galkaPrice),
          pnl: eventPnl != null ? eventPnl : campaignNetPnl(campaign),
        });
      }
    }

    for (const [coin, campaign] of Object.entries(status.campaigns || {})) {
      if (!VALID_COINS.has(coin) || !campaign?.id || !campaign.completedAt) continue;
      const hadTrade = Number(campaign.cycleDeepest || 0) > 0 ||
        (campaign.levels || []).some((level) => Number(level.filledSize || 0) > 0);
      if (!hadTrade) continue;
      const time = eventSeconds(campaign.completedAt);
      if (!time) continue;
      upsert(store, coin, {
        key: `${campaign.id}:exit`,
        campaignId: String(campaign.id),
        kind: 'exit',
        time,
        price: finite(campaign.galkaPrice),
        pnl: campaignNetPnl(campaign),
      });
    }

    saveStore(store);
    renderSelectedCoin();
  }

  function compactMarkers(coin) {
    const store = loadStore();
    const all = Object.values(store[coin] || {});
    const campaigns = new Map();
    for (const row of all) {
      const id = String(row.campaignId || '');
      if (!id) continue;
      if (!campaigns.has(id)) campaigns.set(id, []);
      campaigns.get(id).push(row);
    }

    const result = [];
    for (const rows of campaigns.values()) {
      const exit = rows.find((row) => row.kind === 'exit');
      if (!exit) continue;
      const entries = rows
        .filter((row) => row.kind === 'entry')
        .sort((left, right) => Number(left.level || 0) - Number(right.level || 0));
      const deepest = entries.reduce((best, row) =>
        Number(row.level || 0) > Number(best?.level || 0) ? row : best, null);

      for (const entry of entries) {
        result.push({ ...entry, text: entry === deepest ? `L${entry.level}` : '' });
      }
      result.push({
        ...exit,
        text: Number.isFinite(Number(exit.pnl))
          ? `${Number(exit.pnl) >= 0 ? '+' : ''}$${Number(exit.pnl).toFixed(2)}`
          : '✓',
      });
    }
    return result.sort((left, right) => Number(left.time) - Number(right.time));
  }

  function selectedCoin() {
    const coin = String(document.getElementById('symbolSelect')?.value || 'BTC').toUpperCase();
    return VALID_COINS.has(coin) ? coin : 'BTC';
  }

  function renderSelectedCoin() {
    if (!chart || typeof chart.setTradeMarkers !== 'function') return;
    chart.setTradeMarkers(compactMarkers(selectedCoin()));
  }

  function nearestRowIndex(rows, timestamp) {
    if (!rows.length) return -1;
    let low = 0;
    let high = rows.length - 1;
    while (low < high) {
      const mid = Math.floor((low + high) / 2);
      if (Number(rows[mid].time) < timestamp) low = mid + 1;
      else high = mid;
    }
    if (low > 0) {
      const before = Math.abs(Number(rows[low - 1].time) - timestamp);
      const after = Math.abs(Number(rows[low].time) - timestamp);
      if (before <= after) return low - 1;
    }
    return low;
  }

  function drawMarkerOverlay(target) {
    const markers = target.tradeMarkers || [];
    const ctx = target.ctx;
    if (!ctx || !markers.length) return;

    const geometry = target.geometry?.();
    const windowState = target.visibleWindow?.();
    const rows = windowState?.rows || [];
    const all = windowState?.all || rows;
    const rangeRows = rows.length ? rows : all.slice(-1);
    const range = target.currentPriceRange?.(rangeRows);
    if (!geometry || !rows.length || !range || !(Number(range.span) > 0)) return;

    const firstTime = Number(rows[0].time);
    const lastTime = Number(rows.at(-1).time);
    const count = Math.max(1, Number(windowState?.count || rows.length));
    const start = Number(windowState?.start ?? 0);
    const dataStart = Number(windowState?.dataStart ?? start);
    const slot = geometry.plotWidth / count;
    const yForPrice = (value) => geometry.top + (range.max - value) / range.span * geometry.plotHeight;

    ctx.save();
    ctx.font = 'bold 10px system-ui, sans-serif';
    ctx.textBaseline = 'middle';

    for (const marker of markers) {
      const time = Number(marker.time || 0);
      if (time < firstTime || time > lastTime) continue;
      const rowIndex = nearestRowIndex(rows, time);
      if (rowIndex < 0) continue;
      const row = rows[rowIndex];
      const logicalIndex = dataStart + rowIndex;
      const x = geometry.left + (logicalIndex - start + 0.5) * slot;
      if (x < geometry.left - 8 || x > geometry.right + 8) continue;

      let markerPrice = finite(marker.price);
      if (markerPrice == null) markerPrice = marker.kind === 'entry' ? Number(row.low) : Number(row.close);
      let y = yForPrice(markerPrice);
      y = Math.max(geometry.top + 6, Math.min(geometry.bottom - 6, y));

      if (marker.kind === 'entry') {
        ctx.fillStyle = ENTRY_COLOR;
        ctx.globalAlpha = marker.text ? 0.95 : 0.68;
        ctx.beginPath();
        ctx.arc(x, y, marker.text ? 3.5 : 2.5, 0, Math.PI * 2);
        ctx.fill();
        if (marker.text) {
          ctx.globalAlpha = 0.92;
          ctx.fillStyle = LABEL_BG;
          const width = ctx.measureText(marker.text).width + 8;
          const labelX = Math.max(geometry.left, Math.min(geometry.right - width, x - width / 2));
          const labelY = Math.max(geometry.top + 9, y - 12);
          ctx.fillRect(labelX, labelY - 7, width, 14);
          ctx.fillStyle = ENTRY_COLOR;
          ctx.textAlign = 'center';
          ctx.fillText(marker.text, labelX + width / 2, labelY);
        }
      } else {
        ctx.globalAlpha = 0.96;
        ctx.fillStyle = EXIT_COLOR;
        ctx.beginPath();
        ctx.moveTo(x, y - 4);
        ctx.lineTo(x + 4, y);
        ctx.lineTo(x, y + 4);
        ctx.lineTo(x - 4, y);
        ctx.closePath();
        ctx.fill();

        const text = String(marker.text || '✓');
        const width = ctx.measureText(text).width + 8;
        const labelX = Math.max(geometry.left, Math.min(geometry.right - width, x - width / 2));
        const labelY = Math.min(geometry.bottom - 9, y + 13);
        ctx.fillStyle = LABEL_BG;
        ctx.fillRect(labelX, labelY - 7, width, 14);
        ctx.fillStyle = Number(marker.pnl) < 0 ? '#ef5350' : TEXT_COLOR;
        ctx.textAlign = 'center';
        ctx.fillText(text, labelX + width / 2, labelY);
      }
    }
    ctx.restore();
  }

  const charts = window.LightweightCharts;
  if (charts?.createChart) {
    const originalCreateChart = charts.createChart;
    window.LightweightCharts = Object.freeze({
      ...charts,
      createChart(container, options) {
        const created = originalCreateChart(container, options);
        const originalDraw = created.draw.bind(created);
        created.tradeMarkers = [];
        created.draw = function drawWithTrades() {
          originalDraw();
          drawMarkerOverlay(created);
        };
        created.setTradeMarkers = (markers) => {
          created.tradeMarkers = Array.isArray(markers) ? markers.slice() : [];
          created.draw();
        };
        chart = created;
        window.GalkaTradeMarkers = Object.freeze({
          setMarkers: (markers) => created.setTradeMarkers(markers),
          refresh: renderSelectedCoin,
        });
        renderSelectedCoin();
        return created;
      },
    });
  }

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    try {
      const request = args[0];
      const url = typeof request === 'string' ? request : String(request?.url || '');
      if (url.includes('/api/live/status')) {
        response.clone().json().then((payload) => {
          if (payload?.ok !== false && payload?.data) ingestStatus(payload.data);
        }).catch(() => {});
      }
    } catch (_) {
      // Never interfere with the original API response.
    }
    return response;
  };

  document.addEventListener('DOMContentLoaded', () => {
    const symbol = document.getElementById('symbolSelect');
    symbol?.addEventListener('change', () => setTimeout(renderSelectedCoin, 0));
    renderSelectedCoin();
  });
})();
