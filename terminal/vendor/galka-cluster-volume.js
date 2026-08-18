(() => {
  'use strict';

  const STORAGE_KEY = 'galka-cluster-volume-v1';
  const VALID_COINS = new Set(['BTC', 'ETH', 'SOL']);
  const MAX_VISIBLE_BUBBLES = 160;
  const HOLD_SETTINGS_MS = 500;
  const POLL_MS = 1200;

  let chart = null;
  let cells = [];
  let streamMeta = null;
  let pollTimer = null;
  let busy = false;
  let suppressToggleClick = false;
  let holdTimer = null;

  const defaults = {
    enabled: false,
    mode: 'total',
    aggregation: 'auto',
    autoThreshold: true,
    slider: 50,
    bubbleScale: 1.0,
  };

  function loadSettings() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      return { ...defaults, ...(parsed && typeof parsed === 'object' ? parsed : {}) };
    } catch (_) {
      return { ...defaults };
    }
  }

  const settings = loadSettings();

  function saveSettings() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch (_) {
      // Display preference only.
    }
  }

  function selectedCoin() {
    const coin = String(document.getElementById('symbolSelect')?.value || 'BTC').toUpperCase();
    return VALID_COINS.has(coin) ? coin : 'BTC';
  }

  function selectedInterval() {
    return String(document.getElementById('intervalSelect')?.value || '5m');
  }

  function sessionHeaders() {
    const token = sessionStorage.getItem('galkaLiveSession') || '';
    return token ? { 'X-Galka-Session': token } : {};
  }

  function finite(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function percentile(values, q) {
    const rows = values.filter((value) => Number.isFinite(value) && value >= 0).sort((a, b) => a - b);
    if (!rows.length) return 0;
    if (rows.length === 1) return rows[0];
    const pos = Math.max(0, Math.min(1, q)) * (rows.length - 1);
    const low = Math.floor(pos);
    const high = Math.ceil(pos);
    if (low === high) return rows[low];
    const weight = pos - low;
    return rows[low] * (1 - weight) + rows[high] * weight;
  }

  function metric(row) {
    if (settings.mode === 'buy') return finite(row.buyNotional);
    if (settings.mode === 'sell') return finite(row.sellNotional);
    if (settings.mode === 'delta') return Math.abs(finite(row.deltaNotional));
    return finite(row.totalNotional);
  }

  function metricDistribution() {
    return cells.map(metric).filter((value) => value > 0);
  }

  function thresholdBounds() {
    const values = metricDistribution();
    if (!values.length) return { min: 100, max: 1000, auto: 1000 };
    const max = Math.max(...values);
    const q50 = percentile(values, 0.50);
    const q90 = percentile(values, 0.90);
    const min = Math.max(100, Math.min(q50 * 0.2 || max * 0.01, max));
    return { min, max: Math.max(max, min * 1.01), auto: Math.max(q90, min) };
  }

  function sliderToThreshold(sliderValue = settings.slider) {
    const bounds = thresholdBounds();
    const fraction = Math.max(0, Math.min(100, finite(sliderValue, 50))) / 100;
    if (bounds.max <= bounds.min) return bounds.min;
    return bounds.min * Math.pow(bounds.max / bounds.min, fraction);
  }

  function thresholdToSlider(value) {
    const bounds = thresholdBounds();
    const target = Math.max(bounds.min, Math.min(bounds.max, finite(value, bounds.auto)));
    if (bounds.max <= bounds.min) return 0;
    return 100 * Math.log(target / bounds.min) / Math.log(bounds.max / bounds.min);
  }

  function activeThreshold() {
    const bounds = thresholdBounds();
    return settings.autoThreshold ? bounds.auto : sliderToThreshold();
  }

  function compactMoney(value) {
    const number = Math.max(0, finite(value));
    if (number >= 1_000_000_000) return `$${(number / 1_000_000_000).toFixed(number >= 10_000_000_000 ? 0 : 1)}b`;
    if (number >= 1_000_000) return `$${(number / 1_000_000).toFixed(number >= 10_000_000 ? 0 : 1)}m`;
    if (number >= 1_000) return `$${(number / 1_000).toFixed(number >= 10_000 ? 0 : 1)}k`;
    return `$${number.toFixed(0)}`;
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

  function bubbleColor(row) {
    if (settings.mode === 'buy') return 'rgba(22,199,132,0.48)';
    if (settings.mode === 'sell') return 'rgba(239,83,80,0.48)';
    const total = Math.max(1, finite(row.totalNotional));
    const ratio = finite(row.deltaNotional) / total;
    if (ratio > 0.08) return 'rgba(22,199,132,0.46)';
    if (ratio < -0.08) return 'rgba(239,83,80,0.46)';
    return 'rgba(255,152,0,0.38)';
  }

  function bubbleStroke(row) {
    if (settings.mode === 'buy') return 'rgba(80,235,174,0.82)';
    if (settings.mode === 'sell') return 'rgba(255,126,121,0.82)';
    const total = Math.max(1, finite(row.totalNotional));
    const ratio = finite(row.deltaNotional) / total;
    if (ratio > 0.08) return 'rgba(80,235,174,0.78)';
    if (ratio < -0.08) return 'rgba(255,126,121,0.78)';
    return 'rgba(255,190,92,0.74)';
  }

  function drawClusters(target) {
    if (!settings.enabled || !cells.length) return;
    const ctx = target.ctx;
    const geometry = target.geometry?.();
    const windowState = target.visibleWindow?.();
    const rows = windowState?.rows || [];
    const rangeRows = rows.length ? rows : (windowState?.all || []).slice(-1);
    const range = target.currentPriceRange?.(rangeRows);
    if (!ctx || !geometry || !rows.length || !range || !(finite(range.span) > 0)) return;

    const firstTime = Number(rows[0].time);
    const lastTime = Number(rows.at(-1).time);
    const count = Math.max(1, Number(windowState?.count || rows.length));
    const start = Number(windowState?.start ?? 0);
    const dataStart = Number(windowState?.dataStart ?? start);
    const slot = geometry.plotWidth / count;
    const threshold = activeThreshold();

    const visible = cells
      .filter((row) => Number(row.time) >= firstTime && Number(row.time) <= lastTime && metric(row) >= threshold)
      .sort((left, right) => metric(right) - metric(left))
      .slice(0, MAX_VISIBLE_BUBBLES)
      .sort((left, right) => metric(left) - metric(right));
    if (!visible.length) return;

    const maxMetric = Math.max(...visible.map(metric), threshold);
    const yForPrice = (value) => geometry.top + (range.max - value) / range.span * geometry.plotHeight;

    ctx.save();
    ctx.lineWidth = 1;
    for (const row of visible) {
      const rowIndex = nearestRowIndex(rows, Number(row.time));
      if (rowIndex < 0) continue;
      const logicalIndex = dataStart + rowIndex;
      const x = geometry.left + (logicalIndex - start + 0.5) * slot;
      const y = yForPrice(finite(row.price));
      if (x < geometry.left - 20 || x > geometry.right + 20 || y < geometry.top - 20 || y > geometry.bottom + 20) continue;

      const normalized = Math.sqrt(Math.max(1, metric(row) / Math.max(threshold, 1)));
      const maxNormalized = Math.sqrt(Math.max(1, maxMetric / Math.max(threshold, 1)));
      const radius = Math.min(19, (4 + 12 * normalized / Math.max(1, maxNormalized)) * finite(settings.bubbleScale, 1));
      ctx.fillStyle = bubbleColor(row);
      ctx.strokeStyle = bubbleStroke(row);
      ctx.beginPath();
      ctx.arc(x, y, Math.max(3, radius), 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
    ctx.restore();
  }

  function patchChart() {
    const charts = window.LightweightCharts;
    if (!charts?.createChart) return;
    const originalCreateChart = charts.createChart;
    window.LightweightCharts = Object.freeze({
      ...charts,
      createChart(container, options) {
        const created = originalCreateChart(container, options);
        const originalDraw = created.draw.bind(created);
        created.draw = function drawWithClusters() {
          originalDraw();
          drawClusters(created);
        };
        chart = created;
        return created;
      },
    });
  }

  function ui() {
    return {
      toggle: document.getElementById('clusterToggle'),
      backdrop: document.getElementById('clusterSettingsBackdrop'),
      close: document.getElementById('clusterSettingsClose'),
      mode: document.getElementById('clusterMode'),
      aggregation: document.getElementById('clusterAggregation'),
      auto: document.getElementById('clusterAutoThreshold'),
      threshold: document.getElementById('clusterThreshold'),
      thresholdLabel: document.getElementById('clusterThresholdLabel'),
      bubble: document.getElementById('clusterBubbleScale'),
      stats: document.getElementById('clusterStats'),
    };
  }

  function renderUi() {
    const els = ui();
    if (!els.toggle) return;
    els.toggle.className = `cluster-toggle${settings.enabled ? ' on' : ''}${busy ? ' waiting' : ''}`;
    els.toggle.textContent = 'CL';
    els.toggle.setAttribute('aria-pressed', settings.enabled ? 'true' : 'false');
    els.mode.value = settings.mode;
    els.aggregation.value = settings.aggregation;
    els.auto.classList.toggle('active', settings.autoThreshold);
    els.auto.textContent = settings.autoThreshold ? 'AUTO ✓' : 'AUTO';
    const threshold = activeThreshold();
    if (settings.autoThreshold) settings.slider = thresholdToSlider(threshold);
    els.threshold.value = String(Math.max(0, Math.min(100, finite(settings.slider, 50))));
    els.threshold.disabled = false;
    els.thresholdLabel.textContent = `${compactMoney(threshold)}+`;
    els.bubble.value = String(finite(settings.bubbleScale, 1));
    const shown = cells.filter((row) => metric(row) >= threshold).length;
    const connection = streamMeta?.connected ? 'LIVE' : (streamMeta?.lastError ? 'нет WS' : 'подключение…');
    els.stats.textContent = `${connection} · ${shown}/${cells.length} кластеров выше фильтра`;
    chart?.draw?.();
  }

  function openSettings() {
    const els = ui();
    els.backdrop?.classList.remove('hidden');
    renderUi();
  }

  function closeSettings() {
    ui().backdrop?.classList.add('hidden');
  }

  function showToast(message, type = 'ok') {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast ${type}`;
    window.setTimeout(() => toast.classList.add('hidden'), 3500);
  }

  async function fetchClusters() {
    if (!settings.enabled || busy) return;
    busy = true;
    renderUi();
    const coin = selectedCoin();
    const interval = selectedInterval();
    try {
      const response = await fetch(
        `/api/live/clusters?coin=${encodeURIComponent(coin)}` +
        `&interval=${encodeURIComponent(interval)}` +
        `&aggregation=${encodeURIComponent(settings.aggregation)}`,
        {
          headers: sessionHeaders(),
          credentials: 'same-origin',
          cache: 'no-store',
        },
      );
      const payload = await response.json();
      if (!response.ok || payload?.ok === false) throw new Error(payload?.error || `HTTP ${response.status}`);
      cells = Array.isArray(payload?.data?.cells) ? payload.data.cells : [];
      streamMeta = payload?.data?.stream || null;
    } catch (error) {
      cells = [];
      streamMeta = { connected: false, lastError: String(error?.message || error) };
    } finally {
      busy = false;
      renderUi();
    }
  }

  function restartPolling() {
    clearInterval(pollTimer);
    pollTimer = null;
    if (!settings.enabled) {
      cells = [];
      chart?.draw?.();
      renderUi();
      return;
    }
    fetchClusters();
    pollTimer = setInterval(fetchClusters, POLL_MS);
  }

  function bindUi() {
    const els = ui();
    if (!els.toggle) return;

    els.toggle.addEventListener('pointerdown', () => {
      suppressToggleClick = false;
      clearTimeout(holdTimer);
      holdTimer = setTimeout(() => {
        suppressToggleClick = true;
        openSettings();
      }, HOLD_SETTINGS_MS);
    });
    const clearHold = () => clearTimeout(holdTimer);
    els.toggle.addEventListener('pointerup', clearHold);
    els.toggle.addEventListener('pointercancel', clearHold);
    els.toggle.addEventListener('pointerleave', clearHold);
    els.toggle.addEventListener('click', () => {
      if (suppressToggleClick) {
        suppressToggleClick = false;
        return;
      }
      settings.enabled = !settings.enabled;
      saveSettings();
      restartPolling();
      if (settings.enabled) showToast('Кластеры включены. Удерживай CL для настройки фильтра.');
    });

    els.close?.addEventListener('click', closeSettings);
    els.backdrop?.addEventListener('click', (event) => {
      if (event.target === els.backdrop) closeSettings();
    });

    els.mode?.addEventListener('change', () => {
      settings.mode = els.mode.value;
      if (settings.autoThreshold) settings.slider = thresholdToSlider(thresholdBounds().auto);
      saveSettings();
      renderUi();
    });
    els.aggregation?.addEventListener('change', () => {
      settings.aggregation = els.aggregation.value;
      saveSettings();
      fetchClusters();
    });
    els.auto?.addEventListener('click', () => {
      settings.autoThreshold = !settings.autoThreshold;
      if (!settings.autoThreshold) settings.slider = thresholdToSlider(thresholdBounds().auto);
      saveSettings();
      renderUi();
    });
    els.threshold?.addEventListener('input', () => {
      settings.autoThreshold = false;
      settings.slider = finite(els.threshold.value, 50);
      saveSettings();
      renderUi();
    });
    els.bubble?.addEventListener('input', () => {
      settings.bubbleScale = finite(els.bubble.value, 1);
      saveSettings();
      renderUi();
    });

    document.getElementById('symbolSelect')?.addEventListener('change', () => {
      cells = [];
      if (settings.enabled) fetchClusters();
      else renderUi();
    });
    document.getElementById('intervalSelect')?.addEventListener('change', () => {
      cells = [];
      if (settings.enabled) fetchClusters();
      else renderUi();
    });

    renderUi();
    restartPolling();
  }

  patchChart();
  document.addEventListener('DOMContentLoaded', bindUi);
})();
