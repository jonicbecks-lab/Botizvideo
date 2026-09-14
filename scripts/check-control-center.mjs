import fs from 'node:fs';

const html = fs.readFileSync('terminal/live.html', 'utf8');
const css = fs.readFileSync('terminal/control-center.css', 'utf8');
const ui = fs.readFileSync('terminal/vendor/galka-control-center.js', 'utf8');
const server = fs.readFileSync('live/research_server.py', 'utf8');
const center = fs.readFileSync('live/control_center.py', 'utf8');

const checks = [
  ['control center entry', html.includes('id="openControlCenter"') && html.includes('Центр управления проектом')],
  ['control center panel', html.includes('id="controlCenterPanel"') && html.includes('id="controlCenterContent"')],
  ['local control center assets', html.includes('control-center.css?v=1') && html.includes('galka-control-center.js?v=1')],
  ['strict CSP preserved', html.includes("style-src 'self'") && !html.includes("style-src 'self' 'unsafe-inline'")],
  ['read-only overview API', server.includes('"/api/live/control-center"') && server.includes('control_center_for(self.engine).overview()')],
  ['read-only check API', server.includes('"/api/live/control-center/check"') && server.includes('.check_now(')],
  ['authenticated API', server.includes('_require_api_auth()')],
  ['project-specific Hyperliquid flow', center.includes('Ручная GALKA и её форма') && center.includes('Расчёт 8 уровней') && center.includes('Отправка ордеров') && center.includes('Сверка и завершение')],
  ['parallel research flow', center.includes('Параллельный research-контур') && center.includes('researchAffectsTrading')],
  ['logic drift audit', center.includes('paper-vs-live') && center.includes('coin-universe') && center.includes('l1-rearm') && center.includes('notional-limit')],
  ['truthful partial health', center.includes('удалённый push специально не помечается зелёным') && center.includes('не публикует отдельный постоянный health-флаг')],
  ['safe read-only check', center.includes('Проверка read-only') && center.includes('action_lock.acquire(timeout=0.35)')],
  ['no exchange mutation in control center', !center.includes('place_entry_with_target(') && !center.includes('cancel_oids(') && !center.includes('emergency_market_close(') && !center.includes('reconcile_system(')],
  ['Russian status vocabulary', ui.includes("working: 'Работает'") && ui.includes("problem: 'Проблема'") && ui.includes("partial: 'Частично'") && ui.includes("disconnected: 'Не подключено'")],
  ['flow explainers', ui.includes('Получает:') && ui.includes('Делает:') && ui.includes('Передаёт дальше:')],
  ['check button', ui.includes('Проверить сейчас') && ui.includes('/api/live/control-center/check')],
  ['no runtime style injection', !ui.includes("createElement('style')") && !ui.includes('style=' )],
  ['mobile full-screen layout', css.includes('.control-center-panel{position:fixed;inset:0') && css.includes('@media (min-width:720px)')],
];

const failed = checks.filter(([, ok]) => !ok);
for (const [name, ok] of checks) console.log(`${ok ? 'OK' : 'FAIL'} ${name}`);
if (failed.length) {
  console.error(`Control center checks failed: ${failed.map(([name]) => name).join(', ')}`);
  process.exit(1);
}
console.log(`Control center checks passed: ${checks.length}`);
