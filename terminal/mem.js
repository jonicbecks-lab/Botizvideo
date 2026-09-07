const $ = (id) => document.getElementById(id);

const els = {
  liveBadge: $('liveBadge'),
  account: $('account'),
  withdrawable: $('withdrawable'),
  marginUsed: $('marginUsed'),
  safeMode: $('safeMode'),
  coin: $('coinSelect'),
  currentPrice: $('currentPrice'),
  galka: $('galkaPrice'),
  margin: $('campaignMargin'),
  leverage: $('leverage'),
  maxLeverage: $('maxLeverage'),
  upperLevels: $('upperLevels'),
  addUpper: $('addUpper'),
  templateUpper: $('templateUpper'),
  previewButton: $('previewButton'),
  previewCard: $('previewCard'),
  safetyBadge: $('safetyBadge'),
  previewNotional: $('previewNotional'),
  previewMargin: $('previewMargin'),
  upperAvg: $('upperAvg'),
  upperTp: $('upperTp'),
  deepestLower: $('deepestLower'),
  maxLiq: $('maxLiq'),
  safetyReason: $('safetyReason'),
  planLevels: $('planLevels'),
  liqStates: $('liqStates'),
  startLive: $('startLive'),
  campaignBox: $('campaignBox'),
  cancelCampaign: $('cancelCampaign'),
  emergencyClose: $('emergencyClose'),
  reconcile: $('reconcile'),
  events: $('events'),
  toast: $('toast'),
};

const ACTIVE = new Set(['placing', 'waiting', 'open', 'closing', 'recovery']);
const hashParams = new URLSearchParams(location.hash.replace(/^#/, ''));
const hashToken = hashParams.get('token');
if (hashToken) {
  sessionStorage.setItem('galkaLiveSession', hashToken);
  history.replaceState(null, '', location.pathname + location.search);
}
const sessionToken = sessionStorage.getItem('galkaLiveSession') || '';

const runtime = {
  status: null,
  pendingPreview: null,
  statusBusy: false,
  toastTimer: null,
  marketsLoaded: false,
};

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

function money(value) {
  const n = Number(value || 0);
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function price(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return '—';
  if (n >= 1000) return n.toFixed(2);
  if (n >= 1) return n.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
  return Number(n.toPrecision(7)).toString();
}

function pct(value) {
  const n = Number(value || 0);
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}

function toast(message, type = '') {
  els.toast.textContent = message;
  els.toast.className = 'toast ' + type;
  clearTimeout(runtime.toastTimer);
  runtime.toastTimer = setTimeout(() => els.toast.classList.add('hidden'), 4500);
}

async function api(path, { method = 'GET', body } = {}) {
  if (!sessionToken) throw new Error('Открой GALKA MEM через защищённую ссылку из Termux');
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
    throw new Error('Сервер вернул некорректный ответ');
  }
  if (!response.ok || payload?.ok === false) {
    throw new Error(payload?.error || `HTTP ${response.status}`);
  }
  return payload.data;
}

function addUpperRow(value = '') {
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
  els.upperLevels.append(row);
}

function upperPrices() {
  return [...els.upperLevels.querySelectorAll('.upper-input')]
    .map((input) => input.value.trim())
    .filter(Boolean)
    .map(Number);
}

function selectedMarket() {
  return (runtime.status?.markets || []).find((row) => row.name === els.coin.value) || null;
}

function updateMarketFields() {
  const market = selectedMarket();
  const mid = runtime.status?.mids?.[els.coin.value];
  els.currentPrice.value = price(mid);
  els.maxLeverage.value = market ? `${market.maxLeverage}x` : '—';
  if (market) {
    els.leverage.max = String(market.maxLeverage);
    if (Number(els.leverage.value) > market.maxLeverage) {
      els.leverage.value = String(market.maxLeverage);
    }
  }
}

function populateMarkets() {
  if (runtime.marketsLoaded) return;
  const markets = runtime.status?.markets || [];
  if (!markets.length) return;
  const previous = els.coin.value;
  els.coin.innerHTML = markets
    .map((row) => `<option value="${esc(row.name)}">${esc(row.name)} · max ${row.maxLeverage}x</option>`)
    .join('');
  const preferred = markets.some((row) => row.name.toUpperCase() === 'CASHCAT')
    ? markets.find((row) => row.name.toUpperCase() === 'CASHCAT').name
    : (markets.some((row) => row.name === previous) ? previous : markets[0].name);
  els.coin.value = preferred;
  runtime.marketsLoaded = true;
  updateMarketFields();
}

function renderAccount() {
  const status = runtime.status;
  if (!status) return;
  els.account.textContent = `${status.network} · ${status.account}`;
  els.withdrawable.textContent = money(status.accountState?.withdrawable);
  els.marginUsed.textContent = money(status.accountState?.totalMarginUsed);
  const safe = !!status.system?.safeMode;
  els.safeMode.textContent = safe ? 'BLOCK' : 'OK';
  els.liveBadge.textContent = safe ? 'SAFE MODE' : (status.liveEnabled ? 'LIVE ON' : 'LIVE OFF');
  els.liveBadge.className = `badge ${!safe && status.liveEnabled ? 'on' : 'off'}`;
}

function renderCampaign() {
  const campaign = runtime.status?.campaign;
  const active = campaign && ACTIVE.has(campaign.status);
  if (!campaign) {
    els.campaignBox.textContent = 'Нет активной GALKA MEM.';
  } else {
    const position = runtime.status?.accountState?.positions?.[campaign.coin];
    const levels = (campaign.levels || []).map((row) => (
      `<div class="plan-row"><b>${row.basket === 'upper' ? 'UP' : 'LOW'} L${row.index}</b>` +
      `<span>${price(row.price)} <small>${pct(row.offset_pct)} · ${esc(row.status)}</small></span>` +
      `<span>${Number(row.filledSize || 0).toPrecision(4)}</span></div>`
    )).join('');
    els.campaignBox.innerHTML =
      `<div class="campaign-grid">` +
      `<span><small>Монета</small><b>${esc(campaign.coin)}</b></span>` +
      `<span><small>Статус</small><b>${esc(campaign.status)}</b></span>` +
      `<span><small>Цикл</small><b>${campaign.lowerTouched ? 'BIG' : 'SMALL'}</b></span>` +
      `<span><small>Позиция</small><b>${Number(position?.size || 0).toPrecision(5)}</b></span>` +
      `<span><small>Upper AVG</small><b>${price(campaign.upperAverageFill)}</b></span>` +
      `<span><small>Upper TP</small><b>${price(campaign.upperTakeProfit)}</b></span>` +
      `</div><div class="campaign-levels">${levels}</div>` +
      (campaign.completedReason ? `<p class="rule">${esc(campaign.completedReason)}</p>` : '');
  }
  els.cancelCampaign.disabled = !active || campaign?.status === 'open' || Number(campaign?.actualPosition || 0) !== 0;
  els.emergencyClose.disabled = !active;
}

function renderEvents() {
  const rows = (runtime.status?.events || []).slice().reverse().slice(0, 30);
  els.events.innerHTML = rows.length ? rows.map((row) => (
    `<div class="event"><b>${esc(row.message)}</b><small>${esc(row.time)} · ${esc(row.type)}</small></div>`
  )).join('') : '<div class="event"><small>Событий пока нет.</small></div>';
}

function renderStatus() {
  populateMarkets();
  renderAccount();
  updateMarketFields();
  renderCampaign();
  renderEvents();
}

async function refreshStatus() {
  if (runtime.statusBusy) return;
  runtime.statusBusy = true;
  try {
    runtime.status = await api('/api/mem/status');
    renderStatus();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    runtime.statusBusy = false;
  }
}

function renderPreview(plan) {
  els.previewCard.classList.remove('hidden');
  els.safetyBadge.textContent = plan.safe ? 'SAFE' : 'BLOCK';
  els.safetyBadge.className = `badge ${plan.safe ? 'safe' : 'unsafe'}`;
  els.previewNotional.textContent = money(plan.actualNotional);
  els.previewMargin.textContent = money(plan.actualMargin);
  els.upperAvg.textContent = price(plan.upperAverage);
  els.upperTp.textContent = `${price(plan.upperTakeProfit)} · +${Number(plan.upperTargetMovePct).toFixed(2)}%`;
  els.deepestLower.textContent = price(plan.deepestLowerPrice);
  els.maxLiq.textContent = price(plan.requiredMaxLiquidationPrice);

  const failed = (plan.liquidationStates || []).filter((row) => !row.safe);
  const reasons = [plan.marketReason, plan.marginReason].filter(Boolean);
  if (failed.length) {
    const row = failed[0];
    reasons.push(
      `L${row.last_level_index}: liq ${price(row.estimated_liquidation_price)} > разрешённых ${price(row.required_max_liquidation_price)}`,
    );
  }
  els.safetyReason.textContent = plan.safe
    ? 'Все последовательные fill-состояния проходят правило: ликвидация ниже последней нижней лимитки ещё минимум на 5%.'
    : (reasons.join(' · ') || 'Safety gate не пройден.');
  els.safetyReason.className = `notice ${plan.safe ? 'good' : 'bad'}`;

  els.planLevels.innerHTML = (plan.levels || []).map((row) => (
    `<div class="plan-row"><b>${row.basket === 'upper' ? 'UP' : 'LOW'} L${row.index}</b>` +
    `<span>${price(row.price)} <small>${pct(row.offset_pct)} · w ${row.weight}</small></span>` +
    `<span>${money(row.margin)} / ${money(row.notional)}</span></div>`
  )).join('');

  els.liqStates.innerHTML = (plan.liquidationStates || []).map((row) => (
    `<div class="liq-row"><b>до L${row.last_level_index}</b>` +
    `<span>AVG ${price(row.average_entry)} <small>liq ${price(row.estimated_liquidation_price)}</small></span>` +
    `<span class="badge ${row.safe ? 'safe' : 'unsafe'}">${row.safe ? 'OK' : 'NO'}</span></div>`
  )).join('');
  els.startLive.disabled = !plan.safe || !runtime.status?.liveEnabled;
  els.previewCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function preview() {
  const payload = {
    coin: els.coin.value,
    galkaPrice: Number(els.galka.value),
    upperPrices: upperPrices(),
    campaignMargin: Number(els.margin.value),
    leverage: Number(els.leverage.value),
  };
  if (!(payload.galkaPrice > 0)) return toast('Введи цену GALKA', 'error');
  if (!payload.upperPrices.length || payload.upperPrices.some((value) => !(value > 0))) {
    return toast('Добавь хотя бы одну корректную верхнюю лимитку', 'error');
  }
  try {
    const plan = await api('/api/mem/preview', { method: 'POST', body: payload });
    runtime.pendingPreview = { payload, plan };
    renderPreview(plan);
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function startLive() {
  if (!runtime.pendingPreview?.plan?.safe) return;
  if (!window.confirm('Выставить реальные one-shot лимитки GALKA MEM на Hyperliquid?')) return;
  const body = {
    ...runtime.pendingPreview.payload,
    confirmation: 'PLACE_GALKA_MEM_REAL_ORDERS',
  };
  els.startLive.disabled = true;
  try {
    await api('/api/mem/campaign', { method: 'POST', body });
    toast('Реальные GALKA MEM лимитки выставлены', 'ok');
    runtime.pendingPreview = null;
    els.previewCard.classList.add('hidden');
    await refreshStatus();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    els.startLive.disabled = false;
  }
}

async function cancelCampaign() {
  if (!window.confirm('Отменить GALKA MEM до первого fill?')) return;
  try {
    await api('/api/mem/cancel', {
      method: 'POST',
      body: { confirmation: 'CANCEL_GALKA_MEM' },
    });
    toast('Кампания отменена', 'ok');
    await refreshStatus();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function emergencyClose() {
  if (!window.confirm('АВАРИЙНО: отменить owned-ордера и закрыть реальную позицию рынком?')) return;
  try {
    await api('/api/mem/emergency', {
      method: 'POST',
      body: { confirmation: 'EMERGENCY_CLOSE_GALKA_MEM' },
    });
    toast('Аварийное закрытие отправлено', 'ok');
    await refreshStatus();
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function reconcile() {
  try {
    runtime.status = await api('/api/mem/reconcile', {
      method: 'POST',
      body: { confirmation: 'RECONCILE_GALKA_MEM' },
    });
    renderStatus();
    toast('GALKA MEM сверена с Hyperliquid', 'ok');
  } catch (error) {
    toast(error.message, 'error');
  }
}

els.addUpper.addEventListener('click', () => addUpperRow());
els.templateUpper.addEventListener('click', () => {
  const g = Number(els.galka.value);
  if (!(g > 0)) return toast('Сначала введи GALKA', 'error');
  els.upperLevels.innerHTML = '';
  [1.04, 1.03, 1.02, 1.00].forEach((factor) => addUpperRow(String(Number((g * factor).toPrecision(9)))));
});
els.coin.addEventListener('change', () => {
  runtime.pendingPreview = null;
  els.previewCard.classList.add('hidden');
  updateMarketFields();
});
els.previewButton.addEventListener('click', preview);
els.startLive.addEventListener('click', startLive);
els.cancelCampaign.addEventListener('click', cancelCampaign);
els.emergencyClose.addEventListener('click', emergencyClose);
els.reconcile.addEventListener('click', reconcile);

addUpperRow();
addUpperRow();
addUpperRow();
refreshStatus();
setInterval(refreshStatus, 2000);
