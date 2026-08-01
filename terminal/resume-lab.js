const $ = (id) => document.getElementById(id);

const els = {
  symbol: $('symbolSelect'),
  interval: $('intervalSelect'),
  ticker: $('ticker'),
  chart: $('chart'),
  watermark: $('watermark'),
  loading: $('loading'),
  toast: $('toast'),
  resumePanel: document.querySelector('.resume-panel'),
  resumeState: $('resumeState'),
  resumeDetails: $('resumeDetails'),
  lastCandle: $('lastCandle'),
  lastRefresh: $('lastRefresh'),
  resumeCount: $('resumeCount'),
  refreshNow: $('refreshNow'),
};

const COLORS = {
  green: '#16c784',
  red: '#ef5350',
};

const INTERVAL_MS = {
  '1m': 60_000,
  '3m': 180_000,
  '5m': 300_000,
  '15m': 900_000,
  '30m': 1_800_000,
  '1h': 3_600_000,
  '4h': 14_400_000,
  '1d': 86_400_000,
};

const runtime = {
  coin: 'BTC',
  interval: '5m',
  chart: null,
  series: null,
  candlesLoaded: false,
  candleBusy: false,
  pendingRefresh: null,
  lastCandleTime: null,
  lastRefreshAt: 0,
  hiddenAt: document.hidden ? Date.now() : 0,
  resumeCount: 0,
  lastResumeRequestAt: 0,
  started: false,
  toastTimer: null,
};

function toast(message, type = '') {
  els.toast.textContent = message;
  els.toast.className = `toast ${type}`;
  clearTimeout(runtime.toastTimer);
  runtime.toastTimer = setTimeout(() => els.toast.classList.add('hidden'), 3500);
}

function formatPrice(value) {
  if (!Number.isFinite(Number(value))) return '—';
  return Number(value).toFixed(runtime.coin === 'SOL' ? 4 : 2);
}

function formatClock(timestamp) {
  if (!timestamp) return '—';
  return new Date(timestamp).toLocaleTimeString('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatDuration(milliseconds) {
  const seconds = Math.max(0, Math.round(milliseconds / 1000));
  if (seconds < 60) return `${seconds} сек.`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} мин.`;
  const hours = Math.round(minutes / 60);
  return `${hours} ч.`;
}

function setResumeState(title, details, state = '') {
  els.resumeState.textContent = title;
  els.resumeDetails.textContent = details;
  els.resumePanel.classList.remove('ok', 'error');
  if (state) els.resumePanel.classList.add(state);
}

async function api(path) {
  const response = await fetch(path, {
    cache: 'no-store',
    credentials: 'same-origin',
  });
  let payload;
  try {
    payload = await response.json();
  } catch (_) {
    throw new Error('Resume Lab вернул некорректный ответ');
  }
  if (!response.ok || payload?.ok === false) {
    throw new Error(payload?.error || `HTTP ${response.status}`);
  }
  return payload;
}

function initChart() {
  runtime.chart = LightweightCharts.createChart(els.chart, {
    autoSize: true,
    layout: {
      background: { type: 'solid', color: '#0b0f15' },
      textColor: '#9aa4b2',
    },
    grid: {
      vertLines: { visible: false },
      horzLines: { visible: false },
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: {
      borderColor: '#293241',
      autoScale: true,
      scaleMargins: { top: 0.08, bottom: 0.12 },
    },
    timeScale: {
      borderColor: '#293241',
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 8,
      barSpacing: 7,
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: true,
    },
    handleScale: {
      axisPressedMouseMove: true,
      mouseWheel: true,
      pinch: true,
    },
  });

  runtime.series = runtime.chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: COLORS.green,
    downColor: COLORS.red,
    borderVisible: false,
    wickUpColor: COLORS.green,
    wickDownColor: COLORS.red,
    priceLineVisible: false,
    lastValueVisible: true,
  });
}

function autoCenter() {
  runtime.chart.priceScale('right').applyOptions({ autoScale: true });
  runtime.chart.timeScale().fitContent();
  requestAnimationFrame(() => runtime.chart.priceScale('right').applyOptions({ autoScale: true }));
}

function normalizeCandles(rows) {
  const unique = new Map();
  for (const source of rows || []) {
    const row = {
      time: Number(source.time),
      open: Number(source.open),
      high: Number(source.high),
      low: Number(source.low),
      close: Number(source.close),
    };
    if (Object.values(row).every(Number.isFinite)) unique.set(row.time, row);
  }
  return [...unique.values()].sort((left, right) => left.time - right.time);
}

function updateFreshness(candles, reason, fullReload) {
  const newest = candles.at(-1);
  if (!newest) return;
  runtime.lastCandleTime = newest.time;
  runtime.lastRefreshAt = Date.now();
  els.ticker.textContent = formatPrice(newest.close);
  els.lastCandle.textContent = formatClock(newest.time * 1000);
  els.lastRefresh.textContent = formatClock(runtime.lastRefreshAt);
  els.watermark.textContent = `${runtime.coin} · ${runtime.interval} · HYPERLIQUID`;
  const mode = fullReload ? 'полная догрузка' : 'последние свечи';
  setResumeState('Свечи обновлены', `${reason} · ${mode} · ${formatClock(runtime.lastRefreshAt)}`, 'ok');
}

async function loadCandles({ fullReload = false, reason = 'таймер' } = {}) {
  if (runtime.candleBusy) {
    runtime.pendingRefresh = {
      fullReload: fullReload || Boolean(runtime.pendingRefresh?.fullReload),
      reason,
    };
    return;
  }

  runtime.candleBusy = true;
  els.refreshNow.disabled = true;
  const shouldReload = fullReload || !runtime.candlesLoaded;
  const requestedCoin = runtime.coin;
  const requestedInterval = runtime.interval;
  if (shouldReload) els.loading.classList.remove('hidden');
  setResumeState('Обновление…', reason);

  try {
    const limit = shouldReload ? 600 : 5;
    const payload = await api(
      `/api/resume-lab/candles?coin=${encodeURIComponent(requestedCoin)}` +
      `&interval=${encodeURIComponent(requestedInterval)}&limit=${limit}`,
    );
    if (requestedCoin !== runtime.coin || requestedInterval !== runtime.interval) return;

    const candles = normalizeCandles(payload.data);
    if (!candles.length) throw new Error('Hyperliquid не вернул свечи');

    if (shouldReload) {
      runtime.series.setData(candles);
      runtime.candlesLoaded = true;
      autoCenter();
    } else {
      for (const row of candles) {
        if (runtime.lastCandleTime != null && row.time < runtime.lastCandleTime) continue;
        runtime.series.update(row);
      }
    }
    updateFreshness(candles, reason, shouldReload);
  } catch (error) {
    setResumeState('Ошибка обновления', error.message, 'error');
    toast(error.message, 'error');
  } finally {
    runtime.candleBusy = false;
    els.refreshNow.disabled = false;
    if (shouldReload) els.loading.classList.add('hidden');
    const pending = runtime.pendingRefresh;
    runtime.pendingRefresh = null;
    if (pending) queueMicrotask(() => loadCandles(pending));
  }
}

function requestResumeRefresh(source) {
  if (!runtime.started || document.hidden) return;
  const now = Date.now();
  if (now - runtime.lastResumeRequestAt < 700) return;
  runtime.lastResumeRequestAt = now;

  const hiddenFor = runtime.hiddenAt ? now - runtime.hiddenAt : 0;
  runtime.hiddenAt = 0;
  runtime.resumeCount += 1;
  els.resumeCount.textContent = String(runtime.resumeCount);

  const interval = INTERVAL_MS[runtime.interval] || 300_000;
  const fullReload = hiddenFor > Math.min(interval, 60_000);
  const detail = hiddenFor
    ? `${source}: вкладка была в фоне ${formatDuration(hiddenFor)}`
    : `${source}: вкладка снова активна`;
  loadCandles({ fullReload, reason: detail });
}

function startLifecycleRecovery() {
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      runtime.hiddenAt = Date.now();
      setResumeState('Вкладка в фоне', 'При возврате свечи обновятся автоматически');
      return;
    }
    requestResumeRefresh('visibilitychange');
  });

  window.addEventListener('pageshow', () => requestResumeRefresh('pageshow'));
  window.addEventListener('focus', () => requestResumeRefresh('focus'));
}

async function candleLoop() {
  if (!document.hidden) {
    await loadCandles({ reason: '15-секундный цикл' });
  }
  setTimeout(candleLoop, 15_000);
}

els.symbol.onchange = async () => {
  runtime.coin = els.symbol.value;
  runtime.candlesLoaded = false;
  runtime.lastCandleTime = null;
  await loadCandles({ fullReload: true, reason: 'смена монеты' });
};

els.interval.onchange = async () => {
  runtime.interval = els.interval.value;
  runtime.candlesLoaded = false;
  runtime.lastCandleTime = null;
  await loadCandles({ fullReload: true, reason: 'смена таймфрейма' });
};

els.refreshNow.onclick = () => loadCandles({ fullReload: true, reason: 'ручное обновление' });

initChart();
startLifecycleRecovery();
await loadCandles({ fullReload: true, reason: 'первый запуск' });
runtime.started = true;
candleLoop();
