const $ = (id) => document.getElementById(id);

const els = {
  chart: $('memChart'),
  coin: $('coinSelect'),
  interval: $('chartInterval'),
  chartSymbol: $('chartSymbol'),
  stepBadge: $('chartStepBadge'),
  instruction: $('chartInstruction'),
  selections: $('chartSelections'),
  newGalka: $('newGalka'),
  addUpper: $('chartAddUpper'),
  done: $('chartDone'),
  reset: $('chartReset'),
  galka: $('galkaPrice'),
  upperLevels: $('upperLevels'),
  preview: $('previewButton'),
  toast: $('toast'),
};

const COLORS = {
  green: '#16c784',
  red: '#ef5350',
  blue: '#4d7cff',
  yellow: '#f6c85f',
  gray: '#7f8a9a',
  cyan: '#26c6da',
};

const runtime = {
  chart: null,
  series: null,
  markerPrimitive: null,
  lines: [],
  stage: 'idle',
  anchor: null,
  left: null,
  right: null,
  upper: [],
  lower: [],
  candleBusy: false,
  loadedCoin: '',
  loadedInterval: '',
  lastCandleTime: null,
  refreshTimer: null,
  toastTimer: null,
};

const hashParams = new URLSearchParams(location.hash.replace(/^#/, ''));
const hashToken = hashParams.get('token');
if (hashToken) sessionStorage.setItem('galkaLiveSession', hashToken);

function sessionToken() {
  return sessionStorage.getItem('galkaLiveSession') || '';
}

function showToast(message, type = '') {
  if (!els.toast) return;
  els.toast.textContent = message;
  els.toast.className = 'toast ' + type;
  clearTimeout(runtime.toastTimer);
  runtime.toastTimer = setTimeout(() => els.toast.classList.add('hidden'), 4200);
}

async function api(path) {
  const token = sessionToken();
  if (!token) throw new Error('Открой GALKA MEM через защищённую ссылку из Termux');
  const response = await fetch(path, {
    headers: { 'X-Galka-Session': token },
    cache: 'no-store',
    credentials: 'same-origin',
  });
  let payload;
  try {
    payload = await response.json();
  } catch (_) {
    throw new Error('Сервер вернул некорректный ответ');
  }
  if (!response.ok || payload?.ok === false) {
    throw new Error(payload?.error || `HTTP ${response.status}`);
  }
  return payload.data;
}

function price(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return '—';
  if (n >= 1000) return n.toFixed(2);
  if (n >= 1) return n.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
  return Number(n.toPrecision(7)).toString();
}

function inputPrice(value) {
  const n = Number(value);
  return Number(n.toPrecision(10)).toString();
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
    if (
      !Number.isFinite(row.time) ||
      !Number.isFinite(row.open) ||
      !Number.isFinite(row.high) ||
      !Number.isFinite(row.low) ||
      !Number.isFinite(row.close)
    ) continue;
    unique.set(row.time, row);
  }
  return [...unique.values()].sort((a, b) => a.time - b.time);
}

function initChart() {
  if (!els.chart || !window.LightweightCharts) return;
  runtime.chart = LightweightCharts.createChart(els.chart, {
    autoSize: true,
    layout: {
      background: { type: 'solid', color: '#0b0f15' },
      textColor: '#98a4b5',
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
      rightOffset: 5,
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
  runtime.chart.subscribeClick(handleChartClick);
}

function clearPriceLines() {
  if (!runtime.series) return;
  for (const line of runtime.lines) {
    try { runtime.series.removePriceLine(line); } catch (_) {}
  }
  runtime.lines = [];
}

function addPriceLine(value, color, title, width = 1, style = LightweightCharts.LineStyle.Dashed) {
  if (!runtime.series || !(Number(value) > 0)) return;
  runtime.lines.push(runtime.series.createPriceLine({
    price: Number(value),
    color,
    lineWidth: width,
    lineStyle: style,
    axisLabelVisible: true,
    title,
  }));
}

function renderPriceLines() {
  clearPriceLines();
  if (runtime.anchor?.price) {
    addPriceLine(runtime.anchor.price, COLORS.blue, 'GALKA', 2, LightweightCharts.LineStyle.Solid);
  }
  runtime.upper.forEach((value, index) => {
    addPriceLine(value, COLORS.yellow, `UP ${index + 1}`, 2);
  });
  runtime.lower.forEach((value, index) => {
    addPriceLine(value, COLORS.red, `LOW -${(index + 1) * 2}%`, 1);
  });
}

function markerRows() {
  const rows = [];
  if (runtime.anchor?.time != null) {
    rows.push({ time: runtime.anchor.time, position: 'belowBar', shape: 'circle', color: COLORS.blue, text: 'G' });
  }
  if (runtime.left?.time != null) {
    rows.push({ time: runtime.left.time, position: 'aboveBar', shape: 'circle', color: COLORS.cyan, text: 'Л' });
  }
  if (runtime.right?.time != null) {
    rows.push({ time: runtime.right.time, position: 'aboveBar', shape: 'circle', color: COLORS.yellow, text: 'П' });
  }
  rows.sort((a, b) => Number(a.time) - Number(b.time));
  return rows;
}

function renderMarkers() {
  if (!runtime.series) return;
  const rows = markerRows();
  try {
    if (typeof LightweightCharts.createSeriesMarkers === 'function') {
      if (!runtime.markerPrimitive) {
        runtime.markerPrimitive = LightweightCharts.createSeriesMarkers(runtime.series, rows);
      } else if (typeof runtime.markerPrimitive.setMarkers === 'function') {
        runtime.markerPrimitive.setMarkers(rows);
      }
      return;
    }
    if (typeof runtime.series.setMarkers === 'function') runtime.series.setMarkers(rows);
  } catch (_) {
    // Markers are visual-only. Price-line workflow must keep working if this chart build lacks marker support.
  }
}

function renderSelections() {
  if (!els.selections) return;
  const upperText = runtime.upper.length ? runtime.upper.map(price).join(' · ') : '—';
  els.selections.innerHTML = [
    `<span>Якорь <b>${price(runtime.anchor?.price)}</b></span>`,
    `<span>Левая <b>${price(runtime.left?.price)}</b></span>`,
    `<span>Правая <b>${price(runtime.right?.price)}</b></span>`,
    `<span>Верх <b>${upperText}</b></span>`,
  ].join('');
}

function renderStage() {
  const instructions = {
    idle: ['ГОТОВ', 'Нажми «Новая GALKA», затем отмечай точки пальцем прямо на графике.'],
    anchor: ['1/4', 'Поставь якорь — это уровень GALKA.'],
    left: ['2/4', 'Поставь левую часть GALKA.'],
    right: ['3/4', 'Поставь правую часть GALKA.'],
    upper: ['4/4', 'Поставь верхнюю лимитку между GALKA и +5%.'],
    upper_wait: ['ВЕРХ', 'Лимитка добавлена. Нажми «+ ещё лимитка» или «Готово».'],
    done: ['ГОТОВО', 'Нижние −2 / −4 / −6 / −8% добавлены. Проверь safety-preview ниже.'],
  };
  const [badge, text] = instructions[runtime.stage] || instructions.idle;
  els.stepBadge.textContent = badge;
  els.instruction.textContent = text;
  els.chart?.classList.toggle('picking', ['anchor', 'left', 'right', 'upper'].includes(runtime.stage));
  els.addUpper.disabled = runtime.stage !== 'upper_wait';
  els.done.disabled = runtime.stage !== 'upper_wait' || runtime.upper.length === 0;
  renderSelections();
}

function makeUpperRow(value = '') {
  const row = document.createElement('div');
  row.className = 'upper-row';
  const input = document.createElement('input');
  input.type = 'number';
  input.inputMode = 'decimal';
  input.step = 'any';
  input.placeholder = 'Цена верхней лимитки';
  input.value = value;
  input.className = 'upper-input';
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.textContent = '×';
  remove.addEventListener('click', () => {
    if (els.upperLevels.children.length > 1) row.remove();
  });
  row.append(input, remove);
  return row;
}

function replaceUpperInputs(values) {
  els.upperLevels.innerHTML = '';
  const rows = values.length ? values : ['', '', ''];
  rows.forEach((value) => els.upperLevels.append(makeUpperRow(value)));
}

function syncUpperInputs() {
  const ordered = [...runtime.upper].sort((a, b) => b - a);
  replaceUpperInputs(ordered.map(inputPrice));
}

function resetDraft(clearInputs = true) {
  runtime.stage = 'idle';
  runtime.anchor = null;
  runtime.left = null;
  runtime.right = null;
  runtime.upper = [];
  runtime.lower = [];
  clearPriceLines();
  renderMarkers();
  if (clearInputs) {
    els.galka.value = '';
    replaceUpperInputs([]);
  }
  renderStage();
}

function startDraft() {
  resetDraft(true);
  runtime.stage = 'anchor';
  renderStage();
}

function pointFromClick(param) {
  if (!runtime.series || !param?.point) return null;
  const selectedPrice = runtime.series.coordinateToPrice(param.point.y);
  const selectedTime = param.time;
  if (!(Number(selectedPrice) > 0) || selectedTime == null) return null;
  return { price: Number(selectedPrice), time: selectedTime };
}

function handleChartClick(param) {
  if (!['anchor', 'left', 'right', 'upper'].includes(runtime.stage)) return;
  const point = pointFromClick(param);
  if (!point) return showToast('Нажми прямо на свечу графика', 'error');

  if (runtime.stage === 'anchor') {
    runtime.anchor = point;
    els.galka.value = inputPrice(point.price);
    runtime.stage = 'left';
  } else if (runtime.stage === 'left') {
    runtime.left = point;
    runtime.stage = 'right';
  } else if (runtime.stage === 'right') {
    runtime.right = point;
    runtime.stage = 'upper';
  } else if (runtime.stage === 'upper') {
    const galka = Number(runtime.anchor?.price || els.galka.value);
    if (!(galka > 0)) return showToast('Сначала поставь якорь GALKA', 'error');
    if (point.price < galka * (1 - 1e-8) || point.price > galka * 1.05 * (1 + 1e-8)) {
      return showToast('Верхняя лимитка должна быть от GALKA до +5%', 'error');
    }
    const duplicate = runtime.upper.some((value) => Math.abs(value - point.price) <= Math.max(1e-12, galka * 1e-7));
    if (duplicate) return showToast('Такая верхняя лимитка уже есть', 'error');
    runtime.upper.push(point.price);
    syncUpperInputs();
    runtime.stage = 'upper_wait';
  }

  renderPriceLines();
  renderMarkers();
  renderStage();
}

function addAnotherUpper() {
  if (runtime.stage !== 'upper_wait') return;
  runtime.stage = 'upper';
  renderStage();
}

function finishDraft() {
  const galka = Number(runtime.anchor?.price || els.galka.value);
  if (!(galka > 0) || !runtime.upper.length) return;
  runtime.lower = [0.98, 0.96, 0.94, 0.92].map((factor) => galka * factor);
  runtime.stage = 'done';
  renderPriceLines();
  renderStage();
  setTimeout(() => els.preview?.click(), 50);
}

async function loadCandles(force = false) {
  if (!runtime.series || runtime.candleBusy) return;
  const coin = els.coin?.value;
  const interval = els.interval?.value || '1m';
  if (!coin) return;
  const changed = coin !== runtime.loadedCoin || interval !== runtime.loadedInterval;
  const full = force || changed || runtime.lastCandleTime == null;
  runtime.candleBusy = true;
  try {
    const limit = full ? 600 : 60;
    const rows = await api(
      `/api/mem/candles?coin=${encodeURIComponent(coin)}` +
      `&interval=${encodeURIComponent(interval)}&limit=${limit}`,
    );
    if (coin !== els.coin.value || interval !== els.interval.value) return;
    const candles = normalizeCandles(rows);
    if (!candles.length) throw new Error(`Нет свечей ${coin} ${interval}`);
    if (full) {
      runtime.series.setData(candles);
      runtime.loadedCoin = coin;
      runtime.loadedInterval = interval;
      runtime.lastCandleTime = candles.at(-1).time;
      runtime.chart.timeScale().fitContent();
    } else {
      for (const row of candles) {
        if (runtime.lastCandleTime != null && row.time < runtime.lastCandleTime) continue;
        runtime.series.update(row);
        runtime.lastCandleTime = Math.max(runtime.lastCandleTime ?? row.time, row.time);
      }
    }
    els.chartSymbol.textContent = `${coin} · ${interval} · Hyperliquid`;
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    runtime.candleBusy = false;
  }
}

function coinReady() {
  return !!els.coin?.value && els.coin.options.length > 0;
}

function waitForMarketAndLoad() {
  if (coinReady()) {
    loadCandles(true);
    return;
  }
  setTimeout(waitForMarketAndLoad, 250);
}

els.newGalka?.addEventListener('click', startDraft);
els.addUpper?.addEventListener('click', addAnotherUpper);
els.done?.addEventListener('click', finishDraft);
els.reset?.addEventListener('click', () => resetDraft(true));
els.coin?.addEventListener('change', () => {
  resetDraft(true);
  runtime.lastCandleTime = null;
  runtime.loadedCoin = '';
  loadCandles(true);
});
els.interval?.addEventListener('change', () => {
  resetDraft(true);
  runtime.lastCandleTime = null;
  runtime.loadedInterval = '';
  loadCandles(true);
});

initChart();
renderStage();
waitForMarketAndLoad();
runtime.refreshTimer = setInterval(() => loadCandles(false), 5000);
