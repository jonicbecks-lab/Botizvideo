import assert from 'node:assert/strict';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const app = fs.readFileSync('terminal/pro.js', 'utf8');
const html = fs.readFileSync('terminal/pro.html', 'utf8');
const serviceWorker = fs.readFileSync('terminal/sw.js', 'utf8');

execFileSync(process.execPath, ['--check', 'terminal/pro.js'], { stdio: 'pipe' });
execFileSync(process.execPath, ['--check', 'terminal/sw.js'], { stdio: 'pipe' });

assert.match(app, /drawingAwaitSecond:false/);
assert.match(app, /function recordL1Cycle/);
assert.match(app, /event\.type==='l1_cycle_closed'/);
assert.match(app, /function renderSimpleTradeBar/);
assert.match(app, /installSimpleGalkaUi\(\)/);
assert.match(app, /if\(c\.exitMode!=='target'&&c\.reclaimPrice\)/);
assert.match(app, /Луч: коснись начала, потом направления/);
assert.match(app, /Линейка: коснись начала, потом конца/);
assert.doesNotMatch(app, /text:p\.source==='manual'\?'GALKA':'V-low'/);

assert.match(html, /vendor\/lightweight-charts\.standalone\.production\.js/);
assert.doesNotMatch(html, /<script[^>]+https?:\/\//);
assert.doesNotMatch(serviceWorker, /patchProSource|servePatchedPro|x-galka-patch/i);
assert.match(serviceWorker, /galka-production-shell-v11/);
assert.match(serviceWorker, /MARKET_HOSTS/);
assert.match(serviceWorker, /Market data is never served from an application cache/);

console.log('Final source integration: first-load app and cache-only service worker verified');

