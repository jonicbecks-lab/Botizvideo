import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const html = fs.readFileSync('terminal/live.html', 'utf8');
const css = fs.readFileSync('terminal/live.css', 'utf8');
const chartCss = fs.readFileSync('terminal/live-chart.css', 'utf8');
const js = fs.readFileSync('terminal/live.js', 'utf8');
const setup = fs.readFileSync('scripts/setup-galka-live.sh', 'utf8');
const launcher = fs.readFileSync('scripts/start-galka-live.sh', 'utf8');
const testProfile = fs.readFileSync('scripts/galka-live-10-usd-test.sh', 'utf8');
const testLauncher = fs.readFileSync('scripts/start-galka-10-usd-live-test.sh', 'utf8');
const ladder = fs.readFileSync('live/live_ladder.py', 'utf8');
const gateway = fs.readFileSync('live/hyperliquid_gateway.py', 'utf8');
const server = fs.readFileSync('live/server.py', 'utf8');
const chartShim = fs.readFileSync('terminal/vendor/galka-chart.js', 'utf8');
const dexActions = fs.readFileSync('terminal/vendor/galka-dex-actions.js', 'utf8');
const touchActions = fs.readFileSync('terminal/vendor/galka-touch-actions.js', 'utf8');
const startupDefaults = fs.readFileSync('terminal/vendor/galka-startup-defaults.js', 'utf8');

const checks = [
  ['Hyperliquid title', html.includes('Hyperliquid LIVE') || html.includes('HYPERLIQUID')],
  ['BTC selector', html.includes('<option>BTC</option>')],
  ['ETH selector', html.includes('<option>ETH</option>')],
  ['SOL selector', html.includes('<option>SOL</option>')],
  ['five minute startup', html.includes('<option selected>5m</option>') && html.includes('BTC · 5m · HYPERLIQUID') && html.includes('galka-startup-defaults.js?v=2') && startupDefaults.includes("symbol.value = 'BTC'") && startupDefaults.includes("interval.value = '5m'") && startupDefaults.includes('await interval.onchange()')],
  ['manual GALKA input', html.includes('id="galkaInput"')],
  ['real preview modal', html.includes('id="previewModal"') && html.includes('РЕАЛЬНЫЕ ОРДЕРА')],
  ['eight live depths', ladder.includes('0.15, 0.30, 0.45, 0.60, 0.90, 1.20, 1.50, 2.00')],
  ['small-account minimum adjustment', ladder.includes('_allocate_targets') && ladder.includes('MIN_ORDER_NOTIONAL')],
  ['guarded ten dollar profile', testProfile.includes('HL_LEVERAGE') && testProfile.includes('HL_TOTAL_NOTIONAL') && testProfile.includes('ENABLE_10_USD_LIVE_TEST') && testProfile.includes('config.total_notional - 100.0') && testProfile.includes('config.leverage != 10')],
  ['test profile keeps explicit LIVE confirmation', testProfile.includes('I_UNDERSTAND_REAL_MONEY') && testProfile.includes('--enable') && testProfile.includes('--disable')],
  ['one-command ten dollar launcher', testLauncher.includes('galka-live-10-usd-test.sh --enable') && testLauncher.includes('config.total_notional - 100.0') && testLauncher.includes('exec bash scripts/start-galka-live.sh')],
  ['ALO entries', gateway.includes('"tif": "Alo"')],
  ['exchange-native TP grouping', gateway.includes('grouping="normalTpsl"')],
  ['non-market TP', gateway.includes('"isMarket": False') && gateway.includes('"tpsl": "tp"')],
  ['reduce-only target', gateway.includes('"reduce_only": True')],
  ['local API', js.includes('/api/live/preview') && js.includes('/api/live/campaign')],
  ['session-bound API', js.includes('X-Galka-Session') && server.includes('X-Galka-Session')],
  ['manual reconciliation', js.includes('/api/live/reconcile') && server.includes('/api/live/reconcile')],
  ['local chart dependency', html.includes('vendor/galka-chart.js?v=3') && html.includes('live-chart.css?v=3') && chartShim.includes('LightweightCharts')],
  ['DeX action dependency', html.includes('vendor/galka-dex-actions.js?v=2') && dexActions.includes('installDesktopActions')],
  ['touch action dependency', html.includes('vendor/galka-touch-actions.js?v=1') && touchActions.includes('installTouchActions')],
  ['strict chart CSP', html.includes("style-src 'self'") && !html.includes("style-src 'self' 'unsafe-inline'") && chartCss.includes('.galka-live-canvas') && !chartShim.includes('.style.') && !touchActions.includes('.style.')],
  ['pointer pan controls', chartShim.includes("addEventListener('pointerdown'") && chartShim.includes('setPointerCapture') && chartShim.includes("type: 'pan'")],
  ['grab-style pan direction', chartShim.includes('const draggedBars = deltaX / geometry.plotWidth') && chartShim.includes('this.gesture.startOffset + draggedBars')],
  ['pinch zoom controls', chartShim.includes('activePointers') && chartShim.includes('startPinch') && chartShim.includes('updatePinch')],
  ['wheel and trackpad zoom', chartShim.includes("addEventListener('wheel'") && chartShim.includes('zoomTime') && chartShim.includes('event.deltaX')],
  ['price-axis scaling', chartShim.includes('startPriceScale') && chartShim.includes('updatePriceScale') && chartShim.includes('manualPriceRange') && chartCss.includes('ns-resize')],
  ['time-axis scaling', chartShim.includes('startTimeScale') && chartShim.includes('updateTimeScale') && chartCss.includes('ew-resize')],
  ['axis double-click reset', chartShim.includes("addEventListener('dblclick'") && chartShim.includes('resetPriceScale') && chartShim.includes('fitContent')],
  ['precise chart crosshair', chartShim.includes('drawCrosshair') && chartShim.includes('setCrosshair') && chartShim.includes('priceLabel') && chartShim.includes('timeWidth')],
  ['crosshair does not replace desktop pan', chartShim.includes("this.gesture.type === 'pan'") && chartShim.includes('this.setCrosshair(point)')],
  ['TradingView-style touch pan and hold', touchActions.includes('HOLD_MS = 450') && touchActions.includes("type: 'pending'") && touchActions.includes("gesture.type = 'pan'") && touchActions.includes("type: 'crosshair'")],
  ['persistent touch crosshair', touchActions.includes('crosshairPinned') && touchActions.includes('DOUBLE_TAP_MS') && touchActions.includes('activatedByHold') && touchActions.includes('onPointerLeave')],
  ['touch GALKA plus', chartCss.includes('.galka-touch-plus') && touchActions.includes("new CustomEvent('galka:select-price'") && dexActions.includes("addEventListener('galka:select-price'")],
  ['safe right-click GALKA draft', dexActions.includes("addEventListener('contextmenu'") && dexActions.includes('/api/live/preview') && dexActions.includes('preview.levels')],
  ['right-click remains desktop-only', dexActions.includes("state.lastPointerType !== 'mouse'")],
  ['browser price actions cannot place orders', !dexActions.includes('/api/live/campaign') && !dexActions.includes('PLACE_REAL_ORDERS') && !touchActions.includes('/api/live/campaign') && !touchActions.includes('PLACE_REAL_ORDERS')],
  ['touch-safe chart surface', chartCss.includes('touch-action: none') && chartCss.includes('overscroll-behavior: contain')],
  ['no runtime CDN', !/https?:\/\//.test(html)],
  ['explicit real confirmation', js.includes('PLACE_REAL_ORDERS')],
  ['double-confirmed emergency', js.includes('EMERGENCY_CLOSE_REAL_POSITION')],
  ['no browser secret', !/HL_API_SECRET_KEY|api_secret_key|PASTE_API_WALLET_PRIVATE_KEY/.test(html + css + js + dexActions + touchActions + startupDefaults)],
  ['private Termux config', setup.includes('chmod 600') && setup.includes('$HOME/.config') && setup.includes('galka-live.env')],
  ['live launcher', launcher.includes('Galka LIVE URL:') && launcher.includes('termux-open-url')],
  ['launcher hides session token', launcher.includes("sed '/^Galka LIVE URL: /d'")],
  ['mobile layout', css.includes('.tradebar') && css.includes('100dvh')],
];

for (const [name, ok] of checks) {
  if (!ok) throw new Error(`Live terminal check failed: ${name}`);
}
execFileSync(process.execPath, ['--check', 'terminal/live.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-chart.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-dex-actions.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-touch-actions.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-startup-defaults.js'], { stdio: 'inherit' });
console.log(`Hyperliquid live terminal: ${checks.length} checks passed`);
