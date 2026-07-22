import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import {
  LEGACY_VIEWER_LIMITS,
  loadPackageFile,
  validatePackage,
} from '../src/app.js';

const sourceHtml = fs.readFileSync('src/index.html', 'utf8');
const sourceApp = fs.readFileSync('src/app.js', 'utf8');
const mobileHtml = fs.readFileSync('terminal/index.html', 'utf8');
const proHtml = fs.readFileSync('terminal/pro.html', 'utf8');
const liveHtml = fs.readFileSync('terminal/live.html', 'utf8');
const backtestHtml = fs.readFileSync('terminal/backtest.html', 'utf8');
const jszip = fs.readFileSync('terminal/vendor/jszip-3.10.1.min.js');
const charts = fs.readFileSync('terminal/vendor/lightweight-charts.standalone.production.js');

const terminalHtml = sourceHtml + mobileHtml + proHtml + liveHtml + backtestHtml;
assert.doesNotMatch(terminalHtml, /<script[^>]+src=["']https?:\/\//i, 'terminals cannot execute CDN scripts');
for (const html of [sourceHtml, mobileHtml, proHtml, liveHtml, backtestHtml]) {
  assert.match(html, /Content-Security-Policy/);
}
assert.doesNotMatch(sourceApp, /innerHTML|insertAdjacentHTML|document\.write/, 'uploaded package values must only reach text-safe DOM APIs');
assert.match(mobileHtml, /ARCHIVE_TOTAL_LIMIT=128\*1024\*1024/);
assert.match(mobileHtml, /safeArchiveName/);
assert.equal(createHash('sha256').update(jszip).digest('hex'), 'acc7e41455a80765b5fd9c7ee1b8078a6d160bbbca455aeae854de65c947d59e');
assert.equal(createHash('sha256').update(charts).digest('hex'), 'c0992580867c4912cc9385b3c2728315bcc1a76c7f1087dca908430fccdf31d7');

const demo = JSON.parse(fs.readFileSync('examples/demo.galka.json', 'utf8'));
const validated = validatePackage(demo);
assert.equal(validated.candles.length, demo.candles.length);
assert.equal(validated.trades.length, demo.trades.length);

const hostile = structuredClone(demo);
hostile.meta.symbol = '<img src=x onerror=globalThis.compromised=true>';
hostile.trades[0].id = '<svg onload=globalThis.compromised=true>';
assert.equal(validatePackage(hostile).trades[0].id.includes('<svg'), true, 'hostile text remains data and is rendered only through textContent');

const invalidOhlc = structuredClone(demo);
invalidOhlc.candles[0].high = invalidOhlc.candles[0].low - 1;
assert.throws(() => validatePackage(invalidOhlc), /OHLC/);
assert.throws(
  () => validatePackage({ ...demo, candles: Array(LEGACY_VIEWER_LIMITS.candles + 1) }),
  /количество свечей/,
);

const encoded = JSON.stringify(demo);
const jsonFile = {
  name: 'demo.galka.json',
  size: new TextEncoder().encode(encoded).byteLength,
  async text() { return encoded; },
};
assert.equal((await loadPackageFile(jsonFile)).trades.length, 1);

const bombEntry = {
  dir: false,
  name: 'package.json',
  _data: { uncompressedSize: LEGACY_VIEWER_LIMITS.jsonBytes + 1 },
  async async() { throw new Error('oversized entry must not be decompressed'); },
};
const fakeZip = {
  async loadAsync() {
    return {
      files: { 'package.json': bombEntry },
      file(name) { return name === 'package.json' ? bombEntry : null; },
    };
  },
};
await assert.rejects(
  loadPackageFile({ name: 'bomb.zip', size: 128 }, fakeZip),
  /безопасный лимит/,
);

console.log('Legacy viewers: local pinned runtimes, XSS-safe rendering and archive limits passed');
