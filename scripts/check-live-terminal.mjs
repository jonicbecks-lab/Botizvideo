import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const html = fs.readFileSync('terminal/live.html', 'utf8');
const css = fs.readFileSync('terminal/live.css', 'utf8');
const chartCss = fs.readFileSync('terminal/live-chart.css', 'utf8');
const autoQueueCss = fs.readFileSync('terminal/auto-queue.css', 'utf8');
const js = fs.readFileSync('terminal/live.js', 'utf8');
const setup = fs.readFileSync('scripts/setup-galka-live.sh', 'utf8');
const launcher = fs.readFileSync('scripts/start-galka-live.sh', 'utf8');
const persistentStart = fs.readFileSync('scripts/galka-live-start.sh', 'utf8');
const persistentOpen = fs.readFileSync('scripts/galka-live-open.sh', 'utf8');
const persistentStatus = fs.readFileSync('scripts/galka-live-status.sh', 'utf8');
const persistentStop = fs.readFileSync('scripts/galka-live-stop.sh', 'utf8');
const persistentCommon = fs.readFileSync('scripts/galka-live-common.sh', 'utf8');
const testProfile = fs.readFileSync('scripts/galka-live-10-usd-test.sh', 'utf8');
const testLauncher = fs.readFileSync('scripts/start-galka-10-usd-live-test.sh', 'utf8');
const ladder = fs.readFileSync('live/live_ladder.py', 'utf8');
const gateway = fs.readFileSync('live/hyperliquid_gateway.py', 'utf8');
const server = fs.readFileSync('live/server.py', 'utf8');
const persistentServer = fs.readFileSync('live/persistent_server.py', 'utf8');
const researchServer = fs.readFileSync('live/research_server.py', 'utf8');
const researchEngine = fs.readFileSync('live/research_engine.py', 'utf8');
const researchRecorder = fs.readFileSync('live/research_recorder.py', 'utf8');
const autoQueueEngine = fs.readFileSync('live/auto_queue_engine.py', 'utf8');
const chartShim = fs.readFileSync('terminal/vendor/galka-chart.js', 'utf8');
const futurePan = fs.readFileSync('terminal/vendor/galka-future-pan.js', 'utf8');
const nativePlotPan = fs.readFileSync('terminal/vendor/galka-native-plot-pan.js', 'utf8');
const visibilityRecovery = fs.readFileSync('terminal/vendor/galka-visibility-recovery.js', 'utf8');
const liveSession = fs.readFileSync('terminal/vendor/galka-live-session.js', 'utf8');
const dexActions = fs.readFileSync('terminal/vendor/galka-dex-actions.js', 'utf8');
const touchActions = fs.readFileSync('terminal/vendor/galka-touch-actions.js', 'utf8');
const autoQueue = fs.readFileSync('terminal/vendor/galka-auto-queue.js', 'utf8');
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
  ['legacy header remains compatible', js.includes('X-Galka-Session') && server.includes('X-Galka-Session')],
  ['persistent cookie session', html.includes('galka-live-session.js?v=1') && liveSession.includes('/api/live/session') && liveSession.includes('persistent-cookie-session') && persistentServer.includes('HttpOnly; SameSite=Strict') && persistentServer.includes('GalkaLiveSession')],
  ['persistent token is private', persistentServer.includes('browser-session.token') && persistentServer.includes('O_NOFOLLOW') && persistentServer.includes('0o600') && !persistentServer.includes('Galka LIVE URL:')],
  ['manual reconciliation', js.includes('/api/live/reconcile') && server.includes('/api/live/reconcile')],
  ['local chart dependency', html.includes('vendor/galka-chart.js?v=3') && html.includes('live-chart.css?v=3') && chartShim.includes('LightweightCharts')],
  ['future pan dependency', html.includes('vendor/galka-future-pan.js?v=1') && futurePan.includes('patchFuturePan')],
  ['native plot pan dependency', html.includes('vendor/galka-native-plot-pan.js?v=4') && nativePlotPan.includes('PAN_START_SLOP = 7') && nativePlotPan.includes("galka:native-plot-pan-start")],
  ['resume recovery dependency', html.includes('vendor/galka-visibility-recovery.js?v=2') && visibilityRecovery.includes('pageshow') && visibilityRecovery.includes('visibilitychange') && visibilityRecovery.includes('visualViewport') && visibilityRecovery.includes('RETRY_DELAYS')],
  ['single tested touch controller', !html.includes('galka-relative-crosshair.js') && html.includes('vendor/galka-touch-actions.js?v=5') && touchActions.includes('installTouchActions')],
  ['DeX action dependency', html.includes('vendor/galka-dex-actions.js?v=3') && dexActions.includes('installDesktopActions')],
  ['strict chart CSP', html.includes("style-src 'self'") && !html.includes("style-src 'self' 'unsafe-inline'") && chartCss.includes('.galka-live-canvas') && !chartShim.includes('.style.') && !futurePan.includes('.style.') && !visibilityRecovery.includes('.style.') && !touchActions.includes('.style.')],
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
  ['TradingView-style touch pan and hold', touchActions.includes('HOLD_MS = 650') && touchActions.includes('MOVE_SLOP = 7') && touchActions.includes("type: 'pending'") && touchActions.includes("gesture.type = 'pan'") && touchActions.includes("type: 'crosshair'")],
  ['native vertical pan cancels crosshair hold', touchActions.includes('onNativePlotPanStart') && touchActions.includes("type: 'native-pan'") && touchActions.includes("galka:native-plot-pan-start")],
  ['pinned crosshair locks chart gestures', touchActions.includes('setCrosshairLock(true)') && touchActions.includes('__galkaCrosshairLocked') && nativePlotPan.includes('__galkaCrosshairLocked') && nativePlotPan.includes("galka:crosshair-lock")],
  ['persistent touch crosshair', touchActions.includes('crosshairPinned') && touchActions.includes('DOUBLE_TAP_MS') && touchActions.includes('activatedByHold') && touchActions.includes('onPointerLeave')],
  ['relative pinned crosshair movement', touchActions.includes('crosshairStartX') && touchActions.includes('touchStartX') && touchActions.includes('gesture.crosshairStartX + dx')],
  ['stable crosshair on finger release', touchActions.includes('SYNTHETIC_MOUSE_BLOCK_MS') && touchActions.includes('lastTouchEndAt')],
  ['touch GALKA plus', chartCss.includes('.galka-touch-plus') && touchActions.includes("new CustomEvent('galka:select-price'") && dexActions.includes("addEventListener('galka:select-price'")],
  ['active crosshair can draft AUTO price', dexActions.includes("galka:auto-queue-price") && touchActions.includes('side') && !touchActions.includes('!state.crosshairPinned || !chart.crosshair || input?.disabled')],
  ['crosshair GALKA action', html.includes('id="crosshairGalkaAction"') && html.includes('Поставить GALKA') && chartCss.includes('.crosshair-galka-action') && touchActions.includes('updateCrosshairAction') && touchActions.includes('preview.click()') && touchActions.includes('input.value = formatted')],
  ['AUTO queue UI', html.includes('id="autoQueueButton"') && html.includes('galka-auto-queue.js?v=1') && html.includes('auto-queue.css?v=1') && autoQueue.includes('QUEUE_REAL_GALKA') && autoQueueCss.includes('.auto-queue-button')],
  ['AUTO queue backend', researchServer.includes('/api/live/queue') && researchServer.includes('AutoQueueGalkaLiveEngine') && autoQueueEngine.includes('sourceCampaignId') && autoQueueEngine.includes('historyValidation') && autoQueueEngine.includes('cached_mid_touch')],
  ['AUTO only after normal completion', autoQueueEngine.includes('should_activate = source_status == "completed"') && autoQueueEngine.includes('recovery_closed') && autoQueueEngine.includes('emergency_closed')],
  ['safe right-click GALKA draft', dexActions.includes("addEventListener('contextmenu'") && dexActions.includes('/api/live/preview') && dexActions.includes('preview.levels')],
  ['right-click remains desktop-only', dexActions.includes("state.lastPointerType !== 'mouse'")],
  ['browser price actions cannot place orders directly', !dexActions.includes('/api/live/campaign') && !dexActions.includes('PLACE_REAL_ORDERS') && !touchActions.includes('/api/live/campaign') && !touchActions.includes('PLACE_REAL_ORDERS') && !futurePan.includes('/api/live/campaign') && !visibilityRecovery.includes('/api/live/campaign') && !liveSession.includes('/api/live/campaign')],
  ['touch-safe chart surface', chartCss.includes('touch-action: none') && chartCss.includes('overscroll-behavior: contain')],
  ['no runtime CDN', !/https?:\/\//.test(html)],
  ['explicit real confirmation', js.includes('PLACE_REAL_ORDERS')],
  ['double-confirmed emergency', js.includes('EMERGENCY_CLOSE_REAL_POSITION')],
  ['no browser secret', !/HL_API_SECRET_KEY|api_secret_key|PASTE_API_WALLET_PRIVATE_KEY/.test(html + css + js + dexActions + touchActions + autoQueue + futurePan + visibilityRecovery + liveSession + startupDefaults)],
  ['private Termux config', setup.includes('chmod 600') && setup.includes('$HOME/.config') && setup.includes('galka-live.env')],
  ['persistent background launcher', launcher.includes('galka-live-start.sh') && persistentStart.includes('nohup') && persistentStart.includes('setsid') && persistentStart.includes('termux-wake-lock') && persistentStart.includes('live.research_server') && researchServer.includes('persistent_server')],
  ['research sidecar is isolated', researchEngine.includes('GalkaResearchRecorder') && researchRecorder.includes('researchOnly') && !researchRecorder.includes('place_entry_with_target') && !researchRecorder.includes('cancel_oids') && !researchRecorder.includes('emergency_market_close')],
  ['separate open status stop', persistentOpen.includes('termux-open-url') && persistentOpen.includes('#token=') && persistentStatus.includes('Galka LIVE: RUNNING') && persistentStop.includes('STOP_GALKA_LIVE')],
  ['loopback runtime guard', persistentCommon.includes('127.0.0.1') && persistentCommon.includes('/healthz') && persistentCommon.includes('browser-session.token')],
  ['mobile layout', css.includes('.tradebar') && css.includes('100dvh')],
];

for (const [name, ok] of checks) {
  if (!ok) throw new Error(`Live terminal check failed: ${name}`);
}
execFileSync(process.execPath, ['--check', 'terminal/live.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-chart.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-future-pan.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-native-plot-pan.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-visibility-recovery.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-live-session.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-dex-actions.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-touch-actions.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-auto-queue.js'], { stdio: 'inherit' });
execFileSync(process.execPath, ['--check', 'terminal/vendor/galka-startup-defaults.js'], { stdio: 'inherit' });
console.log(`Hyperliquid live terminal: ${checks.length} checks passed`);
