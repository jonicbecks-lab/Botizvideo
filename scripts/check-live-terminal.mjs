import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const html = fs.readFileSync('terminal/live.html', 'utf8');
const css = fs.readFileSync('terminal/live.css', 'utf8');
const chartCss = fs.readFileSync('terminal/live-chart.css', 'utf8');
const js = fs.readFileSync('terminal/live.js', 'utf8');
const proCss = fs.readFileSync('terminal/pro.css', 'utf8');
const memHtml = fs.readFileSync('terminal/mem.html', 'utf8');
const memCss = fs.readFileSync('terminal/mem.css', 'utf8');
const memJs = fs.readFileSync('terminal/mem.js', 'utf8');
const memChartJs = fs.readFileSync('terminal/mem-chart.js', 'utf8');
const setup = fs.readFileSync('scripts/setup-galka-live.sh', 'utf8');
const launcher = fs.readFileSync('scripts/start-galka-live.sh', 'utf8');
const ladder = fs.readFileSync('live/live_ladder.py', 'utf8');
const gateway = fs.readFileSync('live/hyperliquid_gateway.py', 'utf8');
const server = fs.readFileSync('live/server.py', 'utf8');
const chartShim = fs.readFileSync('terminal/vendor/galka-chart.js', 'utf8');

const checks = [
  ['Hyperliquid title', html.includes('Hyperliquid LIVE') || html.includes('HYPERLIQUID')],
  ['BTC selector', html.includes('<option>BTC</option>')],
  ['ETH selector', html.includes('<option>ETH</option>')],
  ['SOL selector', html.includes('<option>SOL</option>')],
  ['manual GALKA input', html.includes('id="galkaInput"')],
  ['real preview modal', html.includes('id="previewModal"') && html.includes('РЕАЛЬНЫЕ ОРДЕРА')],
  ['eight live depths', ladder.includes('0.15, 0.30, 0.45, 0.60, 0.90, 1.20, 1.50, 2.00')],
  ['small-account minimum adjustment', ladder.includes('_allocate_targets') && ladder.includes('MIN_ORDER_NOTIONAL')],
  ['ALO entries', gateway.includes('"tif": "Alo"')],
  ['exchange-native TP grouping', gateway.includes('grouping="normalTpsl"')],
  ['non-market TP', gateway.includes('"isMarket": False') && gateway.includes('"tpsl": "tp"')],
  ['reduce-only target', gateway.includes('"reduce_only": True')],
  ['local API', js.includes('/api/live/preview') && js.includes('/api/live/campaign')],
  ['session-bound API', js.includes('X-Galka-Session') && server.includes('X-Galka-Session')],
  ['manual reconciliation', js.includes('/api/live/reconcile') && server.includes('/api/live/reconcile')],
  ['local chart dependency', html.includes('vendor/galka-chart.js') && html.includes('live-chart.css') && chartShim.includes('LightweightCharts')],
  ['strict chart CSP', html.includes("style-src 'self'") && !html.includes("style-src 'self' 'unsafe-inline'") && chartCss.includes('.galka-live-canvas') && !chartShim.includes('.style.')],
  ['no runtime CDN', !/https?:\/\//.test(html)],
  ['explicit real confirmation', js.includes('PLACE_REAL_ORDERS')],
  ['double-confirmed emergency', js.includes('EMERGENCY_CLOSE_REAL_POSITION')],
  ['no browser secret', !/HL_API_SECRET_KEY|api_secret_key|PASTE_API_WALLET_PRIVATE_KEY/.test(html + css + js + memHtml + memCss + memJs + memChartJs)],
  ['private Termux config', setup.includes('chmod 600') && setup.includes('$HOME/.config') && setup.includes('galka-live.env')],
  ['live launcher', launcher.includes('Galka LIVE URL:') && launcher.includes('termux-open-url')],
  ['launcher hides session token', launcher.includes("sed '/^Galka LIVE URL: /d'")],
  ['mobile layout', css.includes('.tradebar') && css.includes('100dvh')],

  ['MEM reuses exact Pro stylesheet', memHtml.includes('href="pro.css?v=7"') && proCss.includes('.topbar') && proCss.includes('.leftbar') && proCss.includes('.mobile-nav')],
  ['MEM Pro topbar structure', memHtml.includes('class="topbar"') && memHtml.includes('class="brand-mark"') && memHtml.includes('class="market-controls"') && memHtml.includes('class="top-actions"')],
  ['MEM Pro chart stack', memHtml.includes('id="mainChart"') && memHtml.includes('id="drawingCanvas"') && memHtml.includes('class="chart-main-wrap"') && memHtml.includes('class="chart-actions"')],
  ['MEM Pro drawing rail', memHtml.includes('class="leftbar"') && memHtml.includes('data-tool="cursor"') && memHtml.includes('data-tool="crosshair"') && memHtml.includes('data-tool="manualGalka"')],
  ['MEM Pro bottom sheet', memHtml.includes('class="sidebar"') && memHtml.includes('class="sheet-head"') && memHtml.includes('class="side-tabs"') && memHtml.includes('class="mobile-nav"')],
  ['MEM chart workflow', memHtml.includes('id="newGalka"') && memHtml.includes('id="chartAddUpper"') && memHtml.includes('id="chartDone"')],
  ['MEM chart candle endpoint', server.includes('/api/mem/candles') && memChartJs.includes('/api/mem/candles')],
  ['MEM chart session auth', memChartJs.includes('X-Galka-Session')],
  ['MEM Galka Pro touch gestures', memChartJs.includes('horzTouchDrag: true') && memChartJs.includes('vertTouchDrag: true') && memChartJs.includes('pinch: true')],
  ['MEM canvas isolated from pan zoom', memCss.includes('#drawingCanvas{z-index:8;pointer-events:none}') && memCss.includes('#drawingCanvas.drawing{pointer-events:auto;touch-action:none')],
  ['MEM anchor-left-right-upper sequence', memChartJs.includes("runtime.stage = 'anchor'") && memChartJs.includes("runtime.stage = 'left'") && memChartJs.includes("runtime.stage = 'right'") && memChartJs.includes("runtime.stage = 'upper'")],
  ['MEM chronology gate', memChartJs.includes("point.time >= runtime.anchor.time") && memChartJs.includes("point.time <= runtime.anchor.time")],
  ['MEM GALKA V shape', memChartJs.includes("drawHandle(ctx, anchor, COLORS.galka, 'G')") && memChartJs.includes('ctx.lineTo(anchor.x, anchor.y)') && memChartJs.includes('ctx.lineTo(right.x, right.y)')],
  ['MEM automatic lower ladder', memChartJs.includes('[0.98, 0.96, 0.94, 0.92]') && memChartJs.includes("els.preview?.click()")],
  ['MEM full toolbar drawing support', memChartJs.includes("DRAW_TWO = new Set(['trend', 'ray', 'rect', 'measure', 'fib', 'longPosition'])") && memChartJs.includes("runtime.magnet") && memChartJs.includes("runtime.undoStack")],
];

for (const [name, ok] of checks) {
  if (!ok) throw new Error(`Live terminal check failed: ${name}`);
}
execFileSync(process.execPath, ['--check', 'terminal/live.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/mem.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/mem-chart.js'], { stdio: 'inherit' });
console.log(`Hyperliquid live terminal: ${checks.length} checks passed`);
