import assert from 'node:assert/strict';
import {
  RECOVERY_PATH_POLICY,
  createCampaign,
  paperPortfolioSnapshot,
  processCampaignQuote,
  recoveryCandlePath,
  replayCampaignCandles,
  validateRecoveryCandleRange,
} from '../terminal/modules/paper-engine.js';

const settings = {
  symbolNotional: 400,
  maxHours: 72,
  ladderStepPct: 0.15,
  manualDepthPct: 2,
  exitMode: 'target',
  reclaimBufferPct: 0,
  trailDistancePct: 0.75,
  makerFee: 0.0002,
};
const pattern = {
  patternId: 'M-recovery',
  source: 'manual',
  trainingExampleId: 'M-recovery',
  vLow: 100,
};
const BASE = 1_800_000_000_000;
const minute = (index, { open, high, low, close }) => ({
  openTime: BASE + index * 60_000,
  closeTime: BASE + (index + 1) * 60_000 - 1,
  open,
  high,
  low,
  close,
});

assert.deepEqual(
  recoveryCandlePath(minute(1, { open: 100, high: 102, low: 99, close: 101 })).map(
    (point) => point.phase,
  ),
  ['open', 'low', 'high', 'close'],
  'a bullish missed candle has one deterministic path',
);
assert.deepEqual(
  recoveryCandlePath(minute(1, { open: 101, high: 102, low: 99, close: 100 })).map(
    (point) => point.phase,
  ),
  ['open', 'high', 'low', 'close'],
  'a bearish missed candle has one deterministic path',
);
assert.deepEqual(
  recoveryCandlePath(
    minute(0, { open: 100, high: 105, low: 95, close: 100.1 }),
    BASE + 30_000,
  ).map((point) => point.phase),
  ['boundary_close'],
  'the overlapping boundary minute must not speculate about pre-gap high/low order',
);

const candles = [
  minute(0, { open: 100, high: 105, low: 95, close: 100.1 }),
  minute(1, { open: 100.1, high: 100.2, low: 99.84, close: 100.1 }),
  minute(2, { open: 100.1, high: 100.3, low: 99.65, close: 100.2 }),
  minute(3, { open: 101.5, high: 102, low: 100.5, close: 100.8 }),
];

assert.equal(
  validateRecoveryCandleRange(candles, BASE + 30_000, BASE + 4 * 60_000 - 1).length,
  4,
  'a complete closed-minute range is accepted',
);
assert.throws(
  () => validateRecoveryCandleRange(
    [candles[0], candles[2], candles[3]],
    BASE + 30_000,
    BASE + 4 * 60_000 - 1,
  ),
  /Incomplete recovery candles|Missing recovery candle/,
  'a missing minute must keep recovery pending instead of advancing the cursor',
);

const campaign = createCampaign('BTCUSDT', pattern, settings, BASE - 1_000);
const replay = replayCampaignCandles(campaign, candles, settings, { afterMs: BASE + 30_000 });

assert.equal(replay.policy, RECOVERY_PATH_POLICY);
assert.equal(replay.candlesReplayed, 3, 'replay stops after the deep campaign closes');
assert.equal(replay.boundaryCandles, 1);
assert.equal(
  replay.events.filter((event) => event.type === 'level_filled').length,
  3,
  'replay restores one shallow L1 fill and then the L1+L2 deep fill',
);
const recoveredCycle = replay.events.find((event) => event.type === 'l1_cycle_closed');
assert.ok(recoveredCycle);
assert.equal(recoveredCycle.recovered, true);
assert.ok(recoveredCycle.netPnl > 0);
assert.equal(campaign.l1Cycles, 1);
assert.ok(replay.events.every((event) => event.recovered));
assert.equal(replay.events.some((event) => event.type === 'trailing_armed'), false);
assert.equal(replay.close.reason, 'v_low_target');
assert.equal(replay.close.price, 100, 'deep recovery exits exactly at GALKA');
assert.ok(replay.close.atMs > candles[2].openTime && replay.close.atMs < candles[2].closeTime);

const quantityAfterReplay = campaign.qty;
const duplicate = replayCampaignCandles(campaign, candles, settings, {
  afterMs: candles.at(-1).closeTime,
});
assert.equal(duplicate.candlesReplayed, 0);
assert.equal(duplicate.events.length, 0);
assert.equal(campaign.qty, quantityAfterReplay, 'a durable close-time cursor prevents duplicate replay');

const trailingSettings = { ...settings, exitMode: 'trail', reclaimBufferPct: 0.1 };
const autoCampaign = createCampaign(
  'SOLUSDT',
  { ...pattern, patternId: 'A-recovery', source: 'auto', trainingExampleId: null },
  trailingSettings,
  BASE - 1_000,
);
const autoCandles = [
  minute(0, { open: 100, high: 105, low: 95, close: 100.1 }),
  minute(1, { open: 100.1, high: 100.2, low: 99.4, close: 99.6 }),
  minute(2, { open: 99.6, high: 100.4, low: 99.5, close: 100.3 }),
  minute(3, { open: 101.5, high: 102, low: 100.5, close: 100.8 }),
];
const autoReplay = replayCampaignCandles(autoCampaign, autoCandles, trailingSettings, {
  afterMs: BASE + 30_000,
});
assert.ok(autoReplay.events.some((event) => event.type === 'trailing_armed'));
assert.ok(autoReplay.events.some((event) => event.type === 'trailing_raised'));
assert.equal(autoReplay.close.reason, 'reclaim_trailing_stop');
assert.equal(autoReplay.close.price, autoCampaign.trailStop, 'recovered auto stop exits at the stored stop');

const expiring = createCampaign(
  'ETHUSDT',
  { ...pattern, patternId: 'M-expiring' },
  { ...settings, maxHours: 1 },
  BASE,
);
const expiryReplay = replayCampaignCandles(
  expiring,
  [minute(61, { open: 101, high: 101.2, low: 100.8, close: 101.1 })],
  { ...settings, maxHours: 1 },
  { afterMs: BASE + 60 * 60_000 },
);
assert.equal(expiryReplay.expiredWithoutFill, true);
assert.equal(expiring.qty, 0);

const riskSettings = {
  ...settings,
  startingBalance: 100,
  leverage: 4,
  maintenanceMargin: 0.005,
};
const btcRisk = createCampaign('BTCUSDT', pattern, { ...riskSettings, symbolNotional: 400 }, BASE);
const ethRisk = createCampaign('ETHUSDT', pattern, { ...riskSettings, symbolNotional: 400 }, BASE);
for (const campaign of [btcRisk, ethRisk]) {
  processCampaignQuote(campaign, { bid: 98, ask: 98 }, riskSettings, BASE + 1);
}
const healthy = paperPortfolioSnapshot(
  { BTCUSDT: btcRisk, ETHUSDT: ethRisk },
  { BTCUSDT: 98, ETHUSDT: 98 },
  riskSettings,
);
const liquidating = paperPortfolioSnapshot(
  { BTCUSDT: btcRisk, ETHUSDT: ethRisk },
  { BTCUSDT: 80, ETHUSDT: 80 },
  riskSettings,
);
assert.ok(healthy.equity > healthy.maintenance, 'portfolio is healthy at the recovery boundary');
assert.ok(liquidating.equity <= liquidating.maintenance, 'synchronized recovered prices detect global liquidation');
assert.equal(
  liquidating.notional,
  (btcRisk.qty + ethRisk.qty) * 80,
  'maintenance and margin use current recovered notional',
);

console.log('Paper recovery: repeating L1, deep target finish, legacy trailing and cursor checks passed');
