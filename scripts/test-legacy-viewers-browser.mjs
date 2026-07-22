import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { chromium } from 'playwright';

const PORT = 4177;
const BASE_URL = `http://127.0.0.1:${PORT}`;
const server = spawn(
  'python3',
  ['-m', 'http.server', String(PORT), '--bind', '127.0.0.1', '--directory', '.'],
  { stdio: 'ignore' },
);

async function waitForServer() {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      if ((await fetch(`${BASE_URL}/src/index.html`)).ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('Legacy viewer test server did not start');
}

const hostilePackage = {
  meta: {
    symbol: '<img src=x onerror="globalThis.compromised=true">',
    timeframe: '1m',
    model_id: '<svg onload="globalThis.compromised=true">',
  },
  summary: { trades: 1, win_rate: 1, net_pnl: 1, max_drawdown: 0 },
  candles: [
    { time: '2026-07-20T00:00:00Z', open: 100, high: 101, low: 99, close: 100.5 },
    { time: '2026-07-20T00:01:00Z', open: 100.5, high: 102, low: 100, close: 101 },
  ],
  trades: [{
    id: '<img src=x onerror="globalThis.compromised=true">',
    status: 'closed',
    v_low: 100,
    v_low_time: '2026-07-20T00:00:00Z',
    break_time: '2026-07-20T00:00:00Z',
    exit_time: '2026-07-20T00:01:00Z',
    exit_price: 101,
    pnl: 1,
    fills: [],
  }],
};

let browser;
try {
  await waitForServer();
  browser = await chromium.launch({ headless: true });

  const sourceContext = await browser.newContext({ locale: 'en-US' });
  const sourcePage = await sourceContext.newPage();
  const sourceErrors = [];
  const sourceExternal = [];
  sourcePage.on('pageerror', (error) => sourceErrors.push(error.message));
  sourcePage.on('request', (request) => {
    if (!request.url().startsWith(BASE_URL)) sourceExternal.push(request.url());
  });
  await sourcePage.addInitScript(() => { globalThis.compromised = false; });
  await sourcePage.goto(`${BASE_URL}/src/index.html`, { waitUntil: 'domcontentloaded' });
  await sourcePage.setInputFiles('#fileInput', {
    name: 'hostile.galka.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(hostilePackage)),
  });
  await sourcePage.locator('#tradeSelect option').waitFor();
  const sourceState = await sourcePage.evaluate(() => ({
    compromised: globalThis.compromised,
    summary: document.querySelector('#summary')?.textContent,
    details: document.querySelector('#tradeDetails')?.textContent,
  }));
  assert.equal(sourceState.compromised, false);
  assert.match(sourceState.summary, /<img src=x/);
  assert.match(sourceState.details, /<img src=x/);
  assert.deepEqual(sourceExternal, []);
  assert.deepEqual(sourceErrors, []);
  await sourceContext.close();

  const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'ru-RU' });
  const mobilePage = await mobileContext.newPage();
  const mobileErrors = [];
  const mobileExternal = [];
  mobilePage.on('pageerror', (error) => mobileErrors.push(error.message));
  mobilePage.on('request', (request) => {
    if (!request.url().startsWith(BASE_URL)) mobileExternal.push(request.url());
  });
  await mobilePage.goto(`${BASE_URL}/terminal/index.html`, { waitUntil: 'domcontentloaded' });
  await mobilePage.locator('#status.ok').waitFor({ timeout: 30_000 });
  const mobileState = await mobilePage.evaluate(() => ({
    status: document.querySelector('#status')?.textContent,
    metrics: document.querySelector('#metrics')?.textContent,
  }));
  assert.match(mobileState.status, /Загружено/);
  assert.match(mobileState.metrics, /BTCUSDT/);
  assert.deepEqual(mobileExternal, []);
  assert.deepEqual(mobileErrors, []);
  await mobileContext.close();
} finally {
  await browser?.close();
  server.kill('SIGTERM');
}

console.log('Legacy viewer browser: offline load, hostile-text isolation and bundled history passed');
