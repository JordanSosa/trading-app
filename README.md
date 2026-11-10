# BTC/USDT paper trading - RUNNING

> **Paper money only. No real orders are placed.** Updated 2026-11-10T23:30:13Z - data source `binance_vision` - run `run-20261106T0047Z`

| Metric | Value |
|---|---|
| Equity | **10,042.53 USDT** (+0.43%) |
| P&L | 42.53 USDT (realised 0.00, unrealised 39.65) |
| Buy & hold since start | -3.46% (strategy vs B&H: +3.89%) |
| BTC price | 73,698.39 (start 76,340.00) |
| Position | short 0.10488 BTC = 7,729.49 USDT (-77.0% exposure, hybrid) |
| Cash | 17,772.02 USDT |
| Max drawdown | 1.74% (current 0.85%) |
| Fees paid | 3.89 USDT - funding paid -2.88 USDT (negative = received) |
| Trades / round trips | 1 / 0 (win rate n/a) |
| Run window | 2026-11-06T00:47:35Z -> 2026-12-06T00:00:00Z (day 4.9 of 30) |
| Strategy | `lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004)` |

![equity curve](equity.svg)

## Recent fills

| Time (UTC) | Side | Qty BTC | Fill price | Notional | Fee | Realised P&L | Reason |
|---|---|---:|---:|---:|---:|---:|---|
| 2026-11-08T22:00:00Z | sell | 0.10488 | 74,113.53 | 7,773.03 | 3.89 | 0.00 | signal |

## Events

- `2026-11-06T00:47:35Z` **info** - paper run started: lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004), 10,000.00 USDT, 30 days, data via binance_vision

## Health

- Last tick: 2026-11-10T23:30:13Z (ticks run: 23)
- Last successful tick: 2026-11-10T23:30:13Z
- Last closed bar processed: 2026-11-10T23:00:00Z
- Consecutive failures: 0

Ledgers: [trades.csv](trades.csv) - [trips.csv](trips.csv) - [equity.csv](equity.csv) - [events.jsonl](events.jsonl) - [state.json](state.json)
