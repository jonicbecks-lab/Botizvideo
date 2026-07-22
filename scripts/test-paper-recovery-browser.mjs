import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { chromium } from 'playwright';

const PORT = 4176;
const BASE_URL = `http://127.0.0.1:${PORT}`;
const server = spawn(
  'python3',
  ['-m', 'http.server', String(PORT), '--bind', '127.0.0.1', '--directory', '.'],
  { stdio: 'ignore' },
);

async function waitForServer() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      if ((await fetch(`${BASE_URL}/terminal/pro.html`)).ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('Paper recovery browser server did not start');
}

async function waitForPageCondition(page, condition, timeout = 10_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await page.evaluate(condition)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Page condition timed out after ${timeout}ms`);
}

function levels(galka = 100) {
  return Array.from({ length: 10 }, (_, index) => {
    const depthPct = (index + 1) * 0.15;
    return {
      index: index + 1,
      depthPct,
      weight: 0.1,
      price: galka * (1 - depthPct / 100),
      notional: 40,
      status: 'pending',
      fillPrice: null,
      fillTime: null,
      qty: 0,
      fee: 0,
    };
  });
}

const now = Date.now();
const currentMinute = Math.floor(now / 60_000) * 60_000;
const firstMinute = currentMinute - 4 * 60_000;
const gapStartedAt = firstMinute + 30_000;
const campaign = {
  campaignId: 'C-BTC-browser-recovery',
  symbol: 'BTCUSDT',
  patternId: 'M-BTC-browser-recovery',
  source: 'manual',
  trainingExampleId: null,
  status: 'waiting',
  vLow: 100,
  target: 100,
  createdAt: new Date(gapStartedAt - 10_000).toISOString(),
  expiresAt: now + 72 * 3_600_000,
  exitMode: 'trail',
  reclaimPrice: 100.1,
  trailArmed: false,
  trailHigh: null,
  trailStop: null,
  trailActivatedAt: null,
  levels: levels(),
  qty: 0,
  filledNotional: 0,
  averageEntry: null,
  entryFees: 0,
  unrealizedPnl: 0,
};

function recoverySymbol(lastMarketAt = gapStartedAt) {
  return {
    lastMarketAt,
    lastRecoveredCloseAt: null,
    lastRecoveryAt: null,
    lastRecoveryStatus: 'pending',
    recoveredCandles: 0,
    boundaryCandles: 0,
  };
}

const seed = {
  schemaVersion: 3,
  ui: {
    theme: 'dark',
    symbol: 'BTCUSDT',
    interval: '15m',
    chartType: 'candles',
    compare: '',
    scaleMode: 'normal',
    indicators: { sma20: false, ema20: false, ema50: false, bollinger: false, vwap: false, volume: true },
    lowerIndicator: null,
    magnet: true,
    drawingsLocked: false,
    drawingsHidden: false,
    showLevels: true,
    drawings: {},
    templates: {},
    alerts: [],
    radar: { enabled: false, minScore: 45, filter: 'all', visibleOnly: false },
    onboarding: { completed: true, version: 1 },
    sheet: { panel: 'paper', snap: 'medium' },
  },
  paper: {
    settings: {
      startingBalance: 1000,
      leverage: 10,
      symbolNotional: 400,
      maxHours: 72,
      signalMode: 'manual',
      ladderStepPct: 0.15,
      manualDepthPct: 1.5,
      exitMode: 'trail',
      reclaimBufferPct: 0.1,
      trailDistancePct: 0.75,
      makerFee: 0.0002,
      takerFee: 0.0005,
      slippage: 0.0002,
      maintenanceMargin: 0.0125,
    },
    realizedPnl: 0,
    fees: 0,
    trades: [],
    recovery: {
      version: 1,
      policy: 'closed-1m-directional-v1',
      checkpointAt: gapStartedAt,
      gapStartedAt,
      gapReason: 'background',
      gapSequence: 1,
      lastRecoveryAt: null,
      lastRecoveryStatus: 'pending',
      symbols: {
        BTCUSDT: recoverySymbol(),
        ETHUSDT: recoverySymbol(),
        SOLUSDT: recoverySymbol(),
      },
    },
    symbols: {
      BTCUSDT: {
        pattern: { patternId: campaign.patternId, source: 'manual', vLow: 100, status: 'trading' },
        campaign,
      },
      ETHUSDT: { pattern: null, campaign: null },
      SOLUSDT: { pattern: null, campaign: null },
    },
  },
  training: { manualExamples: [], radarLabels: [], replayExamples: [] },
  activity: [],
};

function kline(openTime, { open, high, low, close }, intervalMs = 60_000) {
  return [
    openTime,
    String(open),
    String(high),
    String(low),
    String(close),
    '100',
    openTime + intervalMs - 1,
  ];
}

const recoveryRows = [
  kline(firstMinute, { open: 100, high: 105, low: 95, close: 100.1 }),
  kline(firstMinute + 60_000, { open: 100.1, high: 100.2, low: 99.4, close: 99.6 }),
  kline(firstMinute + 120_000, { open: 99.6, high: 100.4, low: 99.5, close: 100.3 }),
  kline(firstMinute + 180_000, { open: 101.5, high: 102, low: 100.5, close: 100.8 }),
];
const portfolioRows = [
  kline(firstMinute, { open: 100, high: 101, low: 99, close: 100 }),
  kline(firstMinute + 60_000, { open: 100, high: 101, low: 80, close: 100 }),
  kline(firstMinute + 120_000, { open: 100, high: 101, low: 99, close: 100 }),
  kline(firstMinute + 180_000, { open: 100, high: 101, low: 99, close: 100 }),
];
const chartRows = Array.from({ length: 240 }, (_, index) => {
  const openTime = currentMinute - (240 - index) * 900_000;
  const open = 100 + Math.sin(index / 12) * 0.4;
  const close = open + Math.sin(index) * 0.08;
  return kline(openTime, {
    open,
    high: Math.max(open, close) + 0.1,
    low: Math.min(open, close) - 0.1,
    close,
  }, 900_000);
});

const fakeCharts = `
(() => {
  const timeScale = () => ({
    subscribeVisibleTimeRangeChange() {}, subscribeVisibleLogicalRangeChange() {},
    setVisibleLogicalRange() {}, setVisibleRange() {}, fitContent() {}, scrollToRealTime() {},
    scrollToPosition() {}, getVisibleLogicalRange() { return { from: 0, to: 239 }; },
    coordinateToTime() { return Math.floor(Date.now() / 1000); }, timeToCoordinate() { return 120; },
  });
  const series = () => ({
    setData() {}, update() {}, priceToCoordinate(value) { return Number(value); },
    coordinateToPrice(value) { return Number(value); }, createPriceLine(options) { return options; },
    removePriceLine() {},
  });
  window.LightweightCharts = {
    CrosshairMode: { Normal: 0 }, PriceScaleMode: { Normal: 0, Logarithmic: 1, Percentage: 2, IndexedTo100: 3 },
    LineStyle: { Solid: 0, Dashed: 2 }, CandlestickSeries: 'candles', BarSeries: 'bars',
    LineSeries: 'line', AreaSeries: 'area', BaselineSeries: 'baseline', HistogramSeries: 'histogram',
    createChart() { const scale = timeScale(); return { addSeries() { return series(); }, removeSeries() {},
      subscribeCrosshairMove() {}, subscribeClick() {}, timeScale() { return scale; }, applyOptions() {},
      priceScale() { return { applyOptions() {} }; }, takeScreenshot() { return document.createElement('canvas'); } }; },
    createSeriesMarkers() { return { setMarkers() {} }; },
  };
})();`;

let browser;
try {
  await waitForServer();
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'ru-RU' });
  await context.addInitScript(({ snapshot }) => {
    if (!localStorage.getItem('galka-pro-v1')) {
      localStorage.setItem('galka-pro-v1', JSON.stringify(snapshot));
    }
    class RecoveryWebSocket {
      constructor() {
        this.readyState = 0;
        setTimeout(() => {
          this.readyState = 1;
          this.onopen?.();
          for (const [symbol, bid] of [['BTCUSDT', 100.9], ['ETHUSDT', 3_000], ['SOLUSDT', 150]]) {
            this.onmessage?.({ data: JSON.stringify({ data: { e: 'bookTicker', E: Date.now(), s: symbol, b: String(bid), a: String(bid + 0.01), u: 1 } }) });
          }
        }, 20);
      }
      close() { this.readyState = 3; }
    }
    window.WebSocket = RecoveryWebSocket;
  }, { snapshot: seed });

  let recoveryRequests = 0;
  let externalChartRequests = 0;
  await context.route('https://unpkg.com/**', (route) => {
    externalChartRequests += 1;
    return route.fulfill({ status: 200, contentType: 'application/javascript', body: fakeCharts });
  });
  await context.route('https://fapi.binance.com/**', async (route) => {
    const url = new URL(route.request().url());
    const isRecovery = url.searchParams.get('interval') === '1m' && url.searchParams.has('startTime');
    if (isRecovery) recoveryRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'access-control-allow-origin': '*', 'cache-control': 'no-store' },
      body: JSON.stringify(isRecovery ? recoveryRows : chartRows),
    });
  });

  const page = await context.newPage();
  const runtimeErrors = [];
  page.on('pageerror', (error) => runtimeErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') runtimeErrors.push(message.text());
  });
  await page.goto(`${BASE_URL}/terminal/pro.html`, { waitUntil: 'domcontentloaded', timeout: 10_000 });
  await waitForPageCondition(page, () => {
    const state = JSON.parse(localStorage.getItem('galka-pro-v1'));
    return state?.paper?.trades?.length === 1 && state?.paper?.symbols?.BTCUSDT?.campaign == null;
  });

  const recovered = await page.evaluate(() => ({
    state: JSON.parse(localStorage.getItem('galka-pro-v1')),
    note: document.querySelector('#sessionRecovery')?.textContent,
    simpleTradeBar: !!document.querySelector('#simpleTradeBar'),
  }));
  assert.equal(recovered.state.paper.trades.length, 1);
  assert.equal(recovered.state.paper.trades[0].executionSource, 'recovery');
  assert.equal(recovered.state.paper.trades[0].levelsFilled, 4);
  assert.equal(recovered.state.paper.recovery.gapStartedAt, null);
  assert.match(recovered.note, /Восстановлено 3 закрытых 1m свечей/);
  assert.equal(recovered.simpleTradeBar, true, 'the final UI must exist on the first clean page load');
  assert.equal(externalChartRequests, 0, 'the production page must use the vendored chart runtime');
  assert.equal(recoveryRequests, 1);

  await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    if (!registration.active) throw new Error('service worker did not become active');
  });
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 10_000 });
  await waitForPageCondition(page, () => !!navigator.serviceWorker?.controller);
  await page.waitForTimeout(300);
  const afterReload = await page.evaluate(() => JSON.parse(localStorage.getItem('galka-pro-v1')));
  assert.equal(afterReload.paper.trades.length, 1, 'reloading after recovery must not duplicate a trade');
  assert.equal(recoveryRequests, 1, 'a cleared durable cursor must not replay the same range again');
  assert.deepEqual(runtimeErrors, []);
  await context.close();

  const filledCampaign = (symbol) => ({
    ...structuredClone(campaign),
    campaignId: `C-${symbol}-portfolio-recovery`,
    symbol,
    patternId: `M-${symbol}-portfolio-recovery`,
    source: 'auto',
    status: 'open',
    exitMode: 'trail',
    reclaimPrice: 110,
    levels: [{
      index: 1, depthPct: 0, weight: 1, price: 100, notional: 400,
      status: 'filled', fillPrice: 100, fillTime: new Date(gapStartedAt - 20_000).toISOString(),
      qty: 4, fee: 0.08,
    }],
    qty: 4,
    filledNotional: 400,
    averageEntry: 100,
    entryFees: 0.08,
  });
  const portfolioSeed = structuredClone(seed);
  portfolioSeed.paper.settings.startingBalance = 100;
  portfolioSeed.paper.symbols.BTCUSDT = {
    pattern: { patternId: 'M-BTCUSDT-portfolio-recovery', source: 'auto', vLow: 100, status: 'trading' },
    campaign: filledCampaign('BTCUSDT'),
  };
  portfolioSeed.paper.symbols.ETHUSDT = {
    pattern: { patternId: 'M-ETHUSDT-portfolio-recovery', source: 'auto', vLow: 100, status: 'trading' },
    campaign: filledCampaign('ETHUSDT'),
  };
  const portfolioContext = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'ru-RU' });
  await portfolioContext.addInitScript(({ snapshot }) => {
    localStorage.setItem('galka-pro-v1', JSON.stringify(snapshot));
    class PortfolioRecoveryWebSocket {
      constructor() {
        this.readyState = 0;
        setTimeout(() => {
          this.readyState = 1;
          this.onopen?.();
          for (const symbol of ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']) {
            this.onmessage?.({ data: JSON.stringify({ data: { e: 'bookTicker', E: Date.now(), s: symbol, b: '100', a: '100.01', u: 1 } }) });
          }
        }, 20);
      }
      close() { this.readyState = 3; }
    }
    window.WebSocket = PortfolioRecoveryWebSocket;
  }, { snapshot: portfolioSeed });
  let portfolioRecoveryRequests = 0;
  await portfolioContext.route('https://fapi.binance.com/**', async (route) => {
    const url = new URL(route.request().url());
    const isRecovery = url.searchParams.get('interval') === '1m' && url.searchParams.has('startTime');
    if (isRecovery) portfolioRecoveryRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'access-control-allow-origin': '*', 'cache-control': 'no-store' },
      body: JSON.stringify(isRecovery ? portfolioRows : chartRows),
    });
  });
  const portfolioPage = await portfolioContext.newPage();
  const portfolioErrors = [];
  portfolioPage.on('pageerror', (error) => portfolioErrors.push(error.message));
  await portfolioPage.goto(`${BASE_URL}/terminal/pro.html`, { waitUntil: 'domcontentloaded', timeout: 10_000 });
  try {
    await waitForPageCondition(portfolioPage, () => {
      const state = JSON.parse(localStorage.getItem('galka-pro-v1'));
      return state?.paper?.trades?.length === 2
        && state.paper.symbols.BTCUSDT.campaign == null
        && state.paper.symbols.ETHUSDT.campaign == null;
    });
  } catch (error) {
    const diagnostic = await portfolioPage.evaluate(() => {
      const state = JSON.parse(localStorage.getItem('galka-pro-v1'));
      return { trades: state?.paper?.trades, recovery: state?.paper?.recovery, activity: state?.activity?.slice(-5) };
    });
    throw new Error(`Portfolio recovery did not finish: ${JSON.stringify({ diagnostic, portfolioErrors })}`, { cause: error });
  }
  const portfolioState = await portfolioPage.evaluate(() => JSON.parse(localStorage.getItem('galka-pro-v1')));
  assert.equal(portfolioRecoveryRequests, 2, 'both active symbols require a closed-minute range');
  assert.deepEqual(portfolioState.paper.trades.map((trade) => trade.reason), ['paper_liquidation', 'paper_liquidation']);
  assert.ok(portfolioState.paper.trades.every((trade) => trade.executionSource === 'recovery'));
  assert.equal(new Set(portfolioState.paper.trades.map((trade) => trade.exitTime)).size, 1, 'portfolio positions liquidate at one synchronized replay timestamp');
  assert.deepEqual(portfolioErrors, []);
  await portfolioContext.close();
} finally {
  await browser?.close();
  server.kill('SIGTERM');
}

console.log('Paper recovery browser: durable replay, duplicate guard and synchronized portfolio liquidation passed');
