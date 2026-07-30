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
const touchLabHtml = fs.readFileSync('terminal/touch-lab.html', 'utf8');
const touchLabCss = fs.readFileSync('terminal/touch-lab.css', 'utf8');
const touchLabJs = fs.readFileSync('terminal/touch-lab.js', 'utf8');
const touchLabLegacyLauncher = fs.readFileSync('scripts/start-galka-touch-lab.sh', 'utf8');
const touchLabServer = fs.readFileSync('scripts/galka-touch-lab-server.py', 'utf8');
const touchLabCommon = fs.readFileSync('scripts/galka-touch-lab-common.sh', 'utf8');
const touchLabStart = fs.readFileSync('scripts/galka-touch-lab-start.sh', 'utf8');
const touchLabOpen = fs.readFileSync('scripts/galka-touch-lab-open.sh', 'utf8');
const touchLabStatus = fs.readFileSync('scripts/galka-touch-lab-status.sh', 'utf8');
const touchLabStop = fs.readFileSync('scripts/galka-touch-lab-stop.sh', 'utf8');
const touchLabBundle = [touchLabHtml, touchLabCss, touchLabJs, touchLabLegacyLauncher, touchLabServer, touchLabCommon, touchLabStart, touchLabOpen, touchLabStatus, touchLabStop].join('\n');
const ladder = fs.readFileSync('live/live_ladder.py', 'utf8');
const gateway = fs.readFileSync('live/hyperliquid_gateway.py', 'utf8');
const server = fs.readFileSync('live/server.py', 'utf8');
const chartShim = fs.readFileSync('terminal/vendor/galka-chart.js', 'utf8');
const futurePan = fs.readFileSync('terminal/vendor/galka-future-pan.js', 'utf8');
const relativeCrosshair = fs.readFileSync('terminal/vendor/galka-relative-crosshair.js', 'utf8');
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
  ['future pan dependency', html.includes('vendor/galka-future-pan.js?v=1') && futurePan.includes('patchFuturePan')],
  ['direct touch controller', !html.includes('galka-relative-crosshair.js') && html.includes('vendor/galka-touch-actions.js?v=2') && !touchLabHtml.includes('galka-relative-crosshair.js') && touchLabHtml.includes('vendor/galka-touch-actions.js?v=2')],
  ['DeX action dependency', html.includes('vendor/galka-dex-actions.js?v=2') && dexActions.includes('installDesktopActions')],
  ['touch action dependency', html.includes('vendor/galka-touch-actions.js?v=2') && touchActions.includes('installTouchActions')],
  ['strict chart CSP', html.includes("style-src 'self'") && !html.includes("style-src 'self' 'unsafe-inline'") && chartCss.includes('.galka-live-canvas') && !chartShim.includes('.style.') && !futurePan.includes('.style.') && !relativeCrosshair.includes('.style.') && !touchActions.includes('.style.')],
  ['pointer pan controls', chartShim.includes("addEventListener('pointerdown'") && chartShim.includes('setPointerCapture') && chartShim.includes("type: 'pan'")],
  ['grab-style pan direction', chartShim.includes('const draggedBars = deltaX / geometry.plotWidth') && chartShim.includes('this.gesture.startOffset + draggedBars')],
  ['future chart space', futurePan.includes('FUTURE_SPACE_FRACTION = 0.75') && futurePan.includes('minPanOffset') && futurePan.includes('dataStart') && futurePan.includes('windowState.count')],
  ['future time labels', futurePan.includes('logicalTime') && futurePan.includes('windowState.end - 1') && futurePan.includes('estimateInterval')],
  ['pinch zoom controls', chartShim.includes('activePointers') && chartShim.includes('startPinch') && chartShim.includes('updatePinch')],
  ['wheel and trackpad zoom', chartShim.includes("addEventListener('wheel'") && chartShim.includes('zoomTime') && chartShim.includes('event.deltaX')],
  ['price-axis scaling', chartShim.includes('startPriceScale') && chartShim.includes('updatePriceScale') && chartShim.includes('manualPriceRange') && chartCss.includes('ns-resize')],
  ['time-axis scaling', chartShim.includes('startTimeScale') && chartShim.includes('updateTimeScale') && chartCss.includes('ew-resize')],
  ['axis double-click reset', chartShim.includes("addEventListener('dblclick'") && chartShim.includes('resetPriceScale') && chartShim.includes('fitContent')],
  ['precise chart crosshair', chartShim.includes('drawCrosshair') && chartShim.includes('setCrosshair') && chartShim.includes('priceLabel') && chartShim.includes('timeWidth')],
  ['crosshair does not replace desktop pan', chartShim.includes("this.gesture.type === 'pan'") && chartShim.includes('this.setCrosshair(point)')],
  ['TradingView-style touch pan and hold', touchActions.includes('HOLD_MS = 450') && touchActions.includes("type: 'pending'") && touchActions.includes("gesture.type = 'pan'") && touchActions.includes("type: 'crosshair'")],
  ['persistent touch crosshair', touchActions.includes('crosshairPinned') && touchActions.includes('DOUBLE_TAP_MS') && touchActions.includes('activatedByHold') && touchActions.includes('onPointerLeave')],
  ['relative pinned crosshair movement', touchActions.includes('crosshairStartX') && touchActions.includes('touchStartX') && touchActions.includes('gesture.crosshairStartX + dx') && touchActions.includes('gesture.crosshairStartY + dy')],
  ['release coordinate is ignored', touchActions.includes('Deliberately do not apply the finger') && touchActions.includes('SYNTHETIC_MOUSE_BLOCK_MS') && !touchActions.includes('pinnedAtRelease')],
  ['touch GALKA plus', chartCss.includes('.galka-touch-plus') && touchActions.includes("new CustomEvent('galka:select-price'") && dexActions.includes("addEventListener('galka:select-price'")],
  ['safe right-click GALKA draft', dexActions.includes("addEventListener('contextmenu'") && dexActions.includes('/api/live/preview') && dexActions.includes('preview.levels')],
  ['right-click remains desktop-only', dexActions.includes("state.lastPointerType !== 'mouse'")],
  ['browser price actions cannot place orders', !dexActions.includes('/api/live/campaign') && !dexActions.includes('PLACE_REAL_ORDERS') && !touchActions.includes('/api/live/campaign') && !touchActions.includes('PLACE_REAL_ORDERS') && !futurePan.includes('/api/live/campaign') && !relativeCrosshair.includes('/api/live/campaign')],
  ['touch-safe chart surface', chartCss.includes('touch-action: none') && chartCss.includes('overscroll-behavior: contain')],
  ['no runtime CDN', !/https?:\/\//.test(html)],
  ['explicit real confirmation', js.includes('PLACE_REAL_ORDERS')],
  ['double-confirmed emergency', js.includes('EMERGENCY_CLOSE_REAL_POSITION')],
  ['no browser secret', !/HL_API_SECRET_KEY|api_secret_key|PASTE_API_WALLET_PRIVATE_KEY/.test(html + css + js + dexActions + touchActions + futurePan + relativeCrosshair + startupDefaults)],
  ['private Termux config', setup.includes('chmod 600') && setup.includes('$HOME/.config') && setup.includes('galka-live.env')],
  ['live launcher', launcher.includes('Galka LIVE URL:') && launcher.includes('termux-open-url')],
  ['launcher hides session token', launcher.includes("sed '/^Galka LIVE URL: /d'")],
  ['mobile layout', css.includes('.tradebar') && css.includes('100dvh')],
  ['isolated touch laboratory', touchLabHtml.includes('GALKA TOUCH LAB') && touchLabHtml.includes("connect-src 'self'") && touchLabHtml.includes('touch-lab.js?v=2') && !touchLabHtml.includes('live.js') && !touchLabHtml.includes('galka-dex-actions.js')],
  ['persistent HttpOnly lab cookie', touchLabServer.includes('HttpOnly; SameSite=Strict') && touchLabServer.includes('COOKIE_MAX_AGE') && touchLabJs.includes("'/touch-lab/session'") && touchLabJs.includes("'/touch-lab/status'") && !touchLabJs.includes('sessionStorage')],
  ['loopback-only lab server', touchLabServer.includes('args.host != "127.0.0.1"') && touchLabCommon.includes('TOUCH_LAB_HOST="127.0.0.1"')],
  ['background lab lifecycle', touchLabStart.includes('nohup') && touchLabStart.includes('setsid') && touchLabStart.includes('termux-wake-lock') && touchLabOpen.includes('#token=') && touchLabStatus.includes('Touch Lab: RUNNING') && touchLabStop.includes('kill "$pid"')],
  ['legacy lab launcher uses managed start', touchLabLegacyLauncher.includes('galka-touch-lab-start.sh')],
  ['touch laboratory synthetic only', touchLabJs.includes('syntheticCandles') && !touchLabJs.includes('/api/live/') && !touchLabJs.includes('Hyperliquid')],
  ['touch laboratory cannot trade', !/PLACE_REAL_ORDERS|HL_API_SECRET_KEY|\/api\/live\//.test(touchLabBundle)],
  ['touch laboratory separate port', touchLabCommon.includes('GALKA_TOUCH_LAB_PORT:-8099') && touchLabStart.includes('Hyperliquid и реальные ордера не подключены')],
];

for (const [name, ok] of checks) {
  if (!ok) throw new Error(`Live terminal check failed: ${name}`);
}
execFileSync(process.execPath, ['--check', 'terminal/live.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/touch-lab.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-chart.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-future-pan.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-relative-crosshair.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-dex-actions.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-touch-actions.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-startup-defaults.js'], { stdio: 'inherit' });
execFileSync('python3', ['-m', 'py_compile', 'scripts/galka-touch-lab-server.py'], { stdio: 'inherit' });
console.log(`Hyperliquid live terminal: ${checks.length} checks passed`);
