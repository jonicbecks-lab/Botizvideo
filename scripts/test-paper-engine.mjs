import assert from 'node:assert/strict';
import {
  MANUAL_DENSE_DEPTHS,
  MANUAL_DENSE_WEIGHTS,
  createCampaign,
  moveManualCampaign,
  previewCampaign,
  processCampaignQuote,
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
  patternId: 'M-test',
  source: 'manual',
  trainingExampleId: 'M-test',
  vLow: 100,
};

const campaign = createCampaign('BTCUSDT', pattern, settings, 1_000);
assert.equal(campaign.levels.length, 8);
assert.deepEqual(campaign.levels.map((level) => level.depthPct), MANUAL_DENSE_DEPTHS);
assert.deepEqual(campaign.levels.map((level) => level.weight), MANUAL_DENSE_WEIGHTS);
assert.equal(campaign.levels[0].depthPct, 0.15);
assert.equal(campaign.levels.at(-1).depthPct, 2);
assert.equal(campaign.exitMode, 'target');
assert.equal(campaign.target, 100);
assert.equal(campaign.l1Cycles, 0);
assert.ok(Math.abs(campaign.levels.reduce((sum, level) => sum + level.notional, 0) - 400) < 1e-9);
assert.ok(campaign.levels[0].notional > campaign.levels[1].notional);
assert.ok(campaign.levels[1].notional > campaign.levels.at(-1).notional);

const l1Only = createCampaign('BTCUSDT', { ...pattern, patternId: 'M-l1' }, settings, 1_100);
const l1Fill = processCampaignQuote(l1Only, { bid: 99.85, ask: 99.85 }, settings, 2_000);
assert.equal(l1Fill.events.filter((event) => event.type === 'level_filled').length, 1);
assert.equal(l1Only.levels[0].status, 'filled');
const l1Return = processCampaignQuote(l1Only, { bid: 100, ask: 100.01 }, settings, 3_000);
const cycle = l1Return.events.find((event) => event.type === 'l1_cycle_closed');
assert.ok(cycle, 'L1-only return must emit a cycle close');
assert.equal(l1Return.close, null, 'L1-only cycle keeps the GALKA campaign active');
assert.ok(cycle.netPnl > 0);
assert.equal(l1Only.l1Cycles, 1);
assert.ok(l1Only.l1CycleRealizedPnl > 0);
assert.equal(l1Only.status, 'waiting');
assert.equal(l1Only.qty, 0);
assert.equal(l1Only.levels[0].status, 'pending', 'L1 is rearmed after the cycle');
assert.equal(l1Only.levels[1].status, 'pending');

processCampaignQuote(l1Only, { bid: 99.85, ask: 99.85 }, settings, 4_000);
const secondCycleResult = processCampaignQuote(l1Only, { bid: 100, ask: 100.01 }, settings, 5_000);
assert.ok(secondCycleResult.events.some((event) => event.type === 'l1_cycle_closed'));
assert.equal(l1Only.l1Cycles, 2, 'L1 can repeat more than once');

const firstFill = processCampaignQuote(campaign, { bid: 99.55, ask: 99.55 }, settings, 2_000);
assert.equal(firstFill.events.filter((event) => event.type === 'level_filled').length, 3);
assert.equal(campaign.levels.filter((level) => level.status === 'filled').length, 3);
const quantityAfterFill = campaign.qty;

const repeatedQuote = processCampaignQuote(campaign, { bid: 99.55, ask: 99.55 }, settings, 2_000);
assert.equal(repeatedQuote.events.filter((event) => event.type === 'level_filled').length, 0);
assert.equal(campaign.qty, quantityAfterFill, 'the same quote must not fill a level twice');
assert.equal(moveManualCampaign(campaign, 101, settings), false, 'GALKA locks after first fill');

const targetClose = processCampaignQuote(campaign, { bid: 100, ask: 100.01 }, settings, 3_000);
assert.equal(targetClose.close.reason, 'v_low_target');
assert.equal(targetClose.close.price, 100);
assert.equal(
  targetClose.events.some((event) => event.type === 'l1_cycle_closed'),
  false,
  'L2 or deeper fill must finish the whole campaign',
);

const campaigns = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'].map((symbol, index) =>
  createCampaign(symbol, { ...pattern, patternId: `M-${symbol}`, vLow: 100 + index * 10 }, settings, 10_000 + index),
);
for (const item of campaigns) {
  processCampaignQuote(item, { bid: item.vLow * 0.998, ask: item.vLow * 0.998 }, settings, 11_000);
  assert.ok(item.qty > 0, `${item.symbol} is processed independently`);
  assert.equal(item.exitMode, 'target');
}
assert.equal(new Set(campaigns.map((item) => item.campaignId)).size, 3);

const autoPattern = { ...pattern, patternId: 'A-trailing', source: 'auto', trainingExampleId: null };
const trailingSettings = { ...settings, exitMode: 'trail', reclaimBufferPct: 0.1 };
const trailing = createCampaign('SOLUSDT', autoPattern, trailingSettings, 20_000);
processCampaignQuote(trailing, { bid: 98.4, ask: 98.4 }, trailingSettings, 21_000);
assert.ok(trailing.qty > 0);
const armed = processCampaignQuote(trailing, { bid: 100.2, ask: 100.21 }, trailingSettings, 22_000);
assert.equal(trailing.trailArmed, true);
assert.ok(armed.events.some((event) => event.type === 'trailing_armed'));
assert.ok(trailing.trailStop >= trailing.vLow);

processCampaignQuote(trailing, { bid: 103, ask: 103.01 }, trailingSettings, 23_000);
const raisedStop = trailing.trailStop;
assert.ok(raisedStop > trailing.vLow);
processCampaignQuote(trailing, { bid: 102.8, ask: 102.81 }, trailingSettings, 24_000);
assert.equal(trailing.trailStop, raisedStop, 'trailing stop never decreases');
const closeResult = processCampaignQuote(
  trailing,
  { bid: raisedStop - 0.01, ask: raisedStop },
  trailingSettings,
  25_000,
);
assert.equal(closeResult.close.reason, 'reclaim_trailing_stop');

trailing.status = 'closed';
const afterClose = processCampaignQuote(trailing, { bid: 90, ask: 90 }, trailingSettings, 26_000);
assert.equal(afterClose.changed, false);
assert.equal(afterClose.close, null);

const preview = previewCampaign(100, settings);
assert.equal(preview.count, 8);
assert.ok(preview.first.price > preview.last.price);
assert.ok(preview.averageEntry < 100);
assert.ok(preview.estimatedPnlAtGalka > 0);

const expiredBeforeFill = createCampaign(
  'BTCUSDT',
  { ...pattern, patternId: 'M-expired-before-fill' },
  { ...settings, maxHours: 1 },
  1_000,
);
const expiredResult = processCampaignQuote(
  expiredBeforeFill,
  { bid: 98, ask: 98 },
  settings,
  expiredBeforeFill.expiresAt,
);
assert.equal(expiredResult.expiredWithoutFill, true);
assert.equal(expiredBeforeFill.qty, 0, 'an expired ladder must not fill before its time exit');
assert.equal(
  expiredBeforeFill.levels.some((level) => level.status === 'filled'),
  false,
  'no level may fill at or after expiresAt',
);

console.log('Paper engine: repeating L1, deep target finish, dense ladder and legacy trailing checks passed');
