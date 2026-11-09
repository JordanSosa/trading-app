# BTC/USDT paper trading - RUNNING

> **Paper money only. No real orders are placed.** Updated 2026-11-09T09:37:50Z - data source `binance_vision` - run `run-20261106T0047Z`

| Metric | Value |
|---|---|
| Equity | **9,967.19 USDT** (-0.33%) |
| P&L | -32.81 USDT (realised 0.00, unrealised -33.72) |
| Buy & hold since start | -2.54% (strategy vs B&H: +2.22%) |
| BTC price | 74,398.00 (start 76,340.00) |
| Position | short 0.10488 BTC = 7,802.86 USDT (-78.3% exposure, hybrid) |
| Cash | 17,770.05 USDT |
| Max drawdown | 1.26% (current 0.55%) |
| Fees paid | 3.89 USDT - funding paid -0.91 USDT (negative = received) |
| Trades / round trips | 1 / 0 (win rate n/a) |
| Run window | 2026-11-06T00:47:35Z -> 2026-12-06T00:00:00Z (day 3.4 of 30) |
| Strategy | `lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004)` |

![equity curve](equity.svg)

## Recent fills

| Time (UTC) | Side | Qty BTC | Fill price | Notional | Fee | Realised P&L | Reason |
|---|---|---:|---:|---:|---:|---:|---|
| 2026-11-08T22:00:00Z | sell | 0.10488 | 74,113.53 | 7,773.03 | 3.89 | 0.00 | signal |

## Events

- `2026-11-06T00:47:35Z` **info** - paper run started: lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004), 10,000.00 USDT, 30 days, data via binance_vision

## Health

- Last tick: 2026-11-09T09:37:50Z (ticks run: 18)
- Last successful tick: 2026-11-09T09:37:50Z
- Last closed bar processed: 2026-11-09T09:00:00Z
- Consecutive failures: 0

Ledgers: [trades.csv](trades.csv) - [trips.csv](trips.csv) - [equity.csv](equity.csv) - [events.jsonl](events.jsonl) - [state.json](state.json)
