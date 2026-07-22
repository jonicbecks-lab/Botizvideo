export const LEGACY_DEPTHS = [0.25, 0.7, 1.25, 1.9, 2.65, 3.5];
export const LEGACY_WEIGHTS = [0.05, 0.09, 0.14, 0.18, 0.24, 0.3];
export const MANUAL_DENSE_DEPTHS = [0.15, 0.3, 0.45, 0.6, 0.9, 1.2, 1.5, 2.0];
export const MANUAL_DENSE_WEIGHTS = [0.42, 0.22, 0.12, 0.08, 0.06, 0.04, 0.03, 0.03];
export const ACTIVE_CAMPAIGN_STATUSES = new Set(['waiting', 'open', 'trailing']);
export const RECOVERY_CANDLE_INTERVAL_MS = 60_000;
export const RECOVERY_PATH_POLICY = 'directional-ohlc-v1';

const number = (value, fallback = 0) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

export function campaignLadder(settings, pattern) {
  if (pattern.source === 'manual') {
    return {
      depths: MANUAL_DENSE_DEPTHS.slice(),
      weights: MANUAL_DENSE_WEIGHTS.slice(),
    };
  }
  return { depths: LEGACY_DEPTHS.slice(), weights: LEGACY_WEIGHTS.slice() };
}

export function createCampaign(symbol, pattern, settings, nowMs = Date.now()) {
  const maxNotional = settings.symbolNotional;
  const { depths, weights } = campaignLadder(settings, pattern);
  const manualTarget = pattern.source === 'manual';
  return {
    campaignId: `C-${symbol}-${nowMs}`,
    symbol,
    patternId: pattern.patternId,
    source: pattern.source || 'auto',
    trainingExampleId: pattern.trainingExampleId || null,
    status: 'waiting',
    vLow: pattern.vLow,
    target: pattern.vLow,
    createdAt: new Date(nowMs).toISOString(),
    expiresAt: nowMs + settings.maxHours * 3_600_000,
    exitMode: manualTarget ? 'target' : settings.exitMode || 'trail',
    reclaimPrice: manualTarget
      ? pattern.vLow
      : pattern.vLow * (1 + number(settings.reclaimBufferPct, 0.1) / 100),
    trailArmed: false,
    trailHigh: null,
    trailStop: null,
    trailActivatedAt: null,
    l1Cycles: 0,
    l1CycleRealizedPnl: 0,
    levels: depths.map((depthPct, index) => ({
      index: index + 1,
      depthPct,
      weight: weights[index],
      price: pattern.vLow * (1 - depthPct / 100),
      notional: maxNotional * weights[index],
      status: 'pending',
      fillPrice: null,
      fillTime: null,
      qty: 0,
      fee: 0,
    })),
    qty: 0,
    filledNotional: 0,
    averageEntry: null,
    entryFees: 0,
    unrealizedPnl: 0,
  };
}

export function recalculateCampaign(campaign) {
  const filled = campaign.levels.filter((level) => level.status === 'filled');
  campaign.qty = filled.reduce((sum, level) => sum + number(level.qty), 0);
  campaign.filledNotional = filled.reduce(
    (sum, level) => sum + number(level.fillPrice) * number(level.qty),
    0,
  );
  campaign.averageEntry = campaign.qty ? campaign.filledNotional / campaign.qty : null;
  campaign.entryFees = filled.reduce((sum, level) => sum + number(level.fee), 0);
  return campaign;
}

function closeAndRearmL1(campaign, settings, nowMs) {
  const level = campaign.levels.find((item) => item.status === 'filled');
  if (!level || level.index !== 1) return null;

  const exitPrice = campaign.target;
  const qty = campaign.qty;
  const averageEntry = campaign.averageEntry;
  const filledNotional = campaign.filledNotional;
  const entryFees = campaign.entryFees;
  const exitNotional = qty * exitPrice;
  const exitFee = exitNotional * number(settings.makerFee);
  const grossPnl = qty * (exitPrice - averageEntry);
  const netPnl = grossPnl - entryFees - exitFee;
  const cycle = number(campaign.l1Cycles) + 1;

  const event = {
    type: 'l1_cycle_closed',
    cycle,
    price: exitPrice,
    qty,
    averageEntry,
    filledNotional,
    entryFees,
    exitFee,
    grossPnl,
    netPnl,
    fillTime: level.fillTime,
    exitTime: new Date(nowMs).toISOString(),
  };

  campaign.l1Cycles = cycle;
  campaign.l1CycleRealizedPnl = number(campaign.l1CycleRealizedPnl) + netPnl;
  level.status = 'pending';
  level.fillPrice = null;
  level.fillTime = null;
  level.qty = 0;
  level.fee = 0;
  campaign.status = 'waiting';
  campaign.unrealizedPnl = 0;
  recalculateCampaign(campaign);
  return event;
}

export function moveManualCampaign(campaign, nextLevel, settings) {
  if (
    !campaign ||
    campaign.source !== 'manual' ||
    campaign.qty ||
    campaign.levels.some((level) => level.status === 'filled')
  ) {
    return false;
  }
  campaign.vLow = nextLevel;
  campaign.target = nextLevel;
  campaign.exitMode = 'target';
  campaign.reclaimPrice = nextLevel;
  campaign.trailArmed = false;
  campaign.trailHigh = null;
  campaign.trailStop = null;
  campaign.trailActivatedAt = null;
  for (const level of campaign.levels) {
    level.price = nextLevel * (1 - level.depthPct / 100);
  }
  return true;
}

export function processCampaignQuote(campaign, quote, settings, nowMs = Date.now()) {
  const result = { changed: false, events: [], close: null, expiredWithoutFill: false };
  if (!campaign || !ACTIVE_CAMPAIGN_STATUSES.has(campaign.status)) return result;
  if (!(quote?.bid > 0) || !(quote?.ask > 0)) return result;
  if (nowMs >= number(campaign.expiresAt, Infinity)) {
    if (campaign.qty) result.close = { price: quote.bid, reason: 'time_exit' };
    else result.expiredWithoutFill = true;
    return result;
  }
  const fillTime = new Date(nowMs).toISOString();

  if (!campaign.trailArmed) {
    for (const level of campaign.levels) {
      if (level.status !== 'pending' || quote.ask > level.price) continue;
      level.status = 'filled';
      level.fillPrice = level.price;
      level.fillTime = fillTime;
      level.qty = level.notional / level.fillPrice;
      level.fee = level.notional * settings.makerFee;
      campaign.status = 'open';
      result.changed = true;
      result.events.push({ type: 'level_filled', level: level.index, price: level.fillPrice });
    }
  }

  recalculateCampaign(campaign);
  if (campaign.qty) {
    const mode = campaign.exitMode || settings.exitMode || 'trail';
    if (mode === 'target') {
      if (quote.bid >= campaign.target) {
        const filled = campaign.levels.filter((level) => level.status === 'filled');
        const repeatL1 =
          campaign.source === 'manual' &&
          filled.length === 1 &&
          filled[0].index === 1;
        if (repeatL1) {
          const event = closeAndRearmL1(campaign, settings, nowMs);
          if (event) {
            result.changed = true;
            result.events.push(event);
          }
        } else {
          result.close = { price: campaign.target, reason: 'v_low_target' };
        }
      }
    } else {
      const reclaimPrice =
        campaign.reclaimPrice ||
        campaign.vLow * (1 + number(settings.reclaimBufferPct, 0.1) / 100);
      if (!campaign.trailArmed && quote.bid >= reclaimPrice) {
        campaign.trailArmed = true;
        campaign.status = 'trailing';
        campaign.trailHigh = quote.bid;
        campaign.trailStop = campaign.vLow;
        campaign.trailActivatedAt = fillTime;
        campaign.expiresAt = nowMs + settings.maxHours * 3_600_000;
        result.changed = true;
        result.events.push({ type: 'trailing_armed', stop: campaign.trailStop });
      }
      if (campaign.trailArmed) {
        const previousHigh = number(campaign.trailHigh, quote.bid);
        const nextHigh = Math.max(previousHigh, quote.bid);
        if (nextHigh > previousHigh) {
          campaign.trailHigh = nextHigh;
          result.changed = true;
        }
        const distance = clamp(number(settings.trailDistancePct, 0.75), 0.05, 10) / 100;
        const nextStop = Math.max(campaign.vLow, nextHigh * (1 - distance));
        if (nextStop > number(campaign.trailStop, campaign.vLow)) {
          campaign.trailStop = nextStop;
          result.changed = true;
          result.events.push({ type: 'trailing_raised', stop: campaign.trailStop });
        }
        if (quote.bid <= campaign.trailStop) {
          result.close = { price: quote.bid, reason: 'reclaim_trailing_stop' };
        }
      }
    }
  }

  return result;
}

function normalizedRecoveryCandle(candle) {
  const openTime = number(candle?.openTime, number(candle?.time) * 1_000);
  const closeTime = number(candle?.closeTime, openTime + RECOVERY_CANDLE_INTERVAL_MS - 1);
  const open = number(candle?.open);
  const high = number(candle?.high);
  const low = number(candle?.low);
  const close = number(candle?.close);
  if (
    !(openTime >= 0) ||
    !(closeTime > openTime) ||
    ![open, high, low, close].every((x) => x > 0) ||
    high < Math.max(open, close) ||
    low > Math.min(open, close) ||
    high < low
  ) {
    return null;
  }
  return { openTime, closeTime, open, high, low, close };
}

export function validateRecoveryCandleRange(candles, startMs, endMs) {
  if (!(endMs > startMs)) return [];
  let firstOpen = Math.floor(startMs / RECOVERY_CANDLE_INTERVAL_MS) * RECOVERY_CANDLE_INTERVAL_MS;
  if (firstOpen + RECOVERY_CANDLE_INTERVAL_MS - 1 <= startMs) {
    firstOpen += RECOVERY_CANDLE_INTERVAL_MS;
  }
  const lastOpen = Math.floor(endMs / RECOVERY_CANDLE_INTERVAL_MS) * RECOVERY_CANDLE_INTERVAL_MS;
  if (lastOpen < firstOpen) return [];

  const byOpenTime = new Map();
  for (const source of Array.isArray(candles) ? candles : []) {
    const candle = normalizedRecoveryCandle(source);
    if (!candle || candle.closeTime <= startMs || candle.closeTime > endMs) continue;
    const previous = byOpenTime.get(candle.openTime);
    if (previous && JSON.stringify(previous) !== JSON.stringify(candle)) {
      throw new Error(`Conflicting recovery candle at ${candle.openTime}`);
    }
    byOpenTime.set(candle.openTime, candle);
  }

  const expected = Math.floor((lastOpen - firstOpen) / RECOVERY_CANDLE_INTERVAL_MS) + 1;
  if (byOpenTime.size !== expected) {
    throw new Error(`Incomplete recovery candles: expected ${expected}, received ${byOpenTime.size}`);
  }
  const output = [];
  for (let openTime = firstOpen; openTime <= lastOpen; openTime += RECOVERY_CANDLE_INTERVAL_MS) {
    const candle = byOpenTime.get(openTime);
    if (!candle) throw new Error(`Missing recovery candle at ${openTime}`);
    if (candle.closeTime !== openTime + RECOVERY_CANDLE_INTERVAL_MS - 1) {
      throw new Error(`Invalid recovery close time at ${openTime}`);
    }
    output.push(candle);
  }
  return output;
}

export function recoveryCandlePath(candle, afterMs = -Infinity) {
  const normalized = normalizedRecoveryCandle(candle);
  if (!normalized || normalized.closeTime <= afterMs) return [];

  // The first candle can overlap the last known live quote. Its earlier high/low may predate the
  // disconnect, so only its close is safe to replay. Every fully missed candle uses a fixed,
  // documented directional OHLC path.
  if (normalized.openTime < afterMs) {
    return [
      {
        atMs: normalized.closeTime,
        price: normalized.close,
        phase: 'boundary_close',
        boundary: true,
        candleOpenTime: normalized.openTime,
        candleCloseTime: normalized.closeTime,
      },
    ];
  }

  const bullish = normalized.close >= normalized.open;
  const prices = bullish
    ? [normalized.open, normalized.low, normalized.high, normalized.close]
    : [normalized.open, normalized.high, normalized.low, normalized.close];
  const phases = bullish
    ? ['open', 'low', 'high', 'close']
    : ['open', 'high', 'low', 'close'];
  const span = normalized.closeTime - normalized.openTime;
  const times = [
    normalized.openTime + 1,
    normalized.openTime + Math.floor(span / 3),
    normalized.openTime + Math.floor((span * 2) / 3),
    normalized.closeTime,
  ];
  return prices.map((price, index) => ({
    atMs: times[index],
    price,
    phase: phases[index],
    boundary: false,
    candleOpenTime: normalized.openTime,
    candleCloseTime: normalized.closeTime,
  }));
}

export function replayCampaignCandles(
  campaign,
  candles,
  settings,
  { afterMs = -Infinity } = {},
) {
  const result = {
    changed: false,
    events: [],
    close: null,
    expiredWithoutFill: false,
    candlesReplayed: 0,
    boundaryCandles: 0,
    lastCloseTime: null,
    lastEventAt: null,
    policy: RECOVERY_PATH_POLICY,
  };
  if (!campaign || !ACTIVE_CAMPAIGN_STATUSES.has(campaign.status)) return result;

  const ordered = (Array.isArray(candles) ? candles : [])
    .map(normalizedRecoveryCandle)
    .filter(Boolean)
    .sort((a, b) => a.openTime - b.openTime);

  for (const candle of ordered) {
    const path = recoveryCandlePath(candle, afterMs);
    if (!path.length) continue;
    result.candlesReplayed += 1;
    result.boundaryCandles += path[0].boundary ? 1 : 0;
    result.lastCloseTime = candle.closeTime;

    for (const point of path) {
      const stopBefore = number(campaign.trailStop, 0);
      const step = processCampaignQuote(
        campaign,
        { bid: point.price, ask: point.price },
        settings,
        point.atMs,
      );
      result.changed = result.changed || step.changed;
      result.lastEventAt = point.atMs;
      result.events.push(
        ...step.events.map((event) => ({
          ...event,
          atMs: point.atMs,
          recovered: true,
          phase: point.phase,
          candleOpenTime: point.candleOpenTime,
        })),
      );

      if (step.close) {
        const stop = stopBefore || number(campaign.trailStop, 0);
        result.close = {
          ...step.close,
          price:
            step.close.reason === 'reclaim_trailing_stop' && stop > 0
              ? stop
              : step.close.price,
          atMs: point.atMs,
          recovered: true,
          phase: point.phase,
          candleOpenTime: point.candleOpenTime,
        };
        return result;
      }
      if (step.expiredWithoutFill) {
        result.expiredWithoutFill = true;
        return result;
      }
    }
  }
  return result;
}

export function previewCampaign(level, settings) {
  const pattern = { patternId: 'preview', source: 'manual', vLow: level };
  const campaign = createCampaign('PREVIEW', pattern, settings, 0);
  const totalNotional = campaign.levels.reduce((sum, item) => sum + item.notional, 0);
  const totalQty = campaign.levels.reduce((sum, item) => sum + item.notional / item.price, 0);
  const averageEntry = totalQty ? totalNotional / totalQty : null;
  const grossAtGalka = totalQty * (level - averageEntry);
  const entryFees = totalNotional * settings.makerFee;
  const exitFees = totalQty * level * settings.makerFee;
  return {
    first: campaign.levels[0],
    last: campaign.levels.at(-1),
    count: campaign.levels.length,
    totalNotional,
    averageEntry,
    estimatedPnlAtGalka: grossAtGalka - entryFees - exitFees,
  };
}
