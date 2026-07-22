# GalkaLive production readiness report

Дата аудита: 2026-07-22  
Ветка: `agent/galka-live-hardening-v3`  
Проверенный кодовый диапазон: `d5a5256..4cf2603`

## Итоговое решение

| Контур | Решение | Причина |
|---|---|---|
| Paper terminal | **GO** | Детерминированный replay/reconnect, portfolio recovery, безопасный restore и browser regression прошли. |
| Hyperliquid READ ONLY | **GO** | Онлайн-проверка ранее подтверждена как `READ-ONLY CHECK PASS`; все exchange-write методы дополнительно закрыты абсолютным LIVE-off barrier. |
| Release candidate в локальной ветке | **GO** | Полная матрица тестов и чистая установка с нуля прошли. |
| Публикация release candidate | **BLOCKED EXTERNALLY** | В этой среде нет `gh` и GitHub HTTPS credentials; локальная серия ещё не отправлена. |
| Реальные деньги / LIVE | **NO-GO (намеренно)** | LIVE не включался и не должен включаться до push, зелёных удалённых Actions и контролируемого минимального rollout по инструкции. |

Торговые команды в ходе аудита не отправлялись. API-ключи и содержимое секретных файлов не
изменялись. `main` не изменялся, merge и force-push не выполнялись.

## Проверка требований

| № | Область | Статус и подтверждение |
|---:|---|---|
| 1 | Полный анализ репозитория | PASS — проверены 8 workflows, `scripts/`, `live/`, `terminal/`, research, tests, installer и документация. |
| 2 | TODO/FIXME/DEBUG/заглушки | PASS — actionable-маркеров нет; найденные `pass` относятся к exception/compatibility классам и контролируемым cleanup-ветвям. |
| 3 | GitHub workflows | PASS локально — immutable SHA action pins, явные permissions, concurrency и timeout; `actionlint` PASS. |
| 4 | `scripts/` | PASS — `bash -n`, ShellCheck 0.11.0, installer/update/rollback и security contracts. |
| 5 | `live/` | PASS — 65 тестов на Python 3.12 и 65 на Python 3.14. |
| 6 | `terminal/` | PASS — статические, state migration, responsive, offline/XSS и Chromium gates. |
| 7 | Paper recovery | PASS — точная закрытая 1m последовательность, idempotency и глобальная cross-symbol liquidation. |
| 8 | Hyperliquid | PASS — SDK `0.24.0`, полный runtime lock, response/status contract и отдельный API Wallet. |
| 9 | Reconnect | PASS — durable cursor, gap retry, duplicate guard и синхронный portfolio replay. |
| 10 | Секреты | PASS — config/state вне Git, regular-file и mode checks, browser isolation, history scanner. |
| 11 | Приватные ключи в Git | PASS — filename/content scanner для staged, tracked и всей reachable history; pre-commit и CI gate. |
| 12 | Исправления | PASS — все найденные code/security/recovery/workflow дефекты исправлены логическими коммитами. |
| 13 | Все тесты | PASS локально — см. матрицу ниже. |
| 14 | Падающие тесты | PASS — после исправлений локальных падений нет. |
| 15 | Зелёный CI | PASS для локального эквивалента; удалённый статус новых коммитов ожидает push. |
| 16 | Сборка с нуля | PASS — новый clone, `npm ci`, новые LIVE/research venv и полный набор проверок. |
| 17 | Installer | PASS — чистая установка нужной ветки, secret-history gate, LIVE по умолчанию OFF. |
| 18 | Rollback | PASS — отдельная rollback-ветка, pre-rollback backup, state restore, config не меняется. |
| 19 | Crash recovery | PASS — atomic `fsync`, backup/temp recovery, corrupt quarantine, process lock. |
| 20 | SAFE MODE | PASS — corrupt/orphan/reconcile/monitor failures включают SAFE MODE; снять его можно только после чистой сверки. |

## Основные исправления

- Все Hyperliquid writes требуют одновременно `HL_LIVE_ENABLED=YES` и
  `HL_LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY`; при LIVE OFF даже startup reconcile строго read-only.
- Основной кошелёк не может использоваться как signer: разрешён только отдельный approved API
  Wallet, связанный с указанным account address.
- State сохраняется атомарно с `fsync`, mode `0600`, предыдущей копией и восстановлением из
  завершённого crash-temp только через SAFE MODE.
- Paper reconnect воспроизводит закрытые 1m свечи хронологически по общему BTC/ETH/SOL timeline и
  не пропускает portfolio-level liquidation.
- Browser snapshot/workspace import ограничен по размеру, глубине и количеству узлов; запрещены
  prototype-pollution keys, нечисловые значения и опасные атрибуты.
- LIVE и legacy terminal работают без runtime CDN. Paper launcher отдаёт только `terminal/` и
  проверенные `results/`, не корень репозитория.
- Binance research archives принимаются только с официальным SHA-256; ZIP traversal, ambiguity,
  encryption, compression bombs и oversized payloads отклоняются. Cached dataset привязан к
  self-hashed manifest по SHA-256, размеру и числу строк.
- LIVE lock содержит 25 runtime + 4 tooling packages; research lock содержит полный closure из 15
  пакетов; browser regression tree закреплён `package-lock.json` и устанавливается через `npm ci`.
- Generated research data больше не может напрямую менять `main`: BTC rebuild artifact-only, Lab
  publisher ограничен `agent/galka-statistics-engine`.

## Финальная матрица тестов

| Проверка | Результат |
|---|---:|
| LIVE Python 3.12 | 65/65 PASS |
| LIVE Python 3.14 | 65/65 PASS |
| Research unit | 21/21 PASS |
| Research synthetic E2E (30 дней) | PASS, 2 197 событий, manifest/dataset hash verified |
| `npm test` | PASS |
| Galka Pro static/architecture | 49 checks PASS |
| LIVE terminal | 25 checks PASS |
| Mobile terminal | 15 checks PASS |
| Backtest page | 15 checks PASS |
| Paper reconnect Chromium | PASS |
| Legacy offline/XSS Chromium | PASS |
| Visual regression capture | PASS, 9 PNG, S24/landscape/desktop |
| Installer/update/rollback | PASS |
| Secret scan staged/tracked/history | PASS |
| Workflow audit | 8/8 PASS |
| actionlint 1.7.12 | PASS |
| ShellCheck 0.11.0 | PASS |
| Чистый clone + locked dependency install | PASS |

## Security и recovery invariants

- LIVE default: `HL_LIVE_ENABLED=NO`, `HL_LIVE_CONFIRM=NOT_CONFIRMED`.
- Config: вне репозитория, не symlink, владелец текущий пользователь, mode `0600`.
- Data directory: вне репозитория, не symlink, mode `0700`; state files `0600`.
- Local LIVE API: loopback-only, случайный session token, Host/Origin/Sec-Fetch-Site checks и CSP.
- Exchange reads могут повторяться; торговые команды автоматически не повторяются.
- Необъяснимый fill, short/orphan position, чужой order, corrupt state или неуспешная сверка
  блокируют дальнейшие действия через recovery/SAFE MODE.
- Service worker кэширует только UI shell и никогда не подменяет market data.
- Workflow tokens read-only по умолчанию; все внешние Actions закреплены полными commit SHA.

## Оставшиеся release gates

1. Установить и авторизовать GitHub CLI (`gh auth login`) либо настроить штатный Git credential
   helper для `jonicbecks-lab/Botizvideo`.
2. Выполнить обычный, не force push:
   `git push origin agent/galka-live-hardening-v3`.
3. Дождаться зелёного результата всех применимых GitHub Actions на фактически опубликованном HEAD.
4. До этого сохранить LIVE OFF. После публикации повторить read-only account check.
5. Реальный rollout разрешать только вручную: отдельный API Wallet, чистые BTC/ETH/SOL
   positions/orders, минимальный notional, проверка каждого exchange-side order/TP и немедленная
   остановка при SAFE MODE/recovery.

Удалённый CI для новых коммитов нельзя честно объявить зелёным до выполнения пунктов 1–3. Это
единственный незакрытый технический release gate; локальная production matrix полностью зелёная.
