# SUPER MODEL · PAPER CONTROL · Termux V1

Это отдельное PAPER-приложение. Реальных ордеров оно не отправляет и API-ключи биржи не нужны.

## Что уже заложено

- два независимых PAPER-счёта: **BTC $1000** и **ETH $1000**;
- SUPER adaptive **60D**: 25 признаков, ensemble 68/20/12, EWMA 58/42, LONG/SHORT/NEUTRAL state machine;
- сигнал считается по **закрытым 2H свечам** Binance spot/perp + premium + funding;
- исполнение PAPER по **закрытым 5m свечам**;
- intrabar path как в backtest: green `O→L→H→C`, red `O→H→L→C`;
- риск на новую кампанию = **10% текущего equity**;
- плечо 10×;
- риск кампании делится на **11 равных частей**: initial + 10 grid;
- grid по 1% против позиции;
- максимум 11 траншей;
- NEUTRAL снимает незаполненный grid, но держит набранную позицию;
- противоположный сигнал закрывает позицию и разворачивает;
- step profit lock: +10%, +20%, +30% ROE и далее;
- после AUTO profit-stop новая кампания открывается только если модель всё ещё в том же направлении;
- состояние сохраняется в `paper_super/data/paper_state.json`;
- все ручные действия пишутся в журнал вместе со snapshot депозита/позиции.

## Ручное вмешательство

Кнопки: `LONG + GRID`, `SHORT + GRID`, `CLOSE`, `CANCEL GRID`, `RESTORE GRID`, `ADD 1 TRANCHE`, `RETURN AUTO`, `AUTO ON/OFF`.

Любое ручное торговое действие включает **MANUAL OVERRIDE**. В этом режиме модель продолжает считаться и отображаться, но автомат не отменяет твоё ручное решение. `RETURN AUTO` возвращает управление модели.

Это сделано специально, чтобы потом разобрать журнал и понять, какие твои ручные действия систематически улучшают результат.

## Установка в Termux

```bash
pkg update
pkg install python git -y
git clone -b app/super-paper-termux-v1 https://github.com/jonicbecks-lab/Botizvideo.git
cd Botizvideo/paper_super
pip install -r requirements.txt
python app.py
```

Открыть на телефоне:

```text
http://127.0.0.1:8765
```

Пока Termux-процесс работает, PAPER engine продолжает исполнять модель. После остановки состояние сохраняется. При следующем запуске закрытые 5m свечи догоняются с последней обработанной свечи.

## Важно

Это forward PAPER, а не LIVE. Не добавлять API keys и не подключать реальные ордера до отдельного этапа после проверки журнала ручных вмешательств.
