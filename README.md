# BTC/USDT paper trading - RUNNING

> **Paper money only. No real orders are placed.** Updated 2026-11-23T13:44:01Z - data source `binance_vision` - run `run-20261106T0047Z`

| Metric | Value |
|---|---|
| Equity | **11,366.31 USDT** (+13.66%) |
| P&L | 1,366.31 USDT (realised 175.73, unrealised 1,182.68) |
| Buy & hold since start | -19.69% (strategy vs B&H: +33.36%) |
| BTC price | 61,305.33 (start 76,340.00) |
| Position | short 0.14494 BTC = 8,885.59 USDT (-78.2% exposure, hybrid) |
| Cash | 20,251.90 USDT |
| Max drawdown | 7.88% (current 7.44%) |
| Fees paid | 6.42 USDT - funding paid -7.90 USDT (negative = received) |
| Trades / round trips | 5 / 0 (win rate n/a) |
| Run window | 2026-11-06T00:47:35Z -> 2026-12-06T00:00:00Z (day 17.5 of 30) |
| Strategy | `lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004)` |

![equity curve](equity.svg)

## Recent fills

| Time (UTC) | Side | Qty BTC | Fill price | Notional | Fee | Realised P&L | Reason |
|---|---|---:|---:|---:|---:|---:|---|
| 2026-11-22T10:00:00Z | buy | 0.02119 | 61,141.32 | 1,295.58 | 0.65 | 175.73 | signal |
| 2026-11-21T04:00:00Z | sell | 0.02586 | 56,723.41 | 1,466.87 | 0.73 | 0.00 | signal |
| 2026-11-15T02:00:00Z | sell | 0.01899 | 62,606.20 | 1,188.89 | 0.59 | 0.00 | signal |
| 2026-11-13T06:00:00Z | sell | 0.01640 | 68,123.29 | 1,117.22 | 0.56 | 0.00 | signal |
| 2026-11-08T22:00:00Z | sell | 0.10488 | 74,113.53 | 7,773.03 | 3.89 | 0.00 | signal |

## Events

- `2026-11-06T00:47:35Z` **info** - paper run started: lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004), 10,000.00 USDT, 30 days, data via binance_vision

## Health

- Last tick: 2026-11-23T13:44:01Z (ticks run: 68)
- Last successful tick: 2026-11-23T13:44:01Z
- Last closed bar processed: 2026-11-23T13:00:00Z
- Consecutive failures: 0

Ledgers: [trades.csv](trades.csv) - [trips.csv](trips.csv) - [equity.csv](equity.csv) - [events.jsonl](events.jsonl) - [state.json](state.json)
