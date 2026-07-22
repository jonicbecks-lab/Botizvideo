import assert from 'node:assert/strict';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { gunzipSync } from 'node:zlib';

const paths = {
  html: 'terminal/pro.html',
  css: 'terminal/pro.css',
  app: 'terminal/pro.js',
  store: 'terminal/modules/store.js',
  paper: 'terminal/modules/paper-engine.js',
  radar: 'terminal/modules/radar-engine.js',
  backup: 'terminal/modules/backup.js',
  stats: 'terminal/modules/galka-stats.js',
  shadow: 'terminal/modules/shadow-engine.js',
  statsAsset: 'terminal/data/galka-stats-v1.json.gz',
  chartVendor: 'terminal/vendor/lightweight-charts.standalone.production.js',
  chartLicense: 'terminal/vendor/LICENSE.lightweight-charts',
  sw: 'terminal/sw.js',
  manifest: 'terminal/manifest.webmanifest',
};

for (const path of Object.values(paths)) assert.ok(fs.existsSync(path), `Missing ${path}`);
for (const path of [paths.app, paths.store, paths.paper, paths.radar, paths.backup, paths.stats, paths.shadow, paths.sw]) {
  execFileSync(process.execPath, ['--check', path], { stdio: 'pipe' });
}

const html = fs.readFileSync(paths.html, 'utf8');
const css = fs.readFileSync(paths.css, 'utf8');
const app = fs.readFileSync(paths.app, 'utf8');
const store = fs.readFileSync(paths.store, 'utf8');
const paper = fs.readFileSync(paths.paper, 'utf8');
const radar = fs.readFileSync(paths.radar, 'utf8');
const backup = fs.readFileSync(paths.backup, 'utf8');
const stats = fs.readFileSync(paths.stats, 'utf8');
const shadow = fs.readFileSync(paths.shadow, 'utf8');
const sw = fs.readFileSync(paths.sw, 'utf8');
const statsAsset = fs.readFileSync(paths.statsAsset);
const chartVendor = fs.readFileSync(paths.chartVendor);
const statsPack = JSON.parse(gunzipSync(statsAsset));
const manifest = JSON.parse(fs.readFileSync(paths.manifest, 'utf8'));
const clientSource = [html, app, store, paper, radar, backup, stats, shadow, sw].join('\n');

const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
assert.equal(new Set(ids).size, ids.length, 'HTML IDs must be unique');

const requiredIds = [
  'mainChart', 'drawingCanvas', 'symbolSelect', 'intervalSelect', 'radarBtn', 'radarLegend',
  'paperPortfolioCards', 'manualGalkaPrice', 'pretradeModal', 'confirmPretrade',
  'radarCandidatesList', 'radarPositive', 'radarNegative', 'sessionStatus',
  'labPackStatus', 'labHeatmap', 'labDepthHistogram', 'labSurvival', 'shadowToggle', 'shadowRecords',
  'exportSnapshot', 'importSnapshot', 'onboardingModal', 'activityLog', 'mobile-nav',
];
for (const id of requiredIds) {
  if (id === 'mobile-nav') assert.ok(html.includes('class="mobile-nav"'));
  else assert.ok(ids.includes(id), `Missing #${id}`);
}

const checks = [
  ['module entrypoint', /<script type="module" src="pro\.js\?v=9"><\/script>/.test(html)],
  ['vendored chart runtime', /vendor\/lightweight-charts\.standalone\.production\.js/.test(html) && !/<script[^>]+https?:\/\//.test(html) && createHash('sha256').update(chartVendor).digest('hex') === 'c0992580867c4912cc9385b3c2728315bcc1a76c7f1087dca908430fccdf31d7'],
  ['three paper symbols', /export const SYMBOLS = \['BTCUSDT', 'ETHUSDT', 'SOLUSDT'\]/.test(store)],
  ['storage key unchanged', /export const STORAGE_KEY = 'galka-pro-v1'/.test(store)],
  ['additive migration', /migrateStore/.test(store) && /deepMerge\(createDefaultStore\(\), source\)/.test(store)],
  ['paper long only', /side:'long'/.test(app) && !/side:'short'/.test(clientSource)],
  ['simple manual defaults', /ladderStepPct: 0\.15/.test(store) && /manualDepthPct: 2\.0/.test(store) && /exitMode: 'target'/.test(store)],
  ['Lab-weighted 0.15–2.0 ladder', /MANUAL_DENSE_DEPTHS = \[0\.15, 0\.3, 0\.45, 0\.6, 0\.9, 1\.2, 1\.5, 2\.0\]/.test(paper) && /MANUAL_DENSE_WEIGHTS/.test(paper)],
  ['manual L1 repeats', /l1_cycle_closed/.test(paper) && /closeAndRearmL1/.test(paper) && /l1Cycles/.test(store)],
  ['legacy auto ladder', /LEGACY_DEPTHS = \[0\.25, 0\.7, 1\.25, 1\.9, 2\.65, 3\.5\]/.test(paper)],
  ['manual target-only campaign', /manualTarget \? 'target'/.test(paper) && /reason: 'v_low_target'/.test(paper)],
  ['deterministic paper engine', /processCampaignQuote/.test(paper) && /nowMs = Date\.now\(\)/.test(paper)],
  ['trailing floor', /Math\.max\(campaign\.vLow, nextHigh \* \(1 - distance\)\)/.test(paper)],
  ['idempotent fills', /level\.status !== 'pending'/.test(paper)],
  ['safe pre-trade preview', /previewCampaign/.test(app) && /PAPER PREVIEW/.test(html)],
  ['simple bottom entry UI', /simpleTradeBar/.test(app) && /simpleGalkaPrice/.test(app) && /simpleGalkaLaunch/.test(app)],
  ['essential drawing UI', app.includes('[data-tool="ray"]') && app.includes('[data-tool="measure"]') && app.includes('#deleteBtn') && app.includes('#clearBtn')],
  ['two-tap mobile drawing', /drawingAwaitSecond/.test(app) && app.includes('Луч: коснись начала, потом направления') && app.includes('Линейка: коснись начала, потом конца')],
  ['manual marker hidden', app.includes("if(p&&p.source!=='manual')")],
  ['target mode hides reclaim', app.includes("if(c.exitMode!=='target'&&c.reclaimPrice)")],
  ['Radar visual only', !/createCampaign|paper\.symbols/.test(radar) && /EXPLAINABLE SCORE/.test(html)],
  ['positive and negative labels', /labelRadarCandidate\('positive'\)/.test(app) && /labelRadarCandidate\('negative'\)/.test(app)],
  ['Radar filters', ['all', 'strong', 'mine', 'profitable', 'losing'].every((value) => html.includes(`data-radar-filter="${value}"`))],
  ['verified Galka stats asset', createHash('sha256').update(statsAsset).digest('hex') === '828175607d3619c4af1eea24776ee3d2312e0641962fbc016179bc71f0b830f6' && /parseGalkaStatsBytes/.test(stats)],
  ['mobile stats budget', statsAsset.length < 1_000_000 && !fs.existsSync('terminal/data/galka-stats-v1.json')],
  ['Galka Lab safety contract', statsPack.safety.paperOnly === true && statsPack.safety.autoPaperDefault === false && statsPack.safety.realOrders === false && statsPack.safety.liveShadowRequiredBeforeAutoPaper === true],
  ['Galka Lab full market contract', ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'].every((symbol) => statsPack.data.symbols.includes(symbol)) && statsPack.model.selected_k >= 4 && statsPack.model.selected_k <= 7],
  ['shadow is isolated and opt-in', /enabled: false/.test(shadow) && /paperBalanceImpact: 0/.test(shadow) && !/paper\.symbols|startingBalance|realizedPnl/.test(shadow)],
  ['shadow does not authorize auto-paper', /Shadow-only; auto-paper остаётся выключен/.test(app) && statsPack.safety.historicalScreenIsNotAuthorization === true],
  ['drawing tool set retained', ['cursor','crosshair','trend','ray','horizontal','vertical','rect','channel','fib','measure','longPosition','text'].every((tool) => html.includes(`data-tool="${tool}"`))],
  ['drawing selection and handles', /drawingHitAt/.test(app) && /openSelectedDrawingProperties/.test(app) && /editing-object/.test(css)],
  ['session health and chronological portfolio replay', /catchUpAfterReconnect/.test(app) && /fetchClosedMinuteRange/.test(app) && /recoveryCandlePath/.test(app) && /checkGlobalLiquidation\(\{atMs,recovered:true/.test(app) && /sessionQuoteAge/.test(html)],
  ['bounded full backup restore', /createBackupSnapshot/.test(app) && /validateBackupSnapshot/.test(app) && /parseBoundedJson/.test(app) && /BACKUP_MAX_BYTES/.test(app) && /PRE_RESTORE_BACKUP_KEY/.test(app)],
  ['browser security policy', /Content-Security-Policy/.test(html) && /connect-src 'self' https:\/\/fapi\.binance\.com wss:\/\/fstream\.binance\.com/.test(html)],
  ['installable PWA', manifest.display === 'standalone' && manifest.icons.length >= 2 && /serviceWorker\.register/.test(app)],
  ['service worker is cache-only', /Market data is never served from an application cache/.test(sw) && !/patchProSource|servePatchedPro|importScripts\(|from ['"]\.\/modules\/paper-engine/.test(sw)],
  ['large stats pack stays lazy', !/data\/galka-stats-v1\.json\.gz/.test(sw) && /loadGalkaStatsPack/.test(app)],
  ['onboarding retained but disabled by default', /onboardingSteps/.test(app) && /ШАГ 1 ИЗ 5/.test(html) && /completed: true/.test(store)],
  ['training replay retained', /markReplayGalka/.test(app) && /replayExamples/.test(store) && /future=replay\.source/.test(app)],
  ['legacy navigation retained behind simple UI', ['chart','paper','radar','lab','more'].every((panel) => html.includes(`data-mobile-panel="${panel}"`))],
  ['advanced panels remain reachable', !app.includes('.mobile-nav,.side-tabs') && !app.includes('#radarBtn,#toggleSidebar') && app.includes('.side-panel.active{display:block!important}') && app.includes('#toggleSidebar{display:inline-flex!important}')],
  ['desktop chart remains visible', app.includes('.workspace{grid-column:1!important}') && app.includes('.drawing-pill{display:flex!important')],
  ['bottom sheet snaps retained', /\.sidebar\.snap-low/.test(css) && /\.sidebar\.snap-high/.test(css) && /beginSheetGesture/.test(app)],
  ['chart-first shell', /\.chart-main-wrap[\s\S]*height: 100%/.test(css) && /translateY\(calc\(100% \+ 12px\)\)/.test(css)],
  ['no chart grid', /vertLines:\{visible:false\}/.test(app) && /horzLines:\{visible:false\}/.test(app)],
  ['TradingView attribution retained', /TradingView/.test(html) && /attributionLogo:true/.test(app)],
  ['Termux direct launch remains', /terminal\/pro\.html/.test(fs.readFileSync('scripts/start-termux.sh', 'utf8'))],
  ['no exchange credentials', !/(?:api|secret)[_-]?key\s*[:=]/i.test(clientSource)],
];

for (const [name, ok] of checks) assert.ok(ok, `Galka Pro check failed: ${name}`);

console.log(`Galka Pro: ${checks.length} architecture checks passed; app ${(app.length / 1024).toFixed(1)} KiB; ${ids.length} unique IDs`);
