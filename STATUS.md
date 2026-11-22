# BTC/USDT paper trading - RUNNING

> **Paper money only. No real orders are placed.** Updated 2026-11-22T09:54:24Z - data source `binance_vision` - run `run-20261106T0047Z`

| Metric | Value |
|---|---|
| Equity | **11,431.73 USDT** (+14.32%) |
| P&L | 1,431.73 USDT (realised 0.00, unrealised 1,424.12) |
| Buy & hold since start | -20.23% (strategy vs B&H: +34.55%) |
| BTC price | 60,892.74 (start 76,340.00) |
| Position | short 0.16613 BTC = 10,116.11 USDT (-88.5% exposure, hybrid) |
| Cash | 21,547.84 USDT |
| Max drawdown | 6.45% (current 6.90%) |
| Fees paid | 5.77 USDT - funding paid -7.61 USDT (negative = received) |
| Trades / round trips | 4 / 0 (win rate n/a) |
| Run window | 2026-11-06T00:47:35Z -> 2026-12-06T00:00:00Z (day 16.4 of 30) |
| Strategy | `lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004)` |

![equity curve](equity.svg)

## Recent fills

| Time (UTC) | Side | Qty BTC | Fill price | Notional | Fee | Realised P&L | Reason |
|---|---|---:|---:|---:|---:|---:|---|
| 2026-11-21T04:00:00Z | sell | 0.02586 | 56,723.41 | 1,466.87 | 0.73 | 0.00 | signal |
| 2026-11-15T02:00:00Z | sell | 0.01899 | 62,606.20 | 1,188.89 | 0.59 | 0.00 | signal |
| 2026-11-13T06:00:00Z | sell | 0.01640 | 68,123.29 | 1,117.22 | 0.56 | 0.00 | signal |
| 2026-11-08T22:00:00Z | sell | 0.10488 | 74,113.53 | 7,773.03 | 3.89 | 0.00 | signal |

## Events

- `2026-11-06T00:47:35Z` **info** - paper run started: lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004), 10,000.00 USDT, 30 days, data via binance_vision

## Health

- Last tick: 2026-11-22T09:54:24Z (ticks run: 64)
- Last successful tick: 2026-11-22T09:54:24Z
- Last closed bar processed: 2026-11-22T09:00:00Z
- Consecutive failures: 0

Ledgers: [trades.csv](trades.csv) - [trips.csv](trips.csv) - [equity.csv](equity.csv) - [events.jsonl](events.jsonl) - [state.json](state.json)
