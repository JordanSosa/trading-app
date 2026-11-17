# BTC/USDT paper trading - RUNNING

> **Paper money only. No real orders are placed.** Updated 2026-11-17T09:02:34Z - data source `binance_vision` - run `run-20261106T0047Z`

| Metric | Value |
|---|---|
| Equity | **11,075.18 USDT** (+10.75%) |
| P&L | 1,075.18 USDT (realised 0.00, unrealised 1,066.80) |
| Buy & hold since start | -15.88% (strategy vs B&H: +26.64%) |
| BTC price | 64,213.99 (start 76,340.00) |
| Position | short 0.14027 BTC = 9,007.30 USDT (-81.3% exposure, hybrid) |
| Cash | 20,082.48 USDT |
| Max drawdown | 3.70% (current 1.97%) |
| Fees paid | 5.04 USDT - funding paid -8.38 USDT (negative = received) |
| Trades / round trips | 3 / 0 (win rate n/a) |
| Run window | 2026-11-06T00:47:35Z -> 2026-12-06T00:00:00Z (day 11.3 of 30) |
| Strategy | `lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004)` |

![equity curve](equity.svg)

## Recent fills

| Time (UTC) | Side | Qty BTC | Fill price | Notional | Fee | Realised P&L | Reason |
|---|---|---:|---:|---:|---:|---:|---|
| 2026-11-15T02:00:00Z | sell | 0.01899 | 62,606.20 | 1,188.89 | 0.59 | 0.00 | signal |
| 2026-11-13T06:00:00Z | sell | 0.01640 | 68,123.29 | 1,117.22 | 0.56 | 0.00 | signal |
| 2026-11-08T22:00:00Z | sell | 0.10488 | 74,113.53 | 7,773.03 | 3.89 | 0.00 | signal |

## Events

- `2026-11-06T00:47:35Z` **info** - paper run started: lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004), 10,000.00 USDT, 30 days, data via binance_vision

## Health

- Last tick: 2026-11-17T09:02:34Z (ticks run: 47)
- Last successful tick: 2026-11-17T09:02:34Z
- Last closed bar processed: 2026-11-17T09:00:00Z
- Consecutive failures: 0

Ledgers: [trades.csv](trades.csv) - [trips.csv](trips.csv) - [equity.csv](equity.csv) - [events.jsonl](events.jsonl) - [state.json](state.json)
