import assert from 'node:assert/strict';
import {
  STORAGE_KEY,
  STORE_SCHEMA_VERSION,
  createDefaultStore,
  createMemoryStorage,
  loadStore,
  migrateStore,
  saveStore,
} from '../terminal/modules/store.js';
import {
  BACKUP_MAX_BYTES,
  createBackupSnapshot,
  parseBoundedJson,
  summarizeBackupSnapshot,
  validateBackupSnapshot,
} from '../terminal/modules/backup.js';

const oldCampaign = {
  campaignId: 'C-BTC-legacy',
  symbol: 'BTCUSDT',
  source: 'manual',
  status: 'trailing',
  vLow: 60_000,
  trailArmed: true,
  trailHigh: 61_500,
  trailStop: 60_900,
  qty: 0.012,
  levels: [
    { index: 1, price: 59_910, status: 'filled', qty: 0.006, fillTime: '2026-01-01T00:00:00.000Z' },
    { index: 2, price: 59_820, status: 'pending', qty: 0 },
  ],
  futureField: { preserve: true },
};

const legacyStore = {
  ui: {
    symbol: 'ETHUSDT',
    drawings: { 'BTCUSDT|15m': [{ id: 'D1', type: 'trend' }] },
    radar: { enabled: true, minScore: 67 },
  },
  paper: {
    settings: { startingBalance: 2_000, symbolNotional: 700 },
    realizedPnl: 42.5,
    trades: [{ tradeId: 'P000001', symbol: 'SOLUSDT', netPnl: 42.5 }],
    symbols: {
      BTCUSDT: { pattern: { patternId: 'legacy-pattern' }, campaign: oldCampaign },
      ETHUSDT: { pattern: null, campaign: { campaignId: 'C-ETH', status: 'waiting', levels: [] } },
      SOLUSDT: { pattern: null, campaign: null },
    },
  },
  training: {
    manualExamples: [{ id: 'M1', symbol: 'BTCUSDT', status: 'active' }],
  },
  unknownFutureSection: { mustSurvive: true },
};

const migrated = migrateStore(legacyStore);
assert.equal(migrated.schemaVersion, STORE_SCHEMA_VERSION);
assert.equal(migrated.paper.symbols.BTCUSDT.campaign.campaignId, oldCampaign.campaignId);
assert.equal(migrated.paper.symbols.BTCUSDT.campaign.futureField.preserve, true);
assert.equal(migrated.paper.symbols.BTCUSDT.campaign.exitMode, 'target');
assert.equal(migrated.paper.symbols.BTCUSDT.campaign.reclaimPrice, 60_000);
assert.equal(migrated.paper.symbols.BTCUSDT.campaign.trailArmed, false);
assert.equal(migrated.paper.symbols.BTCUSDT.campaign.status, 'open');
assert.equal(migrated.paper.symbols.BTCUSDT.campaign.l1Cycles, 0);
assert.equal(migrated.paper.symbols.BTCUSDT.campaign.l1CycleRealizedPnl, 0);
assert.deepEqual(migrated.ui.drawings, legacyStore.ui.drawings);
assert.deepEqual(migrated.paper.trades, legacyStore.paper.trades);
assert.deepEqual(migrated.training.manualExamples, legacyStore.training.manualExamples);
assert.deepEqual(migrated.unknownFutureSection, legacyStore.unknownFutureSection);
assert.equal(migrated.ui.radar.filter, 'all');
assert.equal(migrated.ui.radar.enabled, false);
assert.equal(migrated.ui.indicators.volume, false);
assert.equal(migrated.ui.lowerIndicator, null);
assert.equal(migrated.ui.onboarding.completed, true);
assert.deepEqual(migrated.training.radarLabels, []);
assert.equal(migrated.paper.recovery.policy, 'closed-1m-directional-v1');
assert.deepEqual(Object.keys(migrated.paper.recovery.symbols), ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']);
assert.equal(migrated.paper.recovery.symbols.BTCUSDT.lastMarketAt, null);
assert.equal(migrated.shadow.enabled, false);
assert.equal(migrated.shadow.profile, 'Balanced');
assert.deepEqual(migrated.shadow.records, []);
assert.equal(migrated.ui.lab.window, 'all');
assert.equal(migrated.ui.lab.regime, 'all');

const paperBeforeShadow = JSON.stringify(migrated.paper);
migrated.shadow.enabled = true;
migrated.shadow.startedAt = '2026-07-12T00:00:00.000Z';
migrated.shadow.records.push({ id: 'S-test', paperBalanceImpact: 0 });
assert.equal(JSON.stringify(migrated.paper), paperBeforeShadow);

const memory = createMemoryStorage();
saveStore(migrated, memory);
assert.ok(memory.getItem(STORAGE_KEY));
const roundTrip = loadStore(memory);
assert.deepEqual(roundTrip.paper.symbols, migrated.paper.symbols);
assert.deepEqual(roundTrip.ui.drawings, migrated.ui.drawings);
assert.deepEqual(roundTrip.training.manualExamples, migrated.training.manualExamples);
assert.deepEqual(roundTrip.paper.recovery, migrated.paper.recovery);
assert.equal(roundTrip.shadow.enabled, false, 'simple terminal always disables Shadow on load');
assert.deepEqual(roundTrip.shadow.records, migrated.shadow.records);

const blank = createDefaultStore();
assert.deepEqual(Object.keys(blank.paper.symbols), ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']);
assert.equal(blank.paper.settings.exitMode, 'target');
assert.equal(blank.ui.indicators.volume, false);
assert.equal(blank.ui.lowerIndicator, null);
assert.equal(blank.ui.onboarding.completed, true);

const snapshot = createBackupSnapshot(roundTrip, 'test', '2026-07-11T00:00:00.000Z');
const summary = summarizeBackupSnapshot(snapshot);
assert.deepEqual(summary, {
  createdAt: '2026-07-11T00:00:00.000Z',
  campaigns: 2,
  filledLevels: 1,
  trades: 1,
  drawings: 1,
  manualExamples: 1,
  radarLabels: 0,
  shadowRecords: 1,
  shadowEnabled: false,
});
assert.equal(validateBackupSnapshot(snapshot).paper.symbols.BTCUSDT.campaign.campaignId, oldCampaign.campaignId);
assert.throws(() => validateBackupSnapshot({}), /не полный snapshot/);

assert.throws(
  () => parseBoundedJson('{"__proto__":{"polluted":true}}'),
  /опасный ключ/,
  'prototype-polluting JSON keys must be rejected before migration',
);
assert.equal({}.polluted, undefined);
assert.throws(
  () => migrateStore({ paper: { settings: { startingBalance: Number.POSITIVE_INFINITY } } }),
  /Non-finite store number/,
);
assert.throws(
  () => saveStore({ unsafe: Number.NaN }, createMemoryStorage()),
  /Non-finite store number/,
);
const unsafeSnapshot = createBackupSnapshot(createDefaultStore(), 'test');
unsafeSnapshot.store.paper.settings.leverage = 1_000;
assert.throws(() => validateBackupSnapshot(unsafeSnapshot), /безопасного диапазона/);
assert.throws(
  () => parseBoundedJson(`"${'x'.repeat(BACKUP_MAX_BYTES)}"`),
  /безопасный лимит/,
);
const poisonedStorage = createMemoryStorage({
  [STORAGE_KEY]: '{"__proto__":{"polluted":true}}',
});
const originalWarn = console.warn;
console.warn = () => {};
try {
  assert.deepEqual(loadStore(poisonedStorage).paper.symbols, createDefaultStore().paper.symbols);
} finally {
  console.warn = originalWarn;
}
assert.equal({}.polluted, undefined);

console.log('Galka store: simple migration, L1 cycle fields, round-trip and backup checks passed');
