const $ = (id) => document.getElementById(id);

const els = {
  symbol: $('symbolSelect'),
  interval: $('intervalSelect'),
  ticker: $('ticker'),
  liveBadge: $('liveBadge'),
  chart: $('chart'),
  watermark: $('watermark'),
  loading: $('loading'),
  toast: $('toast'),
  details: $('detailsButton'),
  status: $('campaignStatus'),
  input: $('galkaInput'),
  preview: $('previewButton'),
  backdrop: $('backdrop'),
  drawer: $('drawer'),
  closeDrawer: $('closeDrawer'),
  drawerAccount: $('drawerAccount'),
  accountValue: $('accountValue'),
  withdrawable: $('withdrawable'),
  marginUsed: $('marginUsed'),
  riskMode: $('riskMode'),
  campaignDetails: $('campaignDetails'),
  cancel: $('cancelCampaign'),
  emergency: $('emergencyClose'),
  notifications: $('enableNotifications'),
  reconcile: $('reconcileState'),
  events: $('events'),
  modal: $('previewModal'),
  closePreview: $('closePreview'),
  previewCancel: $('previewCancel'),
  confirm: $('confirmLive'),
  previewGalka: $('previewGalka'),
  previewNotional: $('previewNotional'),
  previewMargin: $('previewMargin'),
  previewLeverage: $('previewLeverage'),
  previewLevels: $('previewLevels'),
};

const ACTIVE = new Set(['placing', 'waiting', 'open', 'closing', 'canceling', 'emergency', 'recovery']);
const COLORS = {
  green: '#16c784',
  red: '#ef5350',
  orange: '#ff9800',
  gray: '#7c8797',
  cyan: '#26c6da',
};


const hashParams = new URLSearchParams(location.hash.replace(/^#/, ''));
const hashToken = hashParams.get('token');
if (hashToken) {
  sessionStorage.setItem('galkaLiveSession', hashToken);
  history.replaceState(null, '', location.pathname + location.search);
}
const sessionToken = sessionStorage.getItem('galkaLiveSession') || '';

const runtime = {
  coin: 'BTC',
  interval: '15m',
  chart: null,
  series: null,
  lines: [],
  status: null,
  pendingPreview: null,
  lastEventKey: null,
  candleBusy: false,
  statusBusy: false,
  candlesLoaded: false,
  lastCandleTime: null,
  toastTimer: null,
  lineSignature: '',
  detailsSignature: '',
  eventsSignature: '',
};

function money(value) {
  return '$' + Number(value || 0).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function price(value, coin = runtime.coin) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  return Number(value).toFixed(coin === 'SOL' ? 4 : 2);
}

function signedMoney(value) {
  const number = Number(value || 0);
  return (number >= 0 ? '+' : '') + money(number);
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[char]);
}

function toast(message, type = '') {
  els.toast.textContent = message;
  els.toast.className = 'toast ' + type;
  clearTimeout(runtime.toastTimer);
  runtime.toastTimer = setTimeout(() => els.toast.classList.add('hidden'), 4500);
}

async function api(path, { method = 'GET', body } = {}) {
  if (!sessionToken) throw new Error('Открой терминал через защищённую ссылку из Termux');
  const headers = { 'X-Galka-Session': sessionToken };
  if (body) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
    credentials: 'same-origin',
  });
  let payload;
  try {
    payload = await response.json();
  } catch (_) {
    throw new Error('LIVE-сервер вернул некорректный ответ');
  }
  if (!response.ok || payload?.ok === false) {
    throw new Error(payload?.error || `HTTP ${response.status}`);
  }
  return payload.data;
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

function candleRow(row) {
  return {
    time: Number(row.time),
    open: Number(row.open),
    high: Number(row.high),
    low: Number(row.low),
    close: Number(row.close),
  };
}

function normalizeCandles(rows) {
  const unique = new Map();
  for (const source of rows || []) {
    const row = candleRow(source);
    if (
      !Number.isFinite(row.time) ||
      !Number.isFinite(row.open) ||
      !Number.isFinite(row.high) ||
      !Number.isFinite(row.low) ||
      !Number.isFinite(row.close)
    ) {
      continue;
    }
    unique.set(row.time, row);
  }
  return [...unique.values()].sort((left, right) => left.time - right.time);
}

async function loadCandles({ initial = false } = {}) {
  if (runtime.candleBusy) return;
  runtime.candleBusy = true;
  const fullReload = initial || !runtime.candlesLoaded;
  const requestedCoin = runtime.coin;
  const requestedInterval = runtime.interval;
  if (fullReload) els.loading.classList.remove('hidden');

  try {
    const limit = fullReload ? 600 : 3;
    const rows = await api(
      `/api/live/candles?coin=${encodeURIComponent(requestedCoin)}` +
      `&interval=${encodeURIComponent(requestedInterval)}&limit=${limit}`,
    );

    if (requestedCoin !== runtime.coin || requestedInterval !== runtime.interval) return;
    const candles = normalizeCandles(rows);
    if (!candles.length) return;

    if (fullReload) {
      runtime.series.setData(candles);
      runtime.candlesLoaded = true;
      runtime.lastCandleTime = candles.at(-1).time;
      autoCenter();
    } else {
      for (const row of candles) {
        if (runtime.lastCandleTime != null && row.time < runtime.lastCandleTime) continue;
        runtime.series.update(row);
        runtime.lastCandleTime = Math.max(runtime.lastCandleTime ?? row.time, row.time);
      }
    }
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    runtime.candleBusy = false;
    if (fullReload) els.loading.classList.add('hidden');
  }
}

function currentCampaign() {
  const campaign = runtime.status?.campaigns?.[runtime.coin];
  return campaign && ACTIVE.has(campaign.status) ? campaign : null;
}

function currentPosition() {
  return runtime.status?.accountState?.positions?.[runtime.coin] || null;
}

function clearLines() {
  for (const line of runtime.lines) {
    try {
      runtime.series.removePriceLine(line);
    } catch (_) {
      // The chart may already be rebuilding after a symbol change.
    }
  }
  runtime.lines = [];
}

function addLine(value, color, title, style = LightweightCharts.LineStyle.Dashed, width = 1) {
  if (!(Number(value) > 0)) return;
  runtime.lines.push(runtime.series.createPriceLine({
    price: Number(value),
    color,
    lineWidth: width,
    lineStyle: style,
    axisLabelVisible: true,
    title,
  }));
}

function renderLines(campaign, position) {
  const signature = JSON.stringify({
    id: campaign?.id || null,
    galka: campaign?.galkaPrice || null,
    levels: campaign?.levels?.map((level) => [level.index, level.price, level.status]) || [],
    entry: position?.entryPrice || null,
  });
  if (signature === runtime.lineSignature) return;
  runtime.lineSignature = signature;

  clearLines();
  if (!campaign) return;

  addLine(campaign.galkaPrice, COLORS.orange, 'GALKA', LightweightCharts.LineStyle.Solid, 2);
  for (const level of campaign.levels || []) {
    const filled = ['filled', 'partial'].includes(level.status);
    addLine(
      level.price,
      filled ? COLORS.green : COLORS.gray,
      `L${level.index}`,
      LightweightCharts.LineStyle.Dashed,
      1,
    );
  }
  if (position?.entryPrice) {
    addLine(position.entryPrice, COLORS.cyan, 'AVG', LightweightCharts.LineStyle.Solid, 2);
  }
}

function renderCampaignDetails(campaign, position) {
  const signature = JSON.stringify({
    campaign: campaign ? {
      id: campaign.id,
      status: campaign.status,
      galkaPrice: campaign.galkaPrice,
      l1Cycles: campaign.l1Cycles,
      l1RealizedPnl: campaign.l1RealizedPnl,
      levels: campaign.levels?.map((level) => [
        level.index,
        level.status,
        level.filledSize,
        level.notional,
      ]),
    } : null,
    position: position ? [position.size, position.entryPrice, position.unrealizedPnl] : null,
  });
  if (signature === runtime.detailsSignature) return;
  runtime.detailsSignature = signature;

  if (!campaign) {
    const safeReason = runtime.status?.system?.safeModeReason;
    els.campaignDetails.innerHTML =
      '<div class="campaign-card"><small>' +
      (safeReason ? `SAFE MODE: ${esc(safeReason)}` : 'Нет активной GALKA для выбранной монеты.') +
      '</small></div>';
    els.cancel.disabled = true;
    els.emergency.disabled = !(position && Math.abs(position.size) > 0);
    return;
  }

  const levels = (campaign.levels || []).map((level) => (
    `<div class="level-row"><span><b>L${level.index}</b> ` +
    `<small>−${Number(level.depth_pct).toFixed(2)}%</small></span>` +
    `<span><b>${price(level.price)}</b> ` +
    `<small>${money(level.notional)} · ${esc(level.status)}</small></span></div>`
  )).join('');

  els.campaignDetails.innerHTML =
    `<div class="campaign-card">` +
    `<div class="row"><span><small>GALKA</small><b>${price(campaign.galkaPrice)}</b></span>` +
    `<span><small>Статус</small><b>${esc(campaign.status)}</b></span></div>` +
    `<div class="row"><span><small>L1 циклы</small><b>${Number(campaign.l1Cycles || 0)}</b></span>` +
    `<span><small>L1 прибыль</small><b>${signedMoney(campaign.l1RealizedPnl)}</b></span></div>` +
    (position ? `<div class="row"><span><small>Позиция</small><b>${position.size}</b></span>` +
      `<span><small>Средняя</small><b>${price(position.entryPrice)}</b></span></div>` : '') +
    `</div>${levels}`;

  els.cancel.disabled = campaign.status !== 'waiting' || !!(position && Math.abs(position.size) > 0);
  els.emergency.disabled = !(position && Math.abs(position.size) > 0);
}

function renderEvents() {
  const events = (runtime.status?.events || []).slice().reverse().slice(0, 40);
  const signature = JSON.stringify(events.map((event) => [event.time, event.type, event.message]));
  if (signature === runtime.eventsSignature) return;
  runtime.eventsSignature = signature;

  els.events.innerHTML = events.length
    ? events.map((event) => (
      `<div class="event"><b>${esc(event.message)}</b>` +
      `<small>${new Date(event.time).toLocaleString('ru-RU')}</small></div>`
    )).join('')
    : '<div class="event"><small>Событий пока нет.</small></div>';

  const newest = events[0];
  if (!newest) return;
  const key = newest.time + '|' + newest.message;
  if (runtime.lastEventKey && runtime.lastEventKey !== key) {
    toast(newest.message, newest.type === 'error' || newest.type === 'risk' ? 'error' : 'ok');
    if (Notification.permission === 'granted') {
      new Notification('Galka LIVE', { body: newest.message });
    }
  }
  runtime.lastEventKey = key;
}

function renderStatus() {
  const status = runtime.status;
  if (!status) return;

  const mid = status.mids?.[runtime.coin];
  els.ticker.textContent = price(mid);
  els.watermark.textContent = `${runtime.coin} · ${runtime.interval} · HYPERLIQUID`;
  const monitorFailed = status.liveEnabled && status.system?.monitorStarted && !status.system?.monitorAlive;
  const safeMode = !!status.system?.safeMode || monitorFailed;
  const safeReason = status.system?.safeModeReason || (monitorFailed ? 'Фоновый LIVE-монитор остановлен' : '');
  els.liveBadge.textContent = safeMode ? 'SAFE MODE' : (status.liveEnabled ? 'LIVE ON' : 'LIVE OFF');
  els.liveBadge.className = 'live-badge ' + (!safeMode && status.liveEnabled ? 'on' : 'off');
  els.liveBadge.title = safeMode ? (safeReason || 'Требуется сверка') : '';
  els.drawerAccount.textContent = `${status.network} · ${status.account}`;

  const account = status.accountState || {};
  els.accountValue.textContent = money(account.accountValue);
  els.withdrawable.textContent = money(account.withdrawable);
  els.marginUsed.textContent = money(account.totalMarginUsed);
  els.riskMode.textContent = `${status.leverage}x ${status.isolated ? 'isolated' : 'cross'}`;

  const campaign = currentCampaign();
  const position = currentPosition();
  if (!campaign) {
    els.status.textContent = `${runtime.coin} · нет GALKA`;
    els.status.className = 'campaign-status idle';
    els.preview.disabled = safeMode;
    els.input.disabled = safeMode;
    els.preview.textContent = safeMode ? 'SAFE MODE' : 'Проверить';
  } else {
    const filled = (campaign.levels || []).filter((level) =>
      ['filled', 'partial'].includes(level.status)).length;
    const cycles = Number(campaign.l1Cycles || 0);
    const cycleText = cycles
      ? ` · L1×${cycles} ${signedMoney(campaign.l1RealizedPnl)}`
      : '';

    if (position && Math.abs(position.size) > 0) {
      els.status.textContent =
        `${runtime.coin} · ${filled}/8 · ${signedMoney(position.unrealizedPnl)}${cycleText}`;
      els.status.className = 'campaign-status open';
    } else {
      els.status.textContent = `${runtime.coin} · ждём ${filled}/8${cycleText}`;
      els.status.className = 'campaign-status waiting';
    }
    els.preview.disabled = true;
    els.input.disabled = true;
    els.input.value = price(campaign.galkaPrice);
    els.preview.textContent = 'Активна';
  }

  renderCampaignDetails(campaign, position);
  renderEvents();
  renderLines(campaign, position);
}

async function refreshStatus() {
  if (runtime.statusBusy) return;
  runtime.statusBusy = true;
  try {
    runtime.status = await api('/api/live/status');
    renderStatus();
  } catch (error) {
    els.liveBadge.textContent = 'SERVER OFF';
    els.liveBadge.className = 'live-badge off';
    toast(error.message, 'error');
  } finally {
    runtime.statusBusy = false;
  }
}

async function statusLoop() {
  await refreshStatus();
  const delay = 5000;
  setTimeout(statusLoop, delay);
}

async function candleLoop() {
  await loadCandles();
  setTimeout(candleLoop, 15000);
}

function openDrawer() {
  els.backdrop.classList.remove('hidden');
  els.drawer.classList.remove('hidden');
}

function closeDrawer() {
  els.backdrop.classList.add('hidden');
  els.drawer.classList.add('hidden');
}

function openPreview() {
  els.modal.classList.remove('hidden');
}

function closePreview() {
  els.modal.classList.add('hidden');
  runtime.pendingPreview = null;
}

async function previewGalka() {
  const galkaPrice = Number(els.input.value);
  if (!(galkaPrice > 0)) return toast('Введи цену GALKA', 'error');

  els.preview.disabled = true;
  els.preview.textContent = 'Проверка…';
  try {
    const data = await api('/api/live/preview', {
      method: 'POST',
      body: { coin: runtime.coin, galkaPrice },
    });
    runtime.pendingPreview = data;
    els.previewGalka.textContent = price(data.galkaPrice);
    els.previewNotional.textContent = money(data.actualNotional);
    els.previewMargin.textContent = money(data.requiredMargin);
    els.previewLeverage.textContent = `${data.leverage}x isolated`;
    els.previewLevels.innerHTML = data.levels.map((level) => (
      `<div class="level-row"><span><b>L${level.index}</b> ` +
      `<small>−${Number(level.depth_pct).toFixed(2)}%</small></span>` +
      `<span><b>${price(level.price)}</b> <small>${money(level.notional)}</small></span></div>`
    )).join('');
    els.confirm.disabled = !data.liveEnabled || !!runtime.status?.system?.safeMode;
    openPreview();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    const safeMode = !!runtime.status?.system?.safeMode;
    els.preview.disabled = !!currentCampaign() || safeMode;
    els.preview.textContent = currentCampaign() ? 'Активна' : (safeMode ? 'SAFE MODE' : 'Проверить');
  }
}

async function confirmLive() {
  const data = runtime.pendingPreview;
  if (!data) return;
  els.confirm.disabled = true;
  els.confirm.textContent = 'Отправка…';
  try {
    await api('/api/live/campaign', {
      method: 'POST',
      body: {
        coin: data.coin,
        galkaPrice: data.galkaPrice,
        confirmation: 'PLACE_REAL_ORDERS',
      },
    });
    closePreview();
    toast('Реальные лимитки выставлены', 'ok');
    await refreshStatus();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    els.confirm.disabled = false;
    els.confirm.textContent = 'Выставить реальные лимитки';
  }
}

async function cancelCampaign() {
  if (!confirm(`Отменить все ожидающие ордера ${runtime.coin}?`)) return;
  try {
    await api('/api/live/cancel', {
      method: 'POST',
      body: { coin: runtime.coin },
    });
    toast('GALKA отменена', 'ok');
    await refreshStatus();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function emergencyClose() {
  if (!confirm(`АВАРИЙНО закрыть реальную позицию ${runtime.coin} по рынку и отменить ордера?`)) return;
  if (!confirm('Это рыночное закрытие с возможным проскальзыванием. Подтвердить ещё раз?')) return;
  try {
    await api('/api/live/emergency', {
      method: 'POST',
      body: {
        coin: runtime.coin,
        confirmation: 'EMERGENCY_CLOSE_REAL_POSITION',
      },
    });
    toast('Аварийное закрытие подтверждено биржей', 'error');
    await refreshStatus();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function reconcileState() {
  if (!confirm('Сверить локальное состояние с позициями и ордерами Hyperliquid?')) return;
  els.reconcile.disabled = true;
  try {
    const result = await api('/api/live/reconcile', {
      method: 'POST',
      body: { confirmation: 'RECONCILE_LOCAL_STATE' },
    });
    toast(result.safeMode ? `SAFE MODE остаётся: ${result.risks.join('; ')}` : 'Сверка завершена, SAFE MODE снят', result.safeMode ? 'error' : 'ok');
    await refreshStatus();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    els.reconcile.disabled = false;
  }
}

els.symbol.onchange = async () => {
  runtime.coin = els.symbol.value;
  runtime.candlesLoaded = false;
  runtime.lastCandleTime = null;
  runtime.lineSignature = '';
  runtime.detailsSignature = '';
  els.input.value = '';
  await loadCandles({ initial: true });
  renderStatus();
};

els.interval.onchange = async () => {
  runtime.interval = els.interval.value;
  runtime.candlesLoaded = false;
  runtime.lastCandleTime = null;
  await loadCandles({ initial: true });
  renderStatus();
};

els.preview.onclick = previewGalka;
els.input.onkeydown = (event) => {
  if (event.key === 'Enter') previewGalka();
};
els.confirm.onclick = confirmLive;
els.closePreview.onclick = closePreview;
els.previewCancel.onclick = closePreview;
els.modal.onclick = (event) => {
  if (event.target === els.modal) closePreview();
};
els.details.onclick = openDrawer;
els.status.onclick = openDrawer;
els.liveBadge.onclick = openDrawer;
els.closeDrawer.onclick = closeDrawer;
els.backdrop.onclick = closeDrawer;
els.cancel.onclick = cancelCampaign;
els.emergency.onclick = emergencyClose;
els.reconcile.onclick = reconcileState;
els.notifications.onclick = async () => {
  if (!('Notification' in window)) {
    return toast('Браузер не поддерживает оповещения', 'error');
  }
  const permission = await Notification.requestPermission();
  toast(
    permission === 'granted' ? 'Оповещения включены' : 'Оповещения не разрешены',
    permission === 'granted' ? 'ok' : 'error',
  );
};

initChart();
await refreshStatus();
await loadCandles({ initial: true });
statusLoop();
candleLoop();
