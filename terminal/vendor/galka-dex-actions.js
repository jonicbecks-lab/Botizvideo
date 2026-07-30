(() => {
  'use strict';

  const charts = window.LightweightCharts;
  if (!charts || typeof charts.createChart !== 'function') return;

  const originalCreateChart = charts.createChart.bind(charts);
  const chartStates = new WeakMap();
  let toastTimer = null;
  let requestSequence = 0;

  function showToast(message, type = 'ok') {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast ${type}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.add('hidden'), 4500);
  }

  function formatPrice(value, coin) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '';
    return number.toFixed(coin === 'SOL' ? 4 : 2);
  }

  function clearDraft(state) {
    if (!state?.chart?.series) return;
    for (const line of state.lines) {
      try {
        state.chart.series.removePriceLine(line);
      } catch (_) {
        // The chart may be rebuilding after a symbol change.
      }
    }
    state.lines = [];
  }

  function addDraftLine(state, value, color, title, style, width = 1) {
    if (!(Number(value) > 0) || !state.chart.series) return;
    state.lines.push(state.chart.series.createPriceLine({
      price: Number(value),
      color,
      lineWidth: width,
      lineStyle: style,
      axisLabelVisible: true,
      title,
    }));
  }

  async function renderDraftAtPrice(state, selectedPrice) {
    const input = document.getElementById('galkaInput');
    const symbol = document.getElementById('symbolSelect');
    if (!input || !symbol) return;

    if (input.disabled) {
      showToast('Нельзя выбрать новую GALKA: текущая кампания активна или включён SAFE MODE', 'error');
      return;
    }

    const coin = symbol.value;
    const galkaPrice = Number(selectedPrice);
    if (!(galkaPrice > 0)) return;

    input.value = formatPrice(galkaPrice, coin);
    const token = sessionStorage.getItem('galkaLiveSession') || '';
    if (!token) {
      showToast('Открой терминал через защищённую ссылку из Termux', 'error');
      return;
    }

    const requestId = ++requestSequence;
    try {
      const response = await fetch('/api/live/preview', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Galka-Session': token,
        },
        body: JSON.stringify({ coin, galkaPrice }),
        cache: 'no-store',
        credentials: 'same-origin',
      });
      const payload = await response.json();
      if (!response.ok || payload?.ok === false) {
        throw new Error(payload?.error || `HTTP ${response.status}`);
      }
      if (requestId !== requestSequence || input.disabled || symbol.value !== coin) return;

      const preview = payload.data;
      clearDraft(state);
      input.value = formatPrice(preview.galkaPrice, coin);
      addDraftLine(state, preview.galkaPrice, '#ff9800', 'GALKA', charts.LineStyle.Solid, 2);
      for (const level of preview.levels || []) {
        addDraftLine(state, level.price, '#7c8797', `L${level.index}`, charts.LineStyle.Dashed, 1);
      }
      showToast('GALKA и лимитные уровни выбраны. Для продолжения нажми «Проверить».');
    } catch (error) {
      clearDraft(state);
      showToast(error.message || 'Не удалось рассчитать уровни GALKA', 'error');
    }
  }

  function installDesktopActions(chart) {
    const state = { chart, lines: [], lastPointerType: '' };
    chartStates.set(chart, state);

    chart.canvas.addEventListener('pointerdown', (event) => {
      state.lastPointerType = event.pointerType || '';
    }, true);

    chart.canvas.addEventListener('contextmenu', (event) => {
      if (state.lastPointerType && state.lastPointerType !== 'mouse') return;
      event.preventDefault();

      const rect = chart.canvas.getBoundingClientRect();
      const geometry = chart.geometry();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      if (x < geometry.left || x > geometry.right || y < geometry.top || y > geometry.bottom) return;

      const range = chart.currentPriceRange(chart.lastRows);
      if (!range || !(range.span > 0)) return;
      const price = range.max - ((y - geometry.top) / geometry.plotHeight) * range.span;
      chart.crosshair = { x, y };
      chart.draw();
      renderDraftAtPrice(state, price);
    });

    chart.canvas.addEventListener('galka:select-price', (event) => {
      const selectedPrice = Number(event.detail?.price);
      if (selectedPrice > 0) renderDraftAtPrice(state, selectedPrice);
    });

    const input = document.getElementById('galkaInput');
    const symbol = document.getElementById('symbolSelect');
    if (input) {
      input.addEventListener('input', () => clearDraft(state));
      new MutationObserver(() => {
        if (input.disabled) clearDraft(state);
      }).observe(input, { attributes: true, attributeFilter: ['disabled'] });
    }
    if (symbol) symbol.addEventListener('change', () => clearDraft(state));
  }

  window.LightweightCharts = Object.freeze({
    ...charts,
    createChart(container, options) {
      const chart = originalCreateChart(container, options);
      installDesktopActions(chart);
      return chart;
    },
  });
})();
