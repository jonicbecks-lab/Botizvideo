const $ = (id) => document.getElementById(id);

const els = {
  chart: $('mainChart'),
  frame: $('chartMainWrap'),
  canvas: $('drawingCanvas'),
  coin: $('coinSelect'),
  interval: $('chartInterval'),
  ticker: $('chartTicker'),
  watermark: $('watermark'),
  ohlc: $('ohlc'),
  health: $('chartHealth'),
  healthText: $('chartHealthText'),
  loading: $('loading'),
  stepPill: $('memStagePill'),
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
  leftbar: $('leftbar'),
  closeTools: $('closeTools'),
  toggleTools: $('toggleTools'),
  sidebar: $('sidebar'),
  backdrop: $('sheetBackdrop'),
  closeSidebar: $('closeSidebarSheet'),
  toggleSidebar: $('toggleSidebar'),
  sheetTitle: $('sheetTitle'),
  sheetSubtitle: $('sheetSubtitle'),
  actionBtn: $('chartActionBtn'),
  actionMenu: $('chartActionMenu'),
  quickSetGalka: $('quickSetGalka'),
  quickExactGalka: $('quickExactGalka'),
  quickLevels: $('quickLevels'),
  fit: $('fitBtn'),
  latest: $('latestBtn'),
  connection: $('connectionButton'),
  connectionDot: $('connectionDot'),
  fullscreen: $('fullscreenBtn'),
  magnet: $('magnetBtn'),
  lock: $('lockBtn'),
  hide: $('hideDrawingsBtn'),
  undo: $('undoBtn'),
  redo: $('redoBtn'),
  del: $('deleteBtn'),
  clear: $('clearBtn'),
};

const LWC = window.LightweightCharts;
const COLORS = {
  green: '#089981',
  red: '#f23645',
  blue: '#2962ff',
  galka: '#ffb454',
  cyan: '#26c6da',
  yellow: '#f6c85f',
  gray: '#8b93a4',
  purple: '#9c6ade',
};
const PICK_STAGES = new Set(['anchor', 'left', 'right', 'upper']);
const DRAW_TWO = new Set(['trend', 'ray', 'rect', 'measure', 'fib', 'longPosition']);

const runtime = {
  chart: null,
  series: null,
  priceLines: [],
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
  latestClose: null,
  refreshTimer: null,
  toastTimer: null,
  dpr: window.devicePixelRatio || 1,
  resizeObserver: null,
  showTradeLevels: true,
  drawings: [],
  drawingsHidden: false,
  drawingsLocked: false,
  magnet: false,
  pendingDrawing: null,
  undoStack: [],
  redoStack: [],
};

const hashParams = new URLSearchParams(location.hash.replace(/^#/, ''));
const hashToken = hashParams.get('token');
if (hashToken) sessionStorage.setItem('galkaLiveSession', hashToken);

function sessionToken() {
  return sessionStorage.getItem('galkaLiveSession') || '';
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

function showToast(message, type = '') {
  if (!els.toast) return;
  els.toast.textContent = message;
  els.toast.className = `toast ${type}`;
  clearTimeout(runtime.toastTimer);
  runtime.toastTimer = setTimeout(() => els.toast.classList.add('hidden'), 3800);
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
  try { payload = await response.json(); }
  catch (_) { throw new Error('Сервер вернул некорректный ответ'); }
  if (!response.ok || payload?.ok === false) throw new Error(payload?.error || `HTTP ${response.status}`);
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
  return Number(Number(value).toPrecision(10)).toString();
}

function normalizeCandles(rows) {
  const unique = new Map();
  for (const source of rows || []) {
    const row = {
      time: Number(source.time), open: Number(source.open), high: Number(source.high),
      low: Number(source.low), close: Number(source.close),
    };
    if (![row.time, row.open, row.high, row.low, row.close].every(Number.isFinite)) continue;
    unique.set(row.time, row);
  }
  return [...unique.values()].sort((a, b) => a.time - b.time);
}

function initChart() {
  if (!els.chart || !LWC) return;
  // These options are intentionally copied from Galka Pro createMainChart().
  runtime.chart = LWC.createChart(els.chart, {
    autoSize: true,
    layout: {
      background: { type: 'solid', color: '#0b0e13' },
      textColor: '#a5adbd',
      fontFamily: 'Inter,system-ui',
      attributionLogo: true,
    },
    grid: { vertLines: { visible: false }, horzLines: { visible: false } },
    crosshair: {
      mode: LWC.CrosshairMode.Normal,
      vertLine: { labelBackgroundColor: COLORS.blue },
      horzLine: { labelBackgroundColor: COLORS.blue },
    },
    rightPriceScale: {
      visible: true, borderColor: '#2a303d', autoScale: true,
      scaleMargins: { top: 0.08, bottom: 0.12 },
    },
    leftPriceScale: { visible: false, borderColor: '#2a303d' },
    timeScale: {
      borderColor: '#2a303d', timeVisible: true, secondsVisible: false,
      rightOffset: 8, barSpacing: 7, fixLeftEdge: false, fixRightEdge: false,
    },
    handleScroll: {
      mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true,
    },
    handleScale: {
      axisPressedMouseMove: true, mouseWheel: true, pinch: true,
    },
  });
  runtime.series = runtime.chart.addSeries(LWC.CandlestickSeries, {
    upColor: COLORS.green, downColor: COLORS.red, borderVisible: false,
    wickUpColor: COLORS.green, wickDownColor: COLORS.red,
    priceLineVisible: false, lastValueVisible: true,
  });
  runtime.chart.subscribeCrosshairMove(onCrosshair);
  runtime.chart.timeScale().subscribeVisibleLogicalRangeChange(drawAll);
  runtime.chart.timeScale().subscribeVisibleTimeRangeChange(drawAll);
  resizeCanvas();
  if (window.ResizeObserver && els.frame) {
    runtime.resizeObserver = new ResizeObserver(resizeCanvas);
    runtime.resizeObserver.observe(els.frame);
  }
}

function onCrosshair(param) {
  if (!els.frame || !els.ohlc) return;
  if (!param?.time || !param?.point) {
    els.frame.classList.remove('crosshair-active');
    return;
  }
  const row = param.seriesData?.get(runtime.series);
  if (!row) return;
  els.ohlc.textContent = `O ${price(row.open)}  H ${price(row.high)}  L ${price(row.low)}  C ${price(row.close)}`;
  els.frame.classList.add('crosshair-active');
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
  drawAll();
}

function fitChart() {
  runtime.chart?.priceScale('right').applyOptions({ autoScale: true });
  runtime.chart?.timeScale().fitContent();
  requestAnimationFrame(drawAll);
}

function latestChart() {
  try { runtime.chart?.timeScale().scrollToRealTime(); }
  catch (_) { fitChart(); }
  runtime.chart?.priceScale('right').applyOptions({ autoScale: true });
  requestAnimationFrame(drawAll);
}

function clearPriceLines() {
  if (!runtime.series) return;
  for (const line of runtime.priceLines) {
    try { runtime.series.removePriceLine(line); } catch (_) {}
  }
  runtime.priceLines = [];
}

function addPriceLine(value, color, title, width = 1, style = LWC.LineStyle.Dashed) {
  if (!runtime.series || !(Number(value) > 0)) return;
  runtime.priceLines.push(runtime.series.createPriceLine({
    price: Number(value), color, lineWidth: width, lineStyle: style,
    axisLabelVisible: true, title,
  }));
}

function renderTradeLevels() {
  clearPriceLines();
  if (!runtime.showTradeLevels) return;
  if (runtime.anchor?.price) addPriceLine(runtime.anchor.price, COLORS.galka, 'GALKA', 2, LWC.LineStyle.Solid);
  runtime.upper.forEach((value, index) => addPriceLine(value, COLORS.yellow, `UP ${index + 1}`, 2));
  runtime.lower.forEach((value, index) => addPriceLine(value, COLORS.red, `LOW -${(index + 1) * 2}%`, 1));
}

function timeToX(time) {
  const x = runtime.chart?.timeScale().timeToCoordinate(time);
  return Number.isFinite(Number(x)) ? Number(x) : null;
}
function priceToY(value) {
  const y = runtime.series?.priceToCoordinate(value);
  return Number.isFinite(Number(y)) ? Number(y) : null;
}
function chartPoint(point) {
  if (!point) return null;
  const x = timeToX(point.time), y = priceToY(point.price);
  return x == null || y == null ? null : { x, y };
}

function canvasContext() {
  const ctx = els.canvas?.getContext('2d');
  if (!ctx) return null;
  const width = els.canvas.width / runtime.dpr;
  const height = els.canvas.height / runtime.dpr;
  ctx.setTransform(runtime.dpr, 0, 0, runtime.dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  return { ctx, width, height };
}

function drawHandle(ctx, p, color, label) {
  if (!p) return;
  ctx.beginPath(); ctx.arc(p.x, p.y, 5.5, 0, Math.PI * 2);
  ctx.fillStyle = '#0b0e13'; ctx.fill();
  ctx.lineWidth = 2; ctx.strokeStyle = color; ctx.stroke();
  ctx.fillStyle = color; ctx.font = '800 10px Inter,system-ui';
  ctx.fillText(label, p.x + 8, p.y - 8);
}

function drawGalka(ctx) {
  const anchor = chartPoint(runtime.anchor);
  const left = chartPoint(runtime.left);
  const right = chartPoint(runtime.right);
  ctx.save();
  ctx.lineWidth = 2.5; ctx.strokeStyle = COLORS.galka;
  ctx.shadowColor = 'rgba(255,180,84,.28)'; ctx.shadowBlur = 8;
  if (left && anchor) { ctx.beginPath(); ctx.moveTo(left.x, left.y); ctx.lineTo(anchor.x, anchor.y); ctx.stroke(); }
  if (anchor && right) { ctx.beginPath(); ctx.moveTo(anchor.x, anchor.y); ctx.lineTo(right.x, right.y); ctx.stroke(); }
  ctx.restore();
  drawHandle(ctx, anchor, COLORS.galka, 'G');
  drawHandle(ctx, left, COLORS.cyan, 'Л');
  drawHandle(ctx, right, COLORS.yellow, 'П');
}

function drawOneDrawing(ctx, d, width, height) {
  if (!d || runtime.drawingsHidden) return;
  const p1 = chartPoint(d.p1), p2 = chartPoint(d.p2);
  ctx.save();
  ctx.strokeStyle = d.color || COLORS.blue;
  ctx.fillStyle = d.color || COLORS.blue;
  ctx.lineWidth = d.width || 2;
  if (d.dash) ctx.setLineDash(d.dash);
  if (d.type === 'horizontal' && p1) {
    ctx.beginPath(); ctx.moveTo(0, p1.y); ctx.lineTo(width, p1.y); ctx.stroke();
  } else if (d.type === 'vertical' && p1) {
    ctx.beginPath(); ctx.moveTo(p1.x, 0); ctx.lineTo(p1.x, height); ctx.stroke();
  } else if ((d.type === 'trend' || d.type === 'measure') && p1 && p2) {
    ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y); ctx.stroke();
    if (d.type === 'measure') {
      const move = ((d.p2.price / d.p1.price) - 1) * 100;
      ctx.font = '800 10px Inter,system-ui';
      ctx.fillText(`${move >= 0 ? '+' : ''}${move.toFixed(2)}%`, (p1.x + p2.x) / 2 + 5, (p1.y + p2.y) / 2 - 5);
    }
  } else if (d.type === 'ray' && p1 && p2) {
    const dx = p2.x - p1.x, dy = p2.y - p1.y;
    const factor = dx === 0 ? 1 : Math.max(1, (width - p1.x) / dx);
    ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(p1.x + dx * factor, p1.y + dy * factor); ctx.stroke();
  } else if (d.type === 'rect' && p1 && p2) {
    ctx.strokeRect(Math.min(p1.x, p2.x), Math.min(p1.y, p2.y), Math.abs(p2.x - p1.x), Math.abs(p2.y - p1.y));
  } else if (d.type === 'fib' && p1 && p2) {
    const levels = [0, .236, .382, .5, .618, .786, 1];
    ctx.font = '700 9px Inter,system-ui';
    for (const level of levels) {
      const y = p1.y + (p2.y - p1.y) * level;
      ctx.beginPath(); ctx.moveTo(Math.min(p1.x, p2.x), y); ctx.lineTo(Math.max(p1.x, p2.x), y); ctx.stroke();
      ctx.fillText(String(level), Math.max(p1.x, p2.x) + 4, y - 2);
    }
  } else if (d.type === 'longPosition' && p1 && p2) {
    const x = Math.min(p1.x, p2.x), w = Math.max(30, Math.abs(p2.x - p1.x));
    ctx.globalAlpha = .16; ctx.fillStyle = COLORS.green; ctx.fillRect(x, Math.min(p1.y, p2.y), w, Math.abs(p2.y - p1.y));
    ctx.globalAlpha = 1; ctx.strokeStyle = COLORS.green; ctx.strokeRect(x, Math.min(p1.y, p2.y), w, Math.abs(p2.y - p1.y));
  } else if (d.type === 'text' && p1) {
    ctx.font = '700 12px Inter,system-ui'; ctx.fillText(d.text || 'Text', p1.x, p1.y);
  }
  ctx.restore();
}

function drawAll() {
  const surface = canvasContext();
  if (!surface) return;
  const { ctx, width, height } = surface;
  for (const drawing of runtime.drawings) drawOneDrawing(ctx, drawing, width, height);
  if (runtime.pendingDrawing) drawOneDrawing(ctx, runtime.pendingDrawing, width, height);
  drawGalka(ctx);
}

function renderSelections() {
  if (!els.selections) return;
  const hasAny = runtime.anchor || runtime.left || runtime.right || runtime.upper.length;
  els.selections.classList.toggle('hidden', !hasAny);
  els.selections.innerHTML = [
    `<span>Якорь <b>${price(runtime.anchor?.price)}</b></span>`,
    `<span>Левая <b>${price(runtime.left?.price)}</b></span>`,
    `<span>Правая <b>${price(runtime.right?.price)}</b></span>`,
    `<span>Верх <b>${runtime.upper.length ? runtime.upper.map(price).join(' · ') : '—'}</b></span>`,
  ].join('');
}

function stageCopy() {
  return {
    idle: ['ГОТОВ', 'Нажми G или «Новая GALKA».'],
    anchor: ['1/4', 'Поставь якорь — это уровень GALKA.'],
    left: ['2/4', 'Поставь левую часть галки слева от якоря.'],
    right: ['3/4', 'Поставь правую часть галки справа от якоря.'],
    upper: ['4/4', 'Поставь верхнюю лимитку от GALKA до +5%.'],
    upper_wait: ['ВЕРХ', 'Лимитка добавлена. + лимитка или Готово.'],
    done: ['ГОТОВО', 'Нижние −2 / −4 / −6 / −8% добавлены.'],
  }[runtime.stage] || ['ГОТОВ', 'Нажми G или «Новая GALKA».'];
}

function renderStage() {
  const [badge, text] = stageCopy();
  const picking = PICK_STAGES.has(runtime.stage);
  const paused = picking && runtime.tool !== 'manualGalka';
  els.stepBadge.textContent = badge;
  els.instruction.textContent = paused ? `${text} Нажми G, чтобы продолжить.` : text;
  els.stepPill?.classList.toggle('active', picking);
  els.frame?.classList.toggle('mem-picking', picking && runtime.tool === 'manualGalka');
  els.canvas?.classList.toggle('drawing', (picking && runtime.tool === 'manualGalka') || (!['cursor', 'crosshair'].includes(runtime.tool) && !runtime.drawingsLocked));
  els.addUpper.disabled = runtime.stage !== 'upper_wait';
  els.done.disabled = runtime.stage !== 'upper_wait' || runtime.upper.length === 0;
  renderSelections();
  drawAll();
}

function snapshotDrawings() {
  runtime.undoStack.push(JSON.stringify(runtime.drawings));
  if (runtime.undoStack.length > 50) runtime.undoStack.shift();
  runtime.redoStack = [];
}

function setTool(tool, notify = false) {
  runtime.tool = tool;
  runtime.pendingDrawing = null;
  els.leftbar?.querySelectorAll('[data-tool]').forEach((button) => button.classList.toggle('active', button.dataset.tool === tool));
  renderStage();
  if (notify) {
    if (tool === 'manualGalka') showToast(PICK_STAGES.has(runtime.stage) ? stageCopy()[1] : 'Нажми на графике: начнём новую GALKA');
    else if (tool === 'cursor') showToast('Курсор: pan/zoom как в Galka Pro');
    else if (tool === 'crosshair') showToast('Перекрестие включено');
  }
}

function resetDraft(clearInputs = true) {
  runtime.stage = 'idle'; runtime.anchor = null; runtime.left = null; runtime.right = null;
  runtime.upper = []; runtime.lower = [];
  if (clearInputs && els.galka) els.galka.value = '';
  syncUpperInputs([]);
  renderTradeLevels(); renderStage();
}

function startDraft() {
  resetDraft(true);
  runtime.stage = 'anchor';
  setTool('manualGalka');
  closeSidebar();
}

function pointFromPointer(event) {
  if (!runtime.chart || !runtime.series || !els.canvas) return null;
  const rect = els.canvas.getBoundingClientRect();
  let x = event.clientX - rect.left, y = event.clientY - rect.top;
  let time = runtime.chart.timeScale().coordinateToTime(x);
  let selectedPrice = runtime.series.coordinateToPrice(y);
  if (time && typeof time === 'object') return null;
  time = Number(time);
  selectedPrice = Number(selectedPrice);
  if (!Number.isFinite(time) || !(selectedPrice > 0)) return null;

  if (runtime.magnet && runtime.candles.length) {
    const nearest = runtime.candles.reduce((best, row) => Math.abs(row.time - time) < Math.abs(best.time - time) ? row : best, runtime.candles[0]);
    const choices = [nearest.open, nearest.high, nearest.low, nearest.close];
    selectedPrice = choices.reduce((best, value) => Math.abs(value - selectedPrice) < Math.abs(best - selectedPrice) ? value : best, choices[0]);
    time = nearest.time;
  }
  return { time, price: selectedPrice };
}

function syncUpperInputs(values = runtime.upper) {
  if (!els.upperLevels) return;
  els.upperLevels.innerHTML = '';
  const rows = values.length ? [...values].sort((a, b) => b - a) : ['', '', ''];
  for (const value of rows) {
    const row = document.createElement('div'); row.className = 'upper-row';
    const input = document.createElement('input'); input.type = 'number'; input.inputMode = 'decimal'; input.step = 'any'; input.className = 'upper-input'; input.placeholder = 'Цена верхней лимитки'; input.value = value === '' ? '' : inputPrice(value);
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×';
    remove.onclick = () => { if (els.upperLevels.children.length > 1) row.remove(); };
    row.append(input, remove); els.upperLevels.append(row);
  }
}

function acceptGalkaPoint(point) {
  if (runtime.stage === 'anchor') {
    runtime.anchor = point;
    els.galka.value = inputPrice(point.price);
    runtime.stage = 'left';
  } else if (runtime.stage === 'left') {
    if (runtime.anchor && point.time >= runtime.anchor.time) return showToast('Левая точка должна быть слева от якоря', 'error');
    runtime.left = point; runtime.stage = 'right';
  } else if (runtime.stage === 'right') {
    if (runtime.anchor && point.time <= runtime.anchor.time) return showToast('Правая точка должна быть справа от якоря', 'error');
    runtime.right = point; runtime.stage = 'upper';
  } else if (runtime.stage === 'upper') {
    const galka = Number(runtime.anchor?.price || els.galka.value);
    if (!(galka > 0)) return showToast('Сначала поставь GALKA', 'error');
    if (point.price < galka * (1 - 1e-8) || point.price > galka * 1.05 * (1 + 1e-8)) return showToast('Верхняя лимитка должна быть от GALKA до +5%', 'error');
    if (runtime.upper.some((value) => Math.abs(value - point.price) <= Math.max(1e-12, galka * 1e-7))) return showToast('Такая лимитка уже есть', 'error');
    runtime.upper.push(point.price); syncUpperInputs(); runtime.stage = 'upper_wait'; setTool('cursor');
  }
  renderTradeLevels(); renderStage();
}

function finishDraft() {
  const galka = Number(runtime.anchor?.price || els.galka.value);
  if (!(galka > 0) || !runtime.upper.length) return showToast('Нужна хотя бы одна верхняя лимитка', 'error');
  runtime.lower = [0.98, 0.96, 0.94, 0.92].map((factor) => galka * factor);
  runtime.stage = 'done'; setTool('cursor'); renderTradeLevels(); renderStage();
  openPanel('galka');
  setTimeout(() => els.preview?.click(), 80);
}

function beginGenericDrawing(point) {
  const tool = runtime.tool;
  if (tool === 'horizontal' || tool === 'vertical') {
    snapshotDrawings(); runtime.drawings.push({ type: tool, p1: point, color: COLORS.blue, width: 2 }); drawAll(); setTool('cursor'); return;
  }
  if (tool === 'text') {
    const text = prompt('Текст на графике:');
    if (text) { snapshotDrawings(); runtime.drawings.push({ type: 'text', p1: point, text, color: COLORS.blue, width: 2 }); drawAll(); }
    setTool('cursor'); return;
  }
  if (tool === 'channel') { showToast('Канал в MEM пока не нужен для выставления GALKA'); setTool('cursor'); return; }
  if (DRAW_TWO.has(tool)) {
    if (!runtime.pendingDrawing) {
      runtime.pendingDrawing = { type: tool, p1: point, p2: point, color: COLORS.blue, width: 2 };
      showToast('Коснись второй точки'); drawAll();
    } else {
      runtime.pendingDrawing.p2 = point; snapshotDrawings(); runtime.drawings.push(runtime.pendingDrawing); runtime.pendingDrawing = null; drawAll(); setTool('cursor');
    }
  }
}

function onCanvasPointerDown(event) {
  if (runtime.drawingsLocked) return;
  const point = pointFromPointer(event);
  if (!point) return;
  if (runtime.tool === 'manualGalka') {
    if (!PICK_STAGES.has(runtime.stage)) { startDraft(); return; }
    acceptGalkaPoint(point); return;
  }
  if (!['cursor', 'crosshair'].includes(runtime.tool)) beginGenericDrawing(point);
}

function onCanvasPointerMove(event) {
  if (!runtime.pendingDrawing || !DRAW_TWO.has(runtime.tool)) return;
  const point = pointFromPointer(event); if (!point) return;
  runtime.pendingDrawing.p2 = point; drawAll();
}

function openPanel(panel) {
  if (!els.sidebar) return;
  els.sidebar.classList.add('open'); els.sidebar.setAttribute('aria-hidden', 'false');
  els.backdrop?.classList.add('visible');
  document.querySelectorAll('.side-panel').forEach((node) => node.classList.toggle('active', node.dataset.panelId === panel));
  document.querySelectorAll('.side-tabs [data-panel]').forEach((node) => node.classList.toggle('active', node.dataset.panel === panel));
  const titles = {
    galka: ['GALKA MEM', 'Новая one-shot кампания'], campaign: ['Campaign', 'Позиция и ордера'],
    account: ['Account', 'Hyperliquid'], more: ['More', 'События и правила'],
  };
  const [title, subtitle] = titles[panel] || titles.galka;
  if (els.sheetTitle) els.sheetTitle.textContent = title;
  if (els.sheetSubtitle) els.sheetSubtitle.textContent = subtitle;
}
function closeSidebar() {
  els.sidebar?.classList.remove('open'); els.sidebar?.setAttribute('aria-hidden', 'true');
  els.backdrop?.classList.remove('visible');
}
function closeTools() { els.leftbar?.classList.remove('open'); }

async function loadCandles(force = false) {
  if (!runtime.series || runtime.candleBusy || !els.coin?.value) return;
  const coin = els.coin.value, interval = els.interval?.value || '1m';
  const changed = coin !== runtime.loadedCoin || interval !== runtime.loadedInterval;
  const full = force || changed || runtime.lastCandleTime == null;
  runtime.candleBusy = true;
  if (full) els.loading?.classList.remove('hidden');
  try {
    const rows = await api(`/api/mem/candles?coin=${encodeURIComponent(coin)}&interval=${encodeURIComponent(interval)}&limit=${full ? 600 : 5}`);
    if (coin !== els.coin.value || interval !== els.interval.value) return;
    const candles = normalizeCandles(rows);
    if (!candles.length) throw new Error(`Нет свечей ${coin} ${interval}`);
    if (full) {
      runtime.series.setData(candles); runtime.candles = candles;
      runtime.loadedCoin = coin; runtime.loadedInterval = interval; runtime.lastCandleTime = candles.at(-1).time;
      fitChart();
    } else {
      const map = new Map(runtime.candles.map((row) => [row.time, row]));
      for (const row of candles) {
        if (runtime.lastCandleTime != null && row.time < runtime.lastCandleTime) continue;
        runtime.series.update(row); map.set(row.time, row); runtime.lastCandleTime = Math.max(runtime.lastCandleTime ?? row.time, row.time);
      }
      runtime.candles = [...map.values()].sort((a, b) => a.time - b.time).slice(-900);
    }
    runtime.latestClose = candles.at(-1).close;
    els.ticker.textContent = price(runtime.latestClose);
    els.watermark.textContent = `${coin} · ${interval}`;
    els.health?.classList.add('ok'); els.health?.classList.remove('error');
    if (els.healthText) els.healthText.textContent = 'Поток есть';
    els.connectionDot?.classList.add('ok'); els.connectionDot?.classList.remove('warn', 'error');
  } catch (error) {
    els.health?.classList.add('error'); els.health?.classList.remove('ok');
    if (els.healthText) els.healthText.textContent = 'Ошибка потока';
    els.connectionDot?.classList.add('error'); els.connectionDot?.classList.remove('ok', 'warn');
    showToast(error.message, 'error');
  } finally {
    runtime.candleBusy = false;
    if (full) els.loading?.classList.add('hidden');
  }
}

function waitForMarket() {
  if (els.coin?.options?.length && els.coin.value) loadCandles(true);
  else setTimeout(waitForMarket, 220);
}

// Drawing rail: exact Galka Pro interaction rule — canvas captures touch only while drawing.
els.leftbar?.addEventListener('click', (event) => {
  const button = event.target.closest('[data-tool]'); if (!button) return;
  const tool = button.dataset.tool;
  if (tool === 'manualGalka') {
    if (!PICK_STAGES.has(runtime.stage)) startDraft(); else setTool('manualGalka', true);
  } else setTool(tool, true);
  if (matchMedia('(max-width:700px)').matches) closeTools();
});
els.canvas?.addEventListener('pointerdown', onCanvasPointerDown);
els.canvas?.addEventListener('pointermove', onCanvasPointerMove);

els.newGalka?.addEventListener('click', startDraft);
els.addUpper?.addEventListener('click', () => { if (runtime.stage === 'upper_wait') { runtime.stage = 'upper'; setTool('manualGalka'); closeSidebar(); } });
els.done?.addEventListener('click', finishDraft);
els.reset?.addEventListener('click', () => resetDraft(true));

els.coin?.addEventListener('change', () => {
  resetDraft(true); runtime.loadedCoin = ''; runtime.lastCandleTime = null; loadCandles(true);
});
els.interval?.addEventListener('change', () => {
  resetDraft(true); runtime.loadedInterval = ''; runtime.lastCandleTime = null; loadCandles(true);
});

els.fit?.addEventListener('click', () => { fitChart(); closeActionMenu(); });
els.latest?.addEventListener('click', () => { latestChart(); closeActionMenu(); });
els.toggleTools?.addEventListener('click', () => { closeSidebar(); els.leftbar?.classList.toggle('open'); });
els.closeTools?.addEventListener('click', closeTools);
els.toggleSidebar?.addEventListener('click', () => openPanel('more'));
els.closeSidebar?.addEventListener('click', closeSidebar);
els.backdrop?.addEventListener('click', closeSidebar);
els.connection?.addEventListener('click', () => openPanel('account'));

document.querySelector('.mobile-nav')?.addEventListener('click', (event) => {
  const button = event.target.closest('[data-mobile-panel]'); if (!button) return;
  const panel = button.dataset.mobilePanel;
  document.querySelectorAll('.mobile-nav [data-mobile-panel]').forEach((node) => node.classList.toggle('active', node === button));
  if (panel === 'chart') closeSidebar(); else openPanel(panel);
});
document.querySelector('.side-tabs')?.addEventListener('click', (event) => {
  const button = event.target.closest('[data-panel]'); if (button) openPanel(button.dataset.panel);
});

function closeActionMenu() {
  els.actionMenu?.classList.add('hidden'); els.actionBtn?.classList.remove('open'); els.actionBtn?.setAttribute('aria-expanded', 'false');
}
els.actionBtn?.addEventListener('click', () => {
  const open = els.actionMenu?.classList.toggle('hidden') === false;
  els.actionBtn.classList.toggle('open', open); els.actionBtn.setAttribute('aria-expanded', String(open));
});
els.quickSetGalka?.addEventListener('click', () => { closeActionMenu(); startDraft(); });
els.quickExactGalka?.addEventListener('click', () => { closeActionMenu(); openPanel('galka'); setTimeout(() => els.galka?.focus(), 180); });
els.quickLevels?.addEventListener('click', () => { runtime.showTradeLevels = !runtime.showTradeLevels; renderTradeLevels(); closeActionMenu(); showToast(runtime.showTradeLevels ? 'Уровни показаны' : 'Уровни скрыты'); });

els.fullscreen?.addEventListener('click', async () => {
  try {
    if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
    else await document.exitFullscreen();
  } catch (_) { showToast('Fullscreen недоступен'); }
});
els.magnet?.addEventListener('click', () => { runtime.magnet = !runtime.magnet; els.magnet.classList.toggle('active', runtime.magnet); showToast(runtime.magnet ? 'Магнит включён' : 'Магнит выключен'); });
els.lock?.addEventListener('click', () => { runtime.drawingsLocked = !runtime.drawingsLocked; els.lock.classList.toggle('active', runtime.drawingsLocked); renderStage(); });
els.hide?.addEventListener('click', () => { runtime.drawingsHidden = !runtime.drawingsHidden; els.hide.classList.toggle('active', runtime.drawingsHidden); drawAll(); });
els.undo?.addEventListener('click', () => {
  if (!runtime.undoStack.length) return; runtime.redoStack.push(JSON.stringify(runtime.drawings)); runtime.drawings = JSON.parse(runtime.undoStack.pop()); drawAll();
});
els.redo?.addEventListener('click', () => {
  if (!runtime.redoStack.length) return; runtime.undoStack.push(JSON.stringify(runtime.drawings)); runtime.drawings = JSON.parse(runtime.redoStack.pop()); drawAll();
});
els.del?.addEventListener('click', () => { if (!runtime.drawings.length) return; snapshotDrawings(); runtime.drawings.pop(); drawAll(); });
els.clear?.addEventListener('click', () => { if (!runtime.drawings.length) return; snapshotDrawings(); runtime.drawings = []; drawAll(); });

window.addEventListener('resize', resizeCanvas);

initChart();
renderStage();
waitForMarket();
runtime.refreshTimer = setInterval(() => loadCandles(false), 5000);
