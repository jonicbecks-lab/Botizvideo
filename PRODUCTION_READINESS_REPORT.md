# GalkaLive production readiness report

- Дата финального локального аудита: 2026-07-23
- Ветка: `agent/galka-live-hardening-v3`
- Проверенный кодовый диапазон: `d5a5256..2a998cd`
- Исходный handoff HEAD: `9df8ef279d80ece84b35f2fa1cb662897bd633fc`
- Последний кодовый commit до этого отчёта:
  `2a998cd2ee62ca94b304737fad58633c0a8a5b1d`

## Итоговое решение

| Контур | Решение | Причина |
|---|---|---|
| Paper terminal | **GO** | Детерминированный replay/reconnect, portfolio recovery, безопасный restore и browser regression прошли. |
| Hyperliquid READ ONLY | **GO** | Онлайн-проверка ранее подтверждена как `READ-ONLY CHECK PASS`; все exchange-write методы дополнительно закрыты абсолютным LIVE-off barrier. |
| Release candidate в локальной ветке | **GO** | Полная матрица тестов и чистая установка с нуля прошли. |
| Full-history Git bundle | **GO** | Bundle содержит всю production-ветку, проходит `git bundle verify`, clone и проверку HEAD/tree/status. |
| GitHub publication / remote CI | **NOT PERFORMED** | По текущему заданию GitHub не используется; локальная ветка и bundle являются источником handoff. |
| Реальные деньги / LIVE | **NO-GO (намеренно)** | LIVE не включался; bootstrap и importer завершаются только при точном `HL_LIVE_ENABLED=NO`. |

Торговые команды в ходе аудита не отправлялись. API-ключи и содержимое секретных файлов не
изменялись. `main` не изменялся, merge и force-push не выполнялись.

## Проверка требований

| № | Область | Статус и подтверждение |
|---:|---|---|
| 1 | Полный анализ репозитория | PASS — проверены 8 workflows, `scripts/`, `live/`, `terminal/`, research, tests, installer и документация. |
| 2 | TODO/FIXME/DEBUG/заглушки | PASS — actionable-маркеров нет; найденные `pass` относятся к exception/compatibility классам и контролируемым cleanup-ветвям. |
| 3 | GitHub workflows | PASS локально — immutable SHA action pins, явные permissions, concurrency и timeout; `actionlint` PASS. |
| 4 | `scripts/` | PASS — `bash -n`, ShellCheck 0.11.0, installer/update/rollback, Termux sync, bundle import и security contracts. |
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
| 15 | CI-equivalent | PASS локально; remote Actions намеренно не запускались и не заявляются зелёными. |
| 16 | Сборка с нуля | PASS — новый clone, `npm ci`, новые LIVE/research venv, Python 3.12/3.14 и полный набор проверок. |
| 17 | Installer/bootstrap | PASS — locked dependencies, clean tree, точная ветка, external config и LIVE OFF. |
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
- Termux bootstrap и bundle importer не используют `reset --hard`, `clean -fd`, rebase, force или
  merge без `--ff-only`; старый `CryptoJonic/MeteoraAgent` сохраняется как `legacy-origin`.
- Все LIVE entrypoints используют единый внешний config с поддержкой `GALKA_LIVE_CONFIG` и
  `XDG_CONFIG_HOME`; config внутри репозитория и symlink отклоняются.

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
| Termux sync bootstrap fixtures | PASS — origin/refspec/worktree/branch/dirty/diverged/gh/LIVE OFF |
| Production bundle importer fixtures | PASS — verify/full branch/FF/corrupt/dirty/diverged/LIVE OFF |
| Secret scan staged/tracked/history | PASS |
| Workflow audit | 8/8 PASS |
| actionlint 1.7.12 | PASS |
| ShellCheck 0.11.0 | PASS |
| Чистый clone + locked dependency install | PASS |

## Однокомандный Termux handoff

После доставки bundle в стандартный каталог загрузок Termux:

```bash
cd ~/GalkaLive && bash scripts/import-production-bundle.sh "$HOME/storage/downloads/GalkaLive-agent-galka-live-hardening-v3.bundle"
```

`scripts/import-production-bundle.sh`:

1. Проверяет regular-file bundle через `git bundle verify` и принимает ровно один ref —
   `refs/heads/agent/galka-live-hardening-v3`.
2. Импортирует его во временный ref, проверяет trusted base `d5a5256`, отсутствие merge-коммитов,
   обязательные файлы и executable modes.
3. Требует точную текущую ветку, чистое дерево и доказанный fast-forward от локального HEAD.
4. Выполняет только `git merge --ff-only`, подтверждает точный bundle HEAD и удаляет только свой
   временный ref.
5. Вызывает `scripts/termux-sync-and-prepare-galka.sh --prepare-local <HEAD>`.

`scripts/termux-sync-and-prepare-galka.sh`:

- проверяет `bash`, `git`, `gh`, `python`, exact branch, clean worktree, внешний config и LIVE OFF;
- поддерживает обычный clone и worktree, где `.git` является файлом;
- сохраняет старый `CryptoJonic/MeteoraAgent` как `legacy-origin`, приводит `origin` к
  `https://github.com/jonicbecks-lab/Botizvideo.git`, удаляет старые fetch refspec и оставляет
  стандартный `+refs/heads/*:refs/remotes/origin/*`;
- в online-режиме проверяет `gh auth`, загружает только production ref и делает только
  fast-forward; в bundle-режиме не выполняет сетевой fetch;
- запускает фактический installer с locked dependencies, полный preflight и
  `check-galka-live-account.sh`, который использует только read endpoints;
- требует неизменный чистый HEAD/config и завершает строками `SYNC PASS`, `INSTALL PASS`,
  `PREFLIGHT PASS`, `READ ONLY PASS`, `LIVE OFF`, `ORDERS SENT: NO`, `READY`.

Оба скрипта **не** переключают ветки, не используют reset/clean/rebase/force/обычный merge, не
запускают LIVE-сервер, не включают LIVE, не отправляют ордера и не перезаписывают secret config.
Node/npm не требуются production runtime; Node нужен только для development/CI browser checks.

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

## Известные ограничения и обязательные условия

1. Bundle import предназначен для существующего `~/GalkaLive` на точной production-ветке с чистым
   деревом и общей историей; dirty/diverged/wrong-branch состояния намеренно останавливаются.
2. На устройстве должны быть `git`, `gh` и Python. Для локальной bundle-подготовки gh должен
   запускаться, но GitHub-аутентификация и сеть не нужны; для последующей online-синхронизации
   `gh auth status` обязателен.
3. Внешний local config должен уже существовать, быть regular file и содержать ровно одну строку
   `HL_LIVE_ENABLED=NO`. Его account address и отдельный API Wallet должны быть валидны, иначе
   read-only account check корректно остановит подготовку.
4. Текущий handoff не публикуется в GitHub, поэтому remote Actions для итогового bundle HEAD не
   проверялись и не заявляются PASS. Локальный эквивалент всех применимых проверок зелёный.
5. LIVE остаётся выключен. Реальный rollout не является частью этого handoff и требует отдельного
   явного решения после чистой read-only сверки и проверки SAFE MODE.

Итог аудита: **LIVE OFF**, **READ ONLY**, **ORDERS SENT: NO**.
