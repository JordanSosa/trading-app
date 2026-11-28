# BTC/USDT paper trading - RUNNING

> **Paper money only. No real orders are placed.** Updated 2026-11-28T09:29:38Z - data source `binance_vision` - run `run-20261106T0047Z`

| Metric | Value |
|---|---|
| Equity | **10,991.39 USDT** (+9.91%) |
| P&L | 991.39 USDT (realised 983.49, unrealised 0.00) |
| Buy & hold since start | -21.88% (strategy vs B&H: +31.79%) |
| BTC price | 59,636.85 (start 76,340.00) |
| Position | flat 0.00000 BTC = 0.00 USDT (0.0% exposure, hybrid) |
| Cash | 10,991.39 USDT |
| Max drawdown | 10.49% (current 10.49%) |
| Fees paid | 11.05 USDT - funding paid -7.90 USDT (negative = received) |
| Trades / round trips | 6 / 1 (win rate 100%) |
| Run window | 2026-11-06T00:47:35Z -> 2026-12-06T00:00:00Z (day 22.4 of 30) |
| Strategy | `lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004)` |

![equity curve](equity.svg)

## Recent fills

| Time (UTC) | Side | Qty BTC | Fill price | Notional | Fee | Realised P&L | Reason |
|---|---|---:|---:|---:|---:|---:|---|
| 2026-11-23T14:00:00Z | buy | 0.14494 | 63,860.10 | 9,255.88 | 4.63 | 807.76 | circuit breaker |
| 2026-11-22T10:00:00Z | buy | 0.02119 | 61,141.32 | 1,295.58 | 0.65 | 175.73 | signal |
| 2026-11-21T04:00:00Z | sell | 0.02586 | 56,723.41 | 1,466.87 | 0.73 | 0.00 | signal |
| 2026-11-15T02:00:00Z | sell | 0.01899 | 62,606.20 | 1,188.89 | 0.59 | 0.00 | signal |
| 2026-11-13T06:00:00Z | sell | 0.01640 | 68,123.29 | 1,117.22 | 0.56 | 0.00 | signal |
| 2026-11-08T22:00:00Z | sell | 0.10488 | 74,113.53 | 7,773.03 | 3.89 | 0.00 | signal |

## Events

- `2026-11-25T14:00:00Z` **info** - circuit breaker released; trading resumed
- `2026-11-23T14:00:00Z` **alert** - circuit breaker: drawdown 10.4% from high-water mark; flat for 48h
- `2026-11-06T00:47:35Z` **info** - paper run started: lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004), 10,000.00 USDT, 30 days, data via binance_vision

## Health

- Last tick: 2026-11-28T09:29:38Z (ticks run: 81)
- Last successful tick: 2026-11-28T09:29:38Z
- Last closed bar processed: 2026-11-28T09:00:00Z
- Consecutive failures: 0

Ledgers: [trades.csv](trades.csv) - [trips.csv](trips.csv) - [equity.csv](equity.csv) - [events.jsonl](events.jsonl) - [state.json](state.json)
