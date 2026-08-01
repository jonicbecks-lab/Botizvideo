import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const html = fs.readFileSync('terminal/resume-lab.html', 'utf8');
const css = fs.readFileSync('terminal/resume-lab.css', 'utf8');
const js = fs.readFileSync('terminal/resume-lab.js', 'utf8');
const server = fs.readFileSync('live/resume_lab_server.py', 'utf8');
const start = fs.readFileSync('scripts/galka-resume-lab-start.sh', 'utf8');
const open = fs.readFileSync('scripts/galka-resume-lab-open.sh', 'utf8');
const status = fs.readFileSync('scripts/galka-resume-lab-status.sh', 'utf8');
const stop = fs.readFileSync('scripts/galka-resume-lab-stop.sh', 'utf8');

const checks = [
  ['read-only label', html.includes('READ ONLY') && server.includes('READ ONLY')],
  ['separate port', server.includes('8101') && start.includes('GALKA_RESUME_LAB_PORT')],
  ['real Hyperliquid candles', server.includes('candleSnapshot') && server.includes('api.hyperliquid.xyz/info')],
  ['no live campaign endpoint', !js.includes('/api/live/campaign') && !js.includes('PLACE_REAL_ORDERS')],
  ['no secret handling', !/HL_API_SECRET_KEY|api_secret_key|PRIVATE_KEY/.test(html + js + server)],
  ['resume visibility hook', js.includes("visibilitychange") && js.includes("pageshow") && js.includes("focus")],
  ['immediate resume refresh', js.includes('requestResumeRefresh') && js.includes('loadCandles({ fullReload, reason: detail })')],
  ['full catch-up after background', js.includes('hiddenFor > Math.min(interval, 60_000)') && js.includes('limit = shouldReload ? 600 : 5')],
  ['duplicate request guard', js.includes('runtime.candleBusy') && js.includes('runtime.pendingRefresh')],
  ['fifteen second foreground loop', js.includes('setTimeout(candleLoop, 15_000)') && js.includes('if (!document.hidden)')],
  ['same touch controller', html.includes('galka-touch-actions.js?v=2')],
  ['background start', start.includes('nohup') && start.includes('setsid') && start.includes('termux-wake-lock')],
  ['separate lifecycle commands', open.includes('termux-open-url') && status.includes('RUNNING') && stop.includes('kill "$pid"')],
  ['mobile status layout', css.includes('.resume-bar') && css.includes('env(safe-area-inset-bottom)')],
];

for (const [name, ok] of checks) {
  if (!ok) throw new Error(`Resume Lab check failed: ${name}`);
}

execFileSync(process.execPath, ['--check', 'terminal/resume-lab.js'], { stdio: 'inherit' });
console.log(`Galka Resume Lab: ${checks.length} checks passed`);
