const $ = (id) => document.getElementById(id);

const els = {
  chart: $('memChart'),
  frame: $('memChartFrame'),
  canvas: $('memDrawingCanvas'),
  coin: $('coinSelect'),
  interval: $('chartInterval'),
  chartSymbol: $('chartSymbol'),
  ticker: $('chartTicker'),
  ohlc: $('chartOhlc'),
  stepBadge: $('chartStepBadge'),
  instruction: $('chartInstruction'),
  selections: $('chartSelections'),
  newGalka: $('newGalka'),
  addUpper: $('chartAddUpper'),
  done: $('chartDone'),
  reset: $('chartReset'),
  cursorTool: $('chartCursorTool'),
  crosshairTool: $('chartCrosshairTool'),
  galkaTool: $('chartGalkaTool'),
  fit: $('chartFit'),
  latest: $('chartLatest'),
  galka: $('galkaPrice'),
  upperLevels: $('upperLevels'),
  preview: $('previewButton'),
  toast: $('toast'),
};

const COLORS = {
  green: '#089981',
  red: '#f23645',
  galka: '#ffb454',
  blue: '#2962ff',
  yellow: '#f6c85f',
  gray: '#8b93a4',
  cyan: '#26c6da',
};

const PICK_STAGES = new Set(['anchor', 'left', 'right', 'upper']);

const runtime = {
  chart: null,
  series: null,
  markerPrimitive: null,
  lines: [],
  stage: 'idle',
  tool: 'cursor',
  anchor: null,
  left: null,
  right: null,
  upper: [],
  lower: [],
  candles: [],
  candleBusy: false,
  loadedCoin: '',
  loadedInterval: '',
  lastCandleTime: null,
  refreshTimer: null,
  toastTimer: null,
  resizeObserver: null,
  dpr: window.devicePixelRatio || 1,
  latestClose: null,
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
      background: { type: 'solid', color: '#0b0e13' },
      textColor: '#a5adbd',
      fontFamily: 'Inter,system-ui',
    },
    grid: {
      vertLines: { visible: false },
      horzLines: { visible: false },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { labelBackgroundColor: COLORS.blue },
      horzLine: { labelBackgroundColor: COLORS.blue },
    },
    rightPriceScale: {
      visible: true,
      borderColor: '#2a303d',
      autoScale: true,
      scaleMargins: { top: 0.08, bottom: 0.12 },
    },
    leftPriceScale: { visible: false, borderColor: '#2a303d' },
    timeScale: {
      borderColor: '#2a303d',
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 8,
      barSpacing: 7,
      fixLeftEdge: false,
      fixRightEdge: false,
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

  runtime.chart.subscribeCrosshairMove(handleCrosshair);
  runtime.chart.timeScale().subscribeVisibleLogicalRangeChange(drawDraft);
  runtime.chart.timeScale().subscribeVisibleTimeRangeChange(drawDraft);

  if (window.ResizeObserver && els.frame) {
    runtime.resizeObserver = new ResizeObserver(() => resizeCanvas());
    runtime.resizeObserver.observe(els.frame);
  }
  resizeCanvas();
}

function autoCenter() {
  if (!runtime.chart) return;
  runtime.chart.priceScale('right').applyOptions({ autoScale: true });
  runtime.chart.timeScale().fitContent();
  requestAnimationFrame(() => {
    runtime.chart?.priceScale('right').applyOptions({ autoScale: true });
    drawDraft();
  });
}

function scrollLatest() {
  if (!runtime.chart) return;
  try {
    runtime.chart.timeScale().scrollToRealTime();
  } catch (_) {
    autoCenter();
  }
  runtime.chart.priceScale('right').applyOptions({ autoScale: true });
  requestAnimationFrame(drawDraft);
}

function resizeCanvas() {
  if (!els.canvas || !els.frame) return;
  const rect = els.frame.getBoundingClientRect();
  if (!(rect.width > 0) || !(rect.height > 0)) return;
  runtime.dpr = window.devicePixelRatio || 1;
  els.canvas.width = Math.max(1, Math.round(rect.width * runtime.dpr));
  els.canvas.height = Math.max(1, Math.round(rect.height * runtime.dpr));
  els.canvas.style.width = `${rect.width}px`;
  els.canvas.style.height = `${rect.height}px`;
  drawDraft();
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
    addPriceLine(runtime.anchor.price, COLORS.galka, 'GALKA', 2, LightweightCharts.LineStyle.Solid);
  }
  runtime.upper.forEach((value, index) => {
    addPriceLine(value, COLORS.yellow, `UP ${index + 1}`, 2);
  });
  runtime.lower.forEach((value, index) => {
    addPriceLine(value, COLORS.red, `LOW -${(index + 1) * 2}%`, 1);
  });
  drawDraft();
}

function markerRows() {
  const rows = [];
  if (runtime.anchor?.time != null) {
    rows.push({ time: runtime.anchor.time, position: 'belowBar', shape: 'circle', color: COLORS.galka, text: 'G' });
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
    // Visual-only markers must never block order planning.
  }
}

function canvasPoint(point) {
  if (!point || !runtime.chart || !runtime.series) return null;
  const x = runtime.chart.timeScale().timeToCoordinate(point.time);
  const y = runtime.series.priceToCoordinate(point.price);
  if (!Number.isFinite(Number(x)) || !Number.isFinite(Number(y))) return null;
  return { x: Number(x), y: Number(y) };
}

function drawHandle(ctx, point, color, label) {
  if (!point) return;
  ctx.beginPath();
  ctx.arc(point.x, point.y, 5.5, 0, Math.PI * 2);
  ctx.fillStyle = '#0b0e13';
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = color;
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.font = '700 10px Inter,system-ui';
  ctx.fillText(label, point.x + 8, point.y - 8);
}

function drawDraft() {
  if (!els.canvas) return;
  const ctx = els.canvas.getContext('2d');
  if (!ctx) return;
  const width = els.canvas.width / runtime.dpr;
  const height = els.canvas.height / runtime.dpr;
  ctx.setTransform(runtime.dpr, 0, 0, runtime.dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const anchor = canvasPoint(runtime.anchor);
  const left = canvasPoint(runtime.left);
  const right = canvasPoint(runtime.right);

  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.lineWidth = 2.25;
  ctx.strokeStyle = COLORS.galka;
  ctx.shadowColor = 'rgba(255,180,84,.22)';
  ctx.shadowBlur = 8;

  if (left && anchor) {
    ctx.beginPath();
    ctx.moveTo(left.x, left.y);
    ctx.lineTo(anchor.x, anchor.y);
    ctx.stroke();
  }
  if (anchor && right) {
    ctx.beginPath();
    ctx.moveTo(anchor.x, anchor.y);
    ctx.lineTo(right.x, right.y);
    ctx.stroke();
  }

  ctx.shadowBlur = 0;
  drawHandle(ctx, anchor, COLORS.galka, 'G');
  drawHandle(ctx, left, COLORS.cyan, 'Л');
  drawHandle(ctx, right, COLORS.yellow, 'П');
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

function instructionForStage() {
  const instructions = {
    idle: ['ГОТОВ', 'Нажми «Новая GALKA». Масштаб и перемещение графика работают как в Galka Pro.'],
    anchor: ['1/4', 'Поставь якорь — уровень GALKA.'],
    left: ['2/4', 'Поставь левую часть GALKA слева от якоря.'],
    right: ['3/4', 'Поставь правую часть GALKA справа от якоря.'],
    upper: ['4/4', 'Поставь верхнюю лимитку между GALKA и +5%.'],
    upper_wait: ['ВЕРХ', 'Лимитка добавлена. Можно двигать/зумить график. Нажми «+ ещё лимитка» или «Готово».'],
    done: ['ГОТОВО', 'Нижние −2 / −4 / −6 / −8% добавлены. Проверь safety-preview ниже.'],
  };
  return instructions[runtime.stage] || instructions.idle;
}

function renderStage() {
  const [badge, baseText] = instructionForStage();
  const picking = PICK_STAGES.has(runtime.stage);
  const paused = picking && runtime.tool !== 'galka';
  els.stepBadge.textContent = badge;
  els.instruction.textContent = paused ? `${baseText} Нажми G, чтобы продолжить выбор.` : baseText;
  els.canvas?.classList.toggle('drawing', picking && runtime.tool === 'galka');
  els.addUpper.disabled = runtime.stage !== 'upper_wait';
  els.done.disabled = runtime.stage !== 'upper_wait' || runtime.upper.length === 0;
  renderSelections();
}

function setTool(tool, notify = false) {
  runtime.tool = tool;
  els.cursorTool?.classList.toggle('active', tool === 'cursor');
  els.crosshairTool?.classList.toggle('active', tool === 'crosshair');
  els.galkaTool?.classList.toggle('active', tool === 'galka');
  els.frame?.classList.toggle('crosshair-active', tool === 'crosshair');

  if (runtime.chart) {
    runtime.chart.applyOptions({
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: { labelBackgroundColor: COLORS.blue },
        horzLine: { labelBackgroundColor: COLORS.blue },
      },
    });
  }
  renderStage();
  if (notify && tool === 'crosshair') {
    showToast('Перекрестие: зажми палец на графике и веди по свечам');
  }
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
  drawDraft();
  if (clearInputs) {
    els.galka.value = '';
    replaceUpperInputs([]);
  }
  setTool('cursor');
  renderStage();
}

function startDraft() {
  resetDraft(true);
  runtime.stage = 'anchor';
  setTool('galka');
  renderStage();
  showToast('GALKA: сначала якорь, затем левая и правая часть');
}

function nearestCandleTime(x) {
  if (!runtime.chart || !runtime.candles.length) return null;
  const logical = runtime.chart.timeScale().coordinateToLogical(x);
  if (Number.isFinite(Number(logical))) {
    const index = Math.round(Number(logical));
    if (index >= 0 && index < runtime.candles.length) {
      return runtime.candles[index].time;
    }
  }
  const converted = runtime.chart.timeScale().coordinateToTime(x);
  if (typeof converted === 'number' && Number.isFinite(converted)) return converted;
  return null;
}

function pointFromPointer(event) {
  if (!runtime.series || !els.frame) return null;
  const rect = els.frame.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  if (x < 0 || y < 0 || x > rect.width || y > rect.height) return null;
  const selectedPrice = runtime.series.coordinateToPrice(y);
  const selectedTime = nearestCandleTime(x);
  if (!(Number(selectedPrice) > 0)) return null;
  return { price: Number(selectedPrice), time: selectedTime, x, y };
}

function handlePickPointer(event) {
  if (runtime.tool !== 'galka' || !PICK_STAGES.has(runtime.stage)) return;
  if (event.pointerType === 'mouse' && event.button !== 0) return;
  event.preventDefault();
  const point = pointFromPointer(event);
  if (!point) return showToast('Коснись области свечей графика', 'error');

  if (runtime.stage === 'anchor') {
    if (point.time == null) return showToast('Якорь поставь над свечой', 'error');
    runtime.anchor = { price: point.price, time: point.time };
    els.galka.value = inputPrice(point.price);
    runtime.stage = 'left';
  } else if (runtime.stage === 'left') {
    if (point.time == null) return showToast('Левую часть поставь над свечой', 'error');
    if (!(point.time < runtime.anchor.time)) {
      return showToast('Левая часть должна быть левее якоря', 'error');
    }
    runtime.left = { price: point.price, time: point.time };
    runtime.stage = 'right';
  } else if (runtime.stage === 'right') {
    if (point.time == null) return showToast('Правую часть поставь над свечой', 'error');
    if (!(point.time > runtime.anchor.time)) {
      return showToast('Правая часть должна быть правее якоря', 'error');
    }
    runtime.right = { price: point.price, time: point.time };
    runtime.stage = 'upper';
  } else if (runtime.stage === 'upper') {
    const galka = Number(runtime.anchor?.price || els.galka.value);
    if (!(galka > 0)) return showToast('Сначала поставь якорь GALKA', 'error');
    if (point.price < galka * (1 - 1e-8) || point.price > galka * 1.05 * (1 + 1e-8)) {
      return showToast('Верхняя лимитка должна быть от GALKA до +5%', 'error');
    }
    const duplicate = runtime.upper.some(
      (value) => Math.abs(value - point.price) <= Math.max(1e-12, galka * 1e-7),
    );
    if (duplicate) return showToast('Такая верхняя лимитка уже есть', 'error');
    runtime.upper.push(point.price);
    syncUpperInputs();
    runtime.stage = 'upper_wait';
  }

  renderPriceLines();
  renderMarkers();
  drawDraft();

  if (runtime.stage === 'upper_wait') {
    setTool('cursor');
  } else {
    renderStage();
  }
}

function addAnotherUpper() {
  if (runtime.stage !== 'upper_wait') return;
  runtime.stage = 'upper';
  setTool('galka');
}

function finishDraft() {
  const galka = Number(runtime.anchor?.price || els.galka.value);
  if (!(galka > 0) || !runtime.upper.length) return;
  runtime.lower = [0.98, 0.96, 0.94, 0.92].map((factor) => galka * factor);
  runtime.stage = 'done';
  setTool('cursor');
  renderPriceLines();
  renderStage();
  setTimeout(() => els.preview?.click(), 50);
}

function handleCrosshair(param) {
  if (!els.ohlc) return;
  if (!param?.point || param.time == null) {
    if (runtime.tool === 'crosshair') els.ohlc.textContent = 'O — H — L — C —';
    return;
  }
  let row = null;
  try {
    row = param.seriesData?.get(runtime.series) || null;
  } catch (_) {}
  if (row && 'open' in row) {
    els.ohlc.textContent =
      `O ${price(row.open)}  H ${price(row.high)}  L ${price(row.low)}  C ${price(row.close)}`;
  } else {
    const selected = runtime.series.coordinateToPrice(param.point.y);
    els.ohlc.textContent = `Цена ${price(selected)}`;
  }
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
      runtime.candles = candles;
      runtime.series.setData(candles);
      runtime.loadedCoin = coin;
      runtime.loadedInterval = interval;
      runtime.lastCandleTime = candles.at(-1).time;
      runtime.latestClose = candles.at(-1).close;
      autoCenter();
    } else {
      const byTime = new Map(runtime.candles.map((row) => [row.time, row]));
      for (const row of candles) {
        if (runtime.lastCandleTime != null && row.time < runtime.lastCandleTime) continue;
        runtime.series.update(row);
        byTime.set(row.time, row);
        runtime.lastCandleTime = Math.max(runtime.lastCandleTime ?? row.time, row.time);
        runtime.latestClose = row.close;
      }
      runtime.candles = [...byTime.values()].sort((a, b) => a.time - b.time).slice(-600);
    }
    els.chartSymbol.textContent = `${coin} · ${interval} · Hyperliquid`;
    if (els.ticker) els.ticker.textContent = price(runtime.latestClose);
    drawDraft();
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

els.canvas?.addEventListener('pointerdown', handlePickPointer);
els.newGalka?.addEventListener('click', startDraft);
els.addUpper?.addEventListener('click', addAnotherUpper);
els.done?.addEventListener('click', finishDraft);
els.reset?.addEventListener('click', () => resetDraft(true));
els.cursorTool?.addEventListener('click', () => setTool('cursor'));
els.crosshairTool?.addEventListener('click', () => setTool('crosshair', true));
els.galkaTool?.addEventListener('click', () => {
  if (runtime.stage === 'idle' || runtime.stage === 'done') {
    startDraft();
  } else if (runtime.stage === 'upper_wait') {
    addAnotherUpper();
  } else {
    setTool('galka');
  }
});
els.fit?.addEventListener('click', autoCenter);
els.latest?.addEventListener('click', scrollLatest);

els.coin?.addEventListener('change', () => {
  resetDraft(true);
  runtime.lastCandleTime = null;
  runtime.loadedCoin = '';
  runtime.candles = [];
  loadCandles(true);
});
els.interval?.addEventListener('change', () => {
  resetDraft(true);
  runtime.lastCandleTime = null;
  runtime.loadedInterval = '';
  runtime.candles = [];
  loadCandles(true);
});

window.addEventListener('resize', resizeCanvas);

initChart();
setTool('cursor');
renderStage();
waitForMarketAndLoad();
runtime.refreshTimer = setInterval(() => loadCandles(false), 5000);
