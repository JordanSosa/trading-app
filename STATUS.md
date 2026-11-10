# BTC/USDT paper trading - RUNNING

> **Paper money only. No real orders are placed.** Updated 2026-11-10T10:30:19Z - data source `binance_vision` - run `run-20261106T0047Z`

| Metric | Value |
|---|---|
| Equity | **9,977.95 USDT** (-0.22%) |
| P&L | -22.05 USDT (realised 0.00, unrealised -24.56) |
| Buy & hold since start | -2.66% (strategy vs B&H: +2.44%) |
| BTC price | 74,310.63 (start 76,340.00) |
| Position | short 0.10488 BTC = 7,793.70 USDT (-78.1% exposure, hybrid) |
| Cash | 17,771.65 USDT |
| Max drawdown | 1.74% (current 0.45%) |
| Fees paid | 3.89 USDT - funding paid -2.51 USDT (negative = received) |
| Trades / round trips | 1 / 0 (win rate n/a) |
| Run window | 2026-11-06T00:47:35Z -> 2026-12-06T00:00:00Z (day 4.4 of 30) |
| Strategy | `lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004)` |

![equity curve](equity.svg)

## Recent fills

| Time (UTC) | Side | Qty BTC | Fill price | Notional | Fee | Realised P&L | Reason |
|---|---|---:|---:|---:|---:|---:|---|
| 2026-11-08T22:00:00Z | sell | 0.10488 | 74,113.53 | 7,773.03 | 3.89 | 0.00 | signal |

## Events

- `2026-11-06T00:47:35Z` **info** - paper run started: lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004), 10,000.00 USDT, 30 days, data via binance_vision

## Health

- Last tick: 2026-11-10T10:30:19Z (ticks run: 21)
- Last successful tick: 2026-11-10T10:30:19Z
- Last closed bar processed: 2026-11-10T10:00:00Z
- Consecutive failures: 0

Ledgers: [trades.csv](trades.csv) - [trips.csv](trips.csv) - [equity.csv](equity.csv) - [events.jsonl](events.jsonl) - [state.json](state.json)
