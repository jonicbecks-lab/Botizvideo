import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const html = fs.readFileSync('terminal/live.html', 'utf8');
const css = fs.readFileSync('terminal/live.css', 'utf8');
const chartCss = fs.readFileSync('terminal/live-chart.css', 'utf8');
const js = fs.readFileSync('terminal/live.js', 'utf8');
const memHtml = fs.readFileSync('terminal/mem.html', 'utf8');
const memCss = fs.readFileSync('terminal/mem.css', 'utf8');
const memLive = fs.readFileSync('terminal/mem-live.js', 'utf8');
const memProtectionUi = fs.readFileSync('terminal/mem-protection.js', 'utf8');
const memProtectionHook = fs.readFileSync('terminal/vendor/mem-protection-chart-hook.js', 'utf8');
const memProtectionBackend = fs.readFileSync('live/mem_protection.py', 'utf8');
const memQuickBackend = fs.readFileSync('live/mem_quick_controls.py', 'utf8');
const memSourceChart = fs.readFileSync('terminal/vendor/mem-source-galka-chart.js', 'utf8');
const memSourceTouch = fs.readFileSync('terminal/vendor/mem-source-galka-touch-actions.js', 'utf8');
const memSourceRelativeDrag = fs.readFileSync('terminal/vendor/mem-source-galka-structure-relative-drag.js', 'utf8');
const memSourceStructure = fs.readFileSync('terminal/vendor/mem-source-galka-structure-draft.js', 'utf8');
const memSourceChartCss = fs.readFileSync('terminal/mem-source-live-chart.css', 'utf8');
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
  ['strict chart CSP', html.includes("style-src 'self'") && !html.includes("style-src 'self' 'unsafe-inline'") && chartCss.includes('.galka-live-canvas')],
  ['no runtime CDN', !/https?:\/\//.test(html)],
  ['explicit real confirmation', js.includes('PLACE_REAL_ORDERS')],
  ['double-confirmed emergency', js.includes('EMERGENCY_CLOSE_REAL_POSITION')],
  ['private Termux config', setup.includes('chmod 600') && setup.includes('$HOME/.config') && setup.includes('galka-live.env')],
  ['live launcher', launcher.includes('Galka LIVE URL:') && launcher.includes('termux-open-url')],
  ['mobile layout', css.includes('.tradebar') && css.includes('100dvh')],

  ['MEM uses source LIVE shell not Pro', memHtml.includes('href="live.css?v=2"') && !memHtml.includes('pro.css') && memHtml.includes('class="app-shell"') && memHtml.includes('class="tradebar"')],
  ['MEM source topbar ids', memHtml.includes('id="symbolSelect"') && memHtml.includes('id="intervalSelect"') && memHtml.includes('id="ticker"') && memHtml.includes('id="liveBadge"')],
  ['MEM source chart workspace', memHtml.includes('<main class="workspace">') && memHtml.includes('id="chart"') && memHtml.includes('id="crosshairGalkaAction"') && memHtml.includes('id="detailsButton"')],
  ['MEM source tradebar', memHtml.includes('id="campaignStatus"') && memHtml.includes('id="galkaInput"') && memHtml.includes('id="previewButton"')],
  ['MEM custom source chart', memHtml.includes('mem-source-galka-chart.js') && memSourceChart.includes('galka-live-canvas') && memSourceChartCss.includes('.galka-touch-overlay')],
  ['MEM source touch behavior', memSourceTouch.includes('HOLD_MS=650') && memSourceTouch.includes("type:'crosshair'") && memSourceTouch.includes('galka:select-price') && memSourceTouch.includes('startPinch')],
  ['MEM exact V3 anchor-left-right workflow', memSourceStructure.includes("state.phase='choose-anchor'") && memSourceStructure.includes("state.phase='choose-left'") && memSourceStructure.includes("state.phase='choose-right'") && memSourceStructure.includes("selectionMethod:'manual_crosshair_structure_v3'")],
  ['MEM relative boundary drag', memHtml.includes('mem-source-galka-structure-relative-drag.js') && memSourceRelativeDrag.includes('originX') && memSourceRelativeDrag.includes('dispatchSynthetic')],
  ['MEM structure hands off instead of trading', memSourceStructure.includes('galka:mem-structure-ready') && !memSourceStructure.includes('/api/live/campaign') && !memSourceStructure.includes('PLACE_REAL_ORDERS')],
  ['MEM only uses MEM endpoints', memLive.includes('/api/mem/candles') && memLive.includes('/api/mem/status') && memLive.includes('/api/mem/preview') && memLive.includes('/api/mem/campaign')],
  ['MEM explicit real confirmation isolated', memLive.includes('PLACE_GALKA_MEM_REAL_ORDERS') && !memLive.includes("confirmation:'PLACE_REAL_ORDERS'") && !memLive.includes('/api/live/campaign')],
  ['MEM upper crosshair then automatic lower', memLive.includes('handleSelectedCrosshairPrice') && memLive.includes('[.98,.96,.94,.92]')],
  ['MEM browser has no secret', !/HL_API_SECRET_KEY|api_secret_key|PASTE_API_WALLET_PRIVATE_KEY/.test(memHtml + memCss + memLive + memProtectionUi + memSourceStructure)],
  ['MEM quick chart risk dock', memHtml.includes('id="quickRiskDock"') && memHtml.includes('id="quickBe"') && memHtml.includes('id="quickSl"') && memHtml.includes('id="quickTp"') && memHtml.includes('id="quickApply"')],
  ['MEM one-tap break-even', memProtectionUi.includes('ACTIVATE_GALKA_MEM_BREAK_EVEN') && memProtectionUi.includes("els.be?.addEventListener('click'")],
  ['MEM break-even uses reduce-only stop-market backend', memProtectionBackend.includes('"isMarket": True') && memProtectionBackend.includes('"tpsl": "sl"') && memProtectionBackend.includes('reduce_only=True')],
  ['MEM protection only moves upward', memProtectionBackend.includes('Protection can only move upward') && memProtectionUi.includes('MOVE_GALKA_MEM_PROTECTION:')],
  ['MEM manual TP persists', memQuickBackend.includes('manualUpperTakeProfit') && memQuickBackend.includes('MOVE_GALKA_MEM_TP:') && memQuickBackend.includes('TP')],
  ['MEM TP must remain above market', memQuickBackend.includes('must remain above current market')],
  ['MEM TP SL lines visible', memProtectionUi.includes("'TP'") && memProtectionUi.includes("'SL'") && memProtectionHook.includes('window.GalkaMemChart')],
];

for (const [name, ok] of checks) {
  if (!ok) throw new Error(`Live terminal check failed: ${name}`);
}
for (const file of [
  'terminal/live.js',
  'terminal/mem-live.js',
  'terminal/mem-protection.js',
  'terminal/vendor/mem-protection-chart-hook.js',
  'terminal/vendor/mem-source-galka-chart.js',
  'terminal/vendor/mem-source-galka-future-pan.js',
  'terminal/vendor/mem-source-galka-native-plot-pan.js',
  'terminal/vendor/mem-source-galka-touch-actions.js',
  'terminal/vendor/mem-source-galka-structure-relative-drag.js',
  'terminal/vendor/mem-source-galka-structure-draft.js',
]) {
  execFileSync(process.execPath, ['--check', file], { stdio: 'inherit' });
}
console.log(`Hyperliquid live terminal: ${checks.length} checks passed`);
