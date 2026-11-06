# tradebot: BTC/USDT paper trading

`tradebot` forward-trades BTC/USDT with paper money on 1-hour candles for a fixed
30-day campaign. It runs unattended, monitors itself, and publishes its results.
The live (forward) run and the backtester share one event-driven engine, so the
two can be compared directly. A tick-by-tick replay of the live path reproduces
the backtest to full floating-point precision (see `tests/test_forward.py`).

> **Paper money only.** No exchange keys are used and no orders are sent. Live
> trading is deliberately not implemented, and the config refuses any
> `mode` other than `paper`.

## What to expect (read this first)

No strategy can promise a profit in any given 30 days, and this one doesn't.

**Default strategy: `lss_breakdown_vol`** (long/short, hybrid mode):

* **Long side:** the long-only breakout system `breakout_dual`. It holds 0%,
  50% or 100% BTC on spot, based on a 6-day-high breakout and a
  600-hour-EMA + 3 x ATR band breakout.
* **Short side:** a bear-only "short sleeve" on BTC perpetual futures. It runs
  only while the long side is flat and BTC is below its 125-day average (but
  not more than 35% below it). It enters on a fresh 10-day low, covers on a
  2-day high, and is volatility-sized at 0.25-1.0 of equity. It is short
  about 11% of the time.

Both sides were selected under pre-registered rules from independently
researched and audited strategy families (`research/SELECTION.md`,
`research/LONG_SHORT_PROTOCOL.md`, `research/LONG_SHORT_DECISION.md`). The
table shows 30-day campaigns that start flat with 10,000 USDT and end flat,
one started every day, after fees and funding.

| 30-day campaigns | Long-only (spot) | **Long/short (hybrid, default)** | Buy & hold |
|---|---:|---:|---:|
| 2018-2024: mean return | +4.89% | **+6.24%** | +4.61% |
| 2018-2024: ended in profit / at a loss | 47% / 49% | **57% / 43%** | 54% / 46% |
| 2018-2024: bad month (5th percentile) | -8.4% | -9.1% | -28.2% |
| 2018-2024: continuous max drawdown | 32.5% | **28.8%** | 81% |
| 2024 only (out of sample for both): total | +54.7% | +58.3% | +121% |
| 2025-01 to 2026-11 (see note): total | +12.5% | **+33.4%** | -13.9% |
| 2025-01 to 2026-11: mean 30-day return | +0.63% | **+1.44%** | -0.21% |
| 2025-01 to 2026-11: max drawdown | 22.4% | **16.6%** | 54% |

How to read this:

* **Long-only edge:** the long side beats buy-and-hold mainly by sitting out
  bear markets. It lags in strong bull years (2020, 2023, 2024).
* **What shorts add:** the short sleeve added about +1.2% per 30 days on
  2018-2024. About 84% of that came from five crash trades (Feb 2018,
  Nov 2018, Mar 2020, May 2021, Jan 2022). In 2024, the one fully
  out-of-sample year, it added almost nothing. In bull markets it whipsaws
  for small losses. Treat it as crash insurance with positive expected value,
  not as profit on both sides every month.
* **About the 2025-26 rows:** this period was evaluated after the long-only
  strategy was chosen, but its 36% decline was already known when the short
  research ran. The long/short numbers for it are therefore flattered by
  hindsight and are not a clean test.
* **Current state (2026-11-05):** BTC is above its 125-day average, so the
  short sleeve is off and the strategy is flat. It buys on an upside breakout,
  and shorts only if BTC drops below about 74k and makes a fresh 10-day low.

**Running it for real later:** shorting needs a perpetual-futures account.
Retail access is restricted in some countries (for example, in Australia
ASIC licenses retail crypto derivatives and has penalised an unlicensed
offering). The paper run needs no account.

## How it trades

```
market data ──► strategy (causal signals) ──► engine ──► paper fills ──► ledgers / reports / alerts
 Binance mirror   target exposure 0..1          risk overlay   next-bar open,   trades.csv, equity.csv,
 + 4 fallbacks    decided at each bar close     stops, limits  fees + slippage  STATUS.md, notifications
```

* **Data**: closed 1h candles from `data-api.binance.vision`, Binance's
  market-data-only mirror. It is reachable from US-hosted runners, where
  `api.binance.com` returns HTTP 451. OKX, KuCoin, Gate and Coinbase are
  automatic fallbacks. Every tick cross-checks the newest bars against a second
  venue and refuses to trade on a deviation above 1.5%.
* **Decisions** use closed bars only. Fills happen at the **next bar's open**
  with 0.10% fee and 3 bps slippage per side, using Binance's lot size
  (0.00001 BTC) and a 10 USDT minimum notional. No leverage: exposure is
  between -100% and +100% of equity.
* **Instruments** (`[run] instrument`):
  * `spot`: long or flat only.
  * `perp`: everything on USDT-margined perpetual futures (0.05% fees and
    funding).
  * `hybrid` (default): longs on spot, shorts on perpetuals. Shorts receive
    or pay 8-hourly funding: the live run uses OKX's public funding history
    (Bitget as fallback), and backtests use Binance's archive.
* **Risk overlay** (`[risk]` in the config):
  * **Circuit breaker**: if equity falls 10% from its high-water mark, sell
    everything and pause for 48 hours.
  * **Campaign stop-loss**: if equity falls 15% below the starting capital,
    sell everything and stop trading for the rest of the run.
  * **Rebalance band**: exposure changes smaller than 10% of equity are skipped,
    to save fees.
* **Campaign**: the run lasts 30 days on the hourly bar grid, then sells to
  USDT so the result is fully realised (`end_action = "flatten"`).
* **Catch-up**: every tick replays every bar that closed since the last one
  (up to 30 days), so a delayed or missed scheduler run changes nothing.
  Running a tick twice is a no-op.

## Running the 30-day paper run

### Option A: GitHub Actions (no server needed, recommended)

`.github/workflows/paper-trade.yml` ticks every 15 minutes and commits the run
state to an orphan branch, **`paper-state`**, which never touches your code
branches.

1. Merge this branch into the default branch (`main`). GitHub only runs
   scheduled workflows from the default branch.
2. In **Settings → Actions → General**, allow workflows to run, and under
   "Workflow permissions" allow read and write. The workflow pushes only to
   `paper-state`.
3. Optional: start immediately with **Actions → "Paper trade BTC/USDT" → Run
   workflow**. Otherwise the first scheduled tick starts the run.
4. Watch it on the `paper-state` branch. Its `README.md` is the live status
   page, with equity, P&L against buy & hold, position, fills, events, health
   and an equity chart. Every run also writes the status to the Actions job
   summary.

To restart from scratch, run the workflow manually with **reset = true**. The
old run is archived under `archive/` on the state branch.

### Option B: Docker (any always-on machine)

```bash
docker compose up -d --build                                       # start / resume
docker compose logs -f                                             # follow ticks
docker compose exec tradebot tradebot status --state-dir /state    # status
docker compose run --rm -p 8080:8080 tradebot serve --state-dir /state --host 0.0.0.0  # dashboard
```

### Option C: plain Python

```bash
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.txt && pip install --no-deps --no-build-isolation .
tradebot run                  # daemon: ticks ~20 s after every hourly close
# or schedule `tradebot tick` with cron every 5-15 minutes
tradebot status
tradebot serve                # http://127.0.0.1:8080/ dashboard
```

## Monitoring and alerts

| Where | What |
|---|---|
| `paper-state` branch, `README.md` / `STATUS.md` | Live status page with the SVG equity curve against buy & hold |
| `dashboard.html`, `summary.json` | Same data as HTML (dark/light) and as machine-readable JSON |
| `trades.csv`, `trips.csv`, `equity.csv`, `events.jsonl` | Append-only audit ledgers (hourly equity, every fill, every alert) |
| Actions job summary | Status of every tick |
| GitHub e-mail | Sent when the workflow fails. A tick exits non-zero after 3 consecutive data failures or on a config/state error |
| Push notifications (optional) | Fills, alerts (circuit breaker, stop-loss, data problems), run start and end, and a daily summary |

To enable push notifications, add any of these as repository secrets (Actions)
or in `.env` (Docker): `TRADEBOT_NTFY_TOPIC` (free phone push via the ntfy app,
no account needed; use a long random topic name), `TRADEBOT_DISCORD_WEBHOOK`,
`TRADEBOT_SLACK_WEBHOOK`, or `TRADEBOT_TELEGRAM_BOT_TOKEN` +
`TRADEBOT_TELEGRAM_CHAT_ID`.

## Research and backtesting

```bash
tradebot download                                   # full BTCUSDT 1h history (checksum-verified)
tradebot strategies                                 # list strategies and defaults
tradebot backtest --strategy breakout_dual --start 2022-01-01 --campaigns
python scripts/ls_evaluate.py lss_breakdown_vol --mode hybrid   # long/short adoption metrics
tradebot evaluate --period train                    # research score for a fixed period
tradebot replay --out /tmp/replay                   # run the live code path over the last 31 days
```

The research protocol (`src/tradebot/research.py`):

* **Fixed periods**: TRAIN 2018–2023 (design and tuning), VALID 2024 (checked
  once), HOLDOUT 2025-01-01 to today (touched once, after the final selection).
  The research agents never had access to holdout data.
* **Pre-declared score**: the mean of log(1 + r) over 30-day campaigns started
  every day. Each campaign starts flat with fresh capital and ends flat,
  exactly like the deployed run.
* **Causality gate**: `check_causal` verifies that signals on any prefix and on
  the shortest rolling window the live run can use (`warmup_bars + 1`) equal
  the full-history signals. This catches look-ahead bias and undersized
  warm-ups for recursive indicators.
* Every strategy family was independently audited for look-ahead bias,
  reproducibility, parameter sensitivity and overfitting. The reports are in
  `research/`.

## Configuration

`config/paper.toml` (all keys and defaults: `src/tradebot/config.py`):

| Section | Key | Default | Meaning |
|---|---|---|---|
| `run` | `initial_cash` | 10000 | Paper USDT |
| `run` | `duration_days` | 30 | Campaign length |
| `run` | `end_action` | flatten | `flatten` sells to USDT at the end; `hold` keeps the position |
| `run` | `instrument` | hybrid | `spot`, `perp` or `hybrid` (longs on spot, shorts on perps) |
| `costs` | `fee_rate` / `perp_fee_rate` / `slippage_bps` | 0.001 / 0.0005 / 3 | Spot fee, perpetual fee, slippage |
| `risk` | `max_short` | 1.0 | Largest short exposure (0 = long-only) |
| `risk` | `circuit_breaker_dd` / `circuit_breaker_hours` | 0.10 / 48 | Drawdown pause |
| `risk` | `campaign_stop_loss` | 0.15 | Hard stop below initial capital |
| `risk` | `min_rebalance` | 0.10 | Fee-saving rebalance band |
| `data` | `providers` | binance_vision, okx, kucoin, gate, coinbase | Priority order |
| `strategy` | `name` / `params` | see file | Strategy and parameters |

A running campaign refuses to continue if the strategy, costs or risk settings
change (its results would mix two configurations). Run `tradebot reset` to
start a new campaign, or use `--accept-config-change` to continue deliberately.
Capital, duration, symbol and timeframe can never change mid-run.

## Layout

```
src/tradebot/
  engine.py        event-driven engine: fills, fees, stops, circuit breaker, campaign end
  forward.py       idempotent live tick + daemon
  backtest.py      continuous backtest and rolling 30-day campaign simulator
  research.py      periods, score, causality/window checks
  strategies/      buy_hold, trend, breakout, meanrev, regime, blend, ls_* (long/short)
  data/            candle schema, causal resampling, live providers, archive downloader
  state.py         atomic JSON state + append-only CSV ledgers + file lock
  report.py        STATUS.md, equity.svg, dashboard.html, summary.json
  notify.py        ntfy / Discord / Slack / Telegram
  simulate.py      historical replay through the live tick
scripts/           selection and holdout scripts used for the research
research/          per-family research reports, audits and the final results
tests/             unit, parity and failure-mode tests (pytest -m "not network")
```

## Going live later (not implemented)

Live trading would need: a broker adapter that places and reconciles real
orders (partial fills, rejects, exchange filters), secure key handling, a
venue the operator is legally allowed to use (`api.binance.com` is
geo-restricted in the US), and a paper-versus-live reconciliation period. The
engine's single code path is the foundation for that, but none of it exists
yet, by design.

## Tests

```bash
pip install -e ".[dev]"
pytest -m "not network"      # offline suite
pytest -m network            # live data smoke test
```
