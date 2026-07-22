import {
  migrateStore,
  STORE_MAX_BYTES,
  STORE_SCHEMA_VERSION,
  SYMBOLS,
} from './store.js';

export const BACKUP_MAX_BYTES = STORE_MAX_BYTES;
export const WORKSPACE_MAX_BYTES = 4 * 1024 * 1024;

const FORBIDDEN_OBJECT_KEYS = new Set(['__proto__', 'constructor', 'prototype']);
const ACTIVE_CAMPAIGN_STATUSES = new Set(['waiting', 'open', 'trailing']);
const LEVEL_STATUSES = new Set(['pending', 'filled', 'cancelled']);

function bytes(value) {
  return new TextEncoder().encode(value).byteLength;
}

function auditJsonTree(root) {
  const stack = [{ value: root, depth: 0 }];
  let nodes = 0;
  while (stack.length) {
    const { value, depth } = stack.pop();
    nodes += 1;
    if (nodes > 500_000) throw new Error('Snapshot содержит слишком много значений');
    if (depth > 32) throw new Error('Snapshot имеет слишком глубокую структуру');
    if (typeof value === 'number' && !Number.isFinite(value)) {
      throw new Error('Snapshot содержит non-finite число');
    }
    if (typeof value === 'string' && value.length > 65_536) {
      throw new Error('Snapshot содержит слишком длинную строку');
    }
    if (!value || typeof value !== 'object') continue;
    if (Array.isArray(value)) {
      if (value.length > 100_000) throw new Error('Snapshot содержит слишком большой массив');
      for (const item of value) stack.push({ value: item, depth: depth + 1 });
      continue;
    }
    const keys = Object.keys(value);
    if (keys.length > 10_000) throw new Error('Snapshot содержит слишком большой объект');
    for (const key of keys) {
      if (FORBIDDEN_OBJECT_KEYS.has(key)) throw new Error(`Snapshot содержит опасный ключ: ${key}`);
      stack.push({ value: value[key], depth: depth + 1 });
    }
  }
}

function boundedArray(value, name, limit) {
  if (!Array.isArray(value) || value.length > limit) {
    throw new Error(`${name} должен быть массивом не длиннее ${limit}`);
  }
}

function boundedNumber(value, name, minimum, maximum) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${name} находится вне безопасного диапазона`);
  }
}

function optionalBoundedNumber(value, name, minimum, maximum) {
  if (value == null || value === '') return;
  boundedNumber(value, name, minimum, maximum);
}

function validateStoreBounds(store) {
  boundedArray(store.paper.trades, 'paper.trades', 50_000);
  boundedArray(store.ui.alerts, 'ui.alerts', 1_000);
  boundedArray(store.training.manualExamples, 'training.manualExamples', 10_000);
  boundedArray(store.training.radarLabels, 'training.radarLabels', 10_000);
  boundedArray(store.training.replayExamples, 'training.replayExamples', 10_000);
  boundedArray(store.activity, 'activity', 500);
  boundedArray(store.shadow.records, 'shadow.records', 10_000);

  const settings = store.paper.settings;
  boundedNumber(settings.startingBalance, 'startingBalance', 100, 1_000_000_000);
  boundedNumber(settings.leverage, 'leverage', 1, 20);
  boundedNumber(settings.symbolNotional, 'symbolNotional', 50, 10_000);
  boundedNumber(settings.maxHours, 'maxHours', 1, 336);
  boundedNumber(settings.makerFee, 'makerFee', 0, 0.1);
  boundedNumber(settings.takerFee, 'takerFee', 0, 0.1);
  boundedNumber(settings.slippage, 'slippage', 0, 0.1);
  boundedNumber(settings.maintenanceMargin, 'maintenanceMargin', 0, 0.1);

  for (const symbol of SYMBOLS) {
    const campaign = store.paper.symbols[symbol]?.campaign;
    if (!campaign) continue;
    if (!campaign || typeof campaign !== 'object' || Array.isArray(campaign)) {
      throw new Error(`${symbol} campaign должен быть объектом`);
    }
    if (!ACTIVE_CAMPAIGN_STATUSES.has(campaign.status)) {
      throw new Error(`${symbol} campaign имеет неизвестный status`);
    }
    if (!Array.isArray(campaign.levels) || campaign.levels.length > 64) {
      throw new Error(`${symbol} campaign имеет недопустимое число уровней`);
    }
    optionalBoundedNumber(campaign.vLow, `${symbol}.vLow`, Number.MIN_VALUE, Number.MAX_VALUE);
    optionalBoundedNumber(campaign.qty, `${symbol}.qty`, 0, Number.MAX_VALUE);
    optionalBoundedNumber(campaign.filledNotional, `${symbol}.filledNotional`, 0, Number.MAX_VALUE);
    for (const [index, level] of campaign.levels.entries()) {
      if (!level || typeof level !== 'object' || Array.isArray(level)) {
        throw new Error(`${symbol}.levels[${index}] должен быть объектом`);
      }
      if (!LEVEL_STATUSES.has(level.status)) {
        throw new Error(`${symbol}.levels[${index}] имеет неизвестный status`);
      }
      boundedNumber(level.price, `${symbol}.levels[${index}].price`, Number.MIN_VALUE, Number.MAX_VALUE);
      optionalBoundedNumber(level.notional, `${symbol}.levels[${index}].notional`, 0, 10_000);
      optionalBoundedNumber(level.qty, `${symbol}.levels[${index}].qty`, 0, Number.MAX_VALUE);
      optionalBoundedNumber(level.fee, `${symbol}.levels[${index}].fee`, 0, Number.MAX_VALUE);
    }
  }
  return store;
}

export function parseBoundedJson(text, maximumBytes = BACKUP_MAX_BYTES) {
  if (typeof text !== 'string' || bytes(text) > maximumBytes) {
    throw new Error('JSON-файл превышает безопасный лимит');
  }
  const parsed = JSON.parse(text, (key, value) => {
    if (FORBIDDEN_OBJECT_KEYS.has(key)) throw new Error(`JSON содержит опасный ключ: ${key}`);
    if (typeof value === 'number' && !Number.isFinite(value)) {
      throw new Error('JSON содержит non-finite число');
    }
    return value;
  });
  auditJsonTree(parsed);
  return parsed;
}

export function createBackupSnapshot(store, appVersion, createdAt = new Date().toISOString()) {
  return {
    kind: 'galka-pro-snapshot',
    schemaVersion: STORE_SCHEMA_VERSION,
    appVersion,
    createdAt,
    store: JSON.parse(JSON.stringify(store)),
  };
}

export function validateBackupSnapshot(snapshot) {
  auditJsonTree(snapshot);
  if (!snapshot || snapshot.kind !== 'galka-pro-snapshot' || !snapshot.store) {
    throw new Error('Это не полный snapshot Galka Pro');
  }
  if (!snapshot.store.paper?.symbols || !snapshot.store.ui || !snapshot.store.training) {
    throw new Error('В snapshot отсутствуют обязательные разделы');
  }
  return validateStoreBounds(migrateStore(snapshot.store));
}

export function summarizeBackupSnapshot(snapshot) {
  const store = validateBackupSnapshot(snapshot);
  const campaigns = Object.values(store.paper.symbols).filter((item) => item.campaign).length;
  const filledLevels = Object.values(store.paper.symbols).reduce(
    (sum, item) => sum + (item.campaign?.levels || []).filter((level) => level.status === 'filled').length,
    0,
  );
  const drawings = Object.values(store.ui.drawings || {}).reduce(
    (sum, rows) => sum + (Array.isArray(rows) ? rows.length : 0),
    0,
  );
  return {
    createdAt: snapshot.createdAt || null,
    campaigns,
    filledLevels,
    trades: store.paper.trades.length,
    drawings,
    manualExamples: store.training.manualExamples.length,
    radarLabels: store.training.radarLabels.length,
    shadowRecords: store.shadow.records.length,
    shadowEnabled: store.shadow.enabled,
  };
}
