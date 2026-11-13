# BTC/USDT paper trading - RUNNING

> **Paper money only. No real orders are placed.** Updated 2026-11-13T23:48:55Z - data source `binance_vision` - run `run-20261106T0047Z`

| Metric | Value |
|---|---|
| Equity | **10,627.83 USDT** (+6.28%) |
| P&L | 627.83 USDT (realised 0.00, unrealised 620.81) |
| Buy & hold since start | -10.73% (strategy vs B&H: +17.01%) |
| BTC price | 68,148.01 (start 76,340.00) |
| Position | short 0.12128 BTC = 8,264.99 USDT (-77.8% exposure, hybrid) |
| Cash | 18,892.82 USDT |
| Max drawdown | 1.84% (current 0.02%) |
| Fees paid | 4.45 USDT - funding paid -7.02 USDT (negative = received) |
| Trades / round trips | 2 / 0 (win rate n/a) |
| Run window | 2026-11-06T00:47:35Z -> 2026-12-06T00:00:00Z (day 8.0 of 30) |
| Strategy | `lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004)` |

![equity curve](equity.svg)

## Recent fills

| Time (UTC) | Side | Qty BTC | Fill price | Notional | Fee | Realised P&L | Reason |
|---|---|---:|---:|---:|---:|---:|---|
| 2026-11-13T06:00:00Z | sell | 0.01640 | 68,123.29 | 1,117.22 | 0.56 | 0.00 | signal |
| 2026-11-08T22:00:00Z | sell | 0.10488 | 74,113.53 | 7,773.03 | 3.89 | 0.00 | signal |

## Events

- `2026-11-06T00:47:35Z` **info** - paper run started: lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004), 10,000.00 USDT, 30 days, data via binance_vision

## Health

- Last tick: 2026-11-13T23:48:55Z (ticks run: 34)
- Last successful tick: 2026-11-13T23:48:55Z
- Last closed bar processed: 2026-11-13T23:00:00Z
- Consecutive failures: 0

Ledgers: [trades.csv](trades.csv) - [trips.csv](trips.csv) - [equity.csv](equity.csv) - [events.jsonl](events.jsonl) - [state.json](state.json)
