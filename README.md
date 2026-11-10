# compounder

A crypto trading bot that compounds a small account (from 100 USDT) under a
strict risk governor. It trades the 20 most liquid crypto perpetuals, long and
short, with a trend and momentum strategy; measures every day against a
**2% daily profit target**; and runs backtests, paper trading on live market
data and (opt-in) live trading on **one engine**, so the three can be
compared like for like.

## Read this first: what a 2% a day target gets you

**The bot does not make 2% a day, and nothing here should be read as saying
it will.** 2% a day compounding is 1,377x a year: 100 USDT would become
137,700 in twelve months. The research behind this app set out to measure
how close a robust strategy gets, on six years of data, with the last 22
months held back and evaluated once.

| Default configuration | Development 2020-04 to 2024-12 (in-sample) | **Holdout 2025-01 to 2026-10 (out-of-sample)** |
|---|---:|---:|
| Compound return per day, from 10,000 USDT | +0.118% | **+0.050%** |
| Total / annualised | +676% / +54% | **+39.8% / +20.1%** |
| Maximum drawdown | 21.5% | **23.2%** |
| Days at or above +2% | 7.8% | **7.6%** |
| Worst day | -5.3% | -5.3% |
| From 1,000 USDT: compound per day, max drawdown | +0.120%, 20.9% | +0.035%, 25.4% |
| From 100 USDT: compound per day, max drawdown | +0.157%, 24.3% | +0.009%, 39.8% |
| BTC buy and hold, for scale | +0.153% a day, 76.7% drawdown | -0.030% a day (-18.0%), 53.0% drawdown |

After fees, slippage and funding. The out-of-sample column is the one to
believe, and it is a single 22-month period in which 2025 lost 5.9% and
2026 made 48.7%.

What the evidence says about the target:

* **Out of sample the strategy compounded at 0.05% a day**, one fortieth
  of 2%. That is about +20% a year with a 23% drawdown, in a period when
  Bitcoin fell 18%.
* **Turning the risk up does not get there.** In-sample, compound growth
  peaked at 0.19% a day and fell beyond that. Out of sample, doubling the
  default risk cut the result from +39.8% to +10.6% with a 38.7% drawdown,
  and from a 4% volatility target the kill switch ended the run 28% to 37%
  down.
* **"Banking" 2% a day makes it worse.** Closing everything once the day is
  up 2% turned the out-of-sample gain into a 5.5% loss: it caps the few
  large days the profit comes from and keeps every losing day. So the
  target is measured and reported, never traded on.
* **Costs decide it.** At doubled fees and slippage the out-of-sample
  result falls to +8.7%.
* **100 USDT is the minimum that functions, not the size these numbers
  describe.** Exchange minimum order sizes hold a 100 USDT account to a
  handful of positions; it beat the large account in development and made
  +5.9% against +39.8% in the holdout, with a drawdown that stopped just
  short of the kill switch. From about 1,000 USDT the book is close to the
  tested one.
* **A year from here:** resampling the out-of-sample days gives a median of
  +20%, a range of roughly -31% to +123%, a three-in-ten chance of a losing
  year and a 20% drawdown more likely than not. If the edge has gone, the
  expected result is the cost drag: a few percent a year below zero.

The full record is in [`research/`](research): the protocol written before
any result existed, the candidates frozen before the validation year, the
selection frozen before the holdout, the independent audit, and
[`RESULTS.md`](research/RESULTS.md).

## How it trades

```
market data ──► strategy ──► risk governor ──► engine ──► fills ──► ledgers, status page, alerts
 OKX perps        target         loss limit,      orders at     paper (simulated)
 (+ Gate as       weights        drawdown         the next      or live (exchange)
 cross-check)     every hour     scaling, kill    hourly open
```

* **Universe.** Every Monday 00:00 UTC the crypto perpetuals are ranked by
  their average daily volume over the previous 30 days; the top 20 that have
  been listed for at least 30 days are traded. Stablecoins, indexes and the
  tokenised stocks, ETFs and metals that exchanges now list as perpetuals
  are excluded.
* **Signal.** For each symbol, the average of its 2, 4, 8, 16 and 32-day
  returns, each measured in units of its own volatility and clipped to
  ±1 (trend), plus its rank among the 20 by volatility-adjusted 14-day
  return (cross-sectional momentum). Positive means long, negative means
  short at half size.
* **Sizing.** Weights are inversely proportional to each symbol's
  volatility and scaled so the whole portfolio targets a fixed **daily
  volatility** (`strategy.target_vol`, 1.5% by default). That number is the
  risk dial: equity invested rises and falls with market volatility, the
  expected daily swing does not. Target weights are averaged over 12 hours
  and a position is only adjusted when it is more than 25% away from its
  target; without this, trading costs eat the entire edge (see research).
* **Execution.** Decisions use closed 1-hour bars only. Orders fill at the
  next bar's open; the paper engine charges 0.05% fee and 0.03% slippage a
  side and books funding at the venue's real rates.
* **Compounding.** Every position is a fraction of *current* equity, so
  gains are reinvested automatically. Small accounts hold fewer, larger
  positions so that each clears the exchange's minimum order size.

## Risk controls

| Control | Default | What it does |
|---|---|---|
| Volatility target | 1.5% a day | Sizes the whole book; the main risk setting |
| Exposure caps | 3.0x gross, 0.6x per symbol | Hard limits whatever the signal says |
| Disaster stop | 4 daily sigmas (3%-25%) | A resting reduce-only stop per position; protects while the bot is offline |
| Dead markets | 48 hours without a bar | The position is settled at its last price (a delisting) |
| Daily loss limit | 3% | Down 3% on the UTC day: flat until tomorrow |
| Drawdown scaling | from 15% to 35% | Risk shrinks linearly to half as the drawdown deepens |
| Kill switch | 40% drawdown | Flatten and stop until a person runs `compounder resume` |
| Liquidation model | 2.5% maintenance margin | Backtests and paper runs wipe the account when a real exchange would |
| Data cross-check | 2% | Refuses to trade if the venue's prices disagree with a second venue |
| Daily profit target | 2%, measured | Reported every day; **not** used to close winners (doing so turned the out-of-sample gain into a loss) |

The kill switch cannot be disabled. It bounds how far a failing strategy
can take the account down, but only as fast as an hourly check can act. At
volatility targets up to 8% a day (five times the default) every test run
that reached it ended 40.1% to 40.4% below its peak; at 10% to 20% a day a
single hour took the account 45% to 59% down first. It does not protect
against an exchange failing or a bug.

## Quick start: paper trading

Paper mode needs no exchange account and no API keys. It trades simulated
money against live OKX prices.

### Option A: GitHub Actions (no server)

`.github/workflows/paper-trade.yml` runs a tick every 15 minutes and commits
the run to an orphan branch, **`paper-state`**, whose `README.md` is the live
status page (equity curve, today's return against the target, positions,
fills, alerts).

1. Merge this branch into the default branch (scheduled workflows only run
   from there).
2. In **Settings → Actions → General**, allow workflows and give them read
   and write permission. The workflow pushes only to `paper-state`.
3. Optional: start now with **Actions → "Paper trade (compounder)" → Run
   workflow**. A run left on `paper-state` by the previous app is archived
   under `archive/` automatically.

### Option B: Docker (any always-on machine)

```bash
docker compose up -d --build                                             # start / resume
docker compose logs -f                                                   # follow ticks
docker compose exec compounder compounder status --state-dir /state      # status
docker compose run --rm -p 8080:8080 compounder serve --state-dir /state --host 0.0.0.0
```

### Option C: plain Python

```bash
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.txt && pip install --no-deps --no-build-isolation .
compounder run                    # daemon: ticks ~15 s after every hourly close
# or schedule `compounder tick` with cron every 5-15 minutes
compounder status
compounder serve                  # http://127.0.0.1:8080/
```

To paper trade a different deposit, set `run.initial_equity` (100 USDT
minimum) in `config/paper.toml`. Presets: `config/aggressive.toml` (twice
the risk) and `config/spot.toml` (long or flat, no leverage).

A tick is idempotent: it replays every hourly bar that closed since the last
one (up to a week), so a late or missed run changes nothing, and running it
twice does nothing the second time. A paper fill uses the open of the bar
that has just started, or the latest price if the tick is more than five
minutes late.

## Backtesting

```bash
compounder download --end 2026-10          # ~40 min: every USDT perpetual's history, checksum-verified, holes repaired
compounder backtest --start 2025-01-01 --equity 100 --ruin --no-months
compounder backtest --config config/aggressive.toml --start 2025-01-01 --cost-mult 2
compounder backtest --output out/           # daily.csv, equity.csv, trades.csv, metrics.json
```

The dataset is built from Binance's public archive and includes delisted
symbols (LUNA, FTT, ...), ranked point-in-time, so the backtest cannot pick
tomorrow's survivors. Holes in the archive's monthly files are filled from
its daily files. `compound_daily` in the output is the constant daily
return that compounds to the same result: the number to hold against 2%.
`scripts/holdout.py` and `scripts/make_tables.py` reproduce the tables in
`research/RESULTS.md`.

## Going live (opt-in, read all of it)

Live trading sends real market orders through [ccxt](https://github.com/ccxt/ccxt)
to an **OKX** USDT-margined perpetual account (the same venue the paper run
takes its prices from; it is the only venue supported). **It has been
tested against a simulated exchange only** (`tests/test_broker.py`), never
against the real one from the environment this was built in. Treat the
first weeks as a test of the software, on OKX's demo trading environment.

Real orders are sent only when all four hold: `run.mode = "live"`,
`live.dry_run = false`, API keys in the environment, and the command carries
`--i-understand-live-trading`.

1. `pip install ".[live]"`.
2. In OKX **demo trading**, create an API key with trade permission only
   (no withdrawals). Use a dedicated account or sub-account that holds only
   USDT, in one-way (net) position mode, with no other positions or orders.
   The bot refuses an account with positions it is not tracking, and treats
   every stop order on a symbol it trades as its own.
3. `cp config/live.example.toml config/live.toml` (git-ignored). Leave
   `sandbox = true` and `dry_run = true`.
4. `export COMPOUNDER_API_KEY=... COMPOUNDER_API_SECRET=... COMPOUNDER_API_PASSWORD=...`
5. `compounder live-check --config config/live.toml` prints the equity and
   positions the bot sees. Check them against the exchange's own screen.
6. `compounder run --config config/live.toml --i-understand-live-trading`
   in dry-run logs every order it *would* send. Check the sizes.
7. Set `dry_run = false` (still sandbox). Let it trade demo money next to a
   paper run for a few weeks; the two should track each other.
8. Only then set `sandbox = false`, with money you can afford to lose.

What the live broker does: mirrors the exchange account into the engine
before every decision; places market orders for the planned changes
(reductions first); keeps one reduce-only stop order per position on the
exchange; re-reads the account after trading and, if a position is far
larger than intended (a contract-size or unit error), closes it and sets
the kill switch. It refuses any single order above
`live.max_order_notional` and any account above `live.max_account_equity`.

Leveraged crypto derivatives are restricted or banned for retail clients in
several countries (in Australia, for example, ASIC restricts their issue to
retail clients). Whether you may trade them, and on which venue, is yours
to establish; the spot preset exists for paper and backtest use.

## Monitoring and alerts

| Where | What |
|---|---|
| `STATUS.md` / `README.md` on `paper-state` | Equity, today against the target, days at target, compound daily return achieved, drawdown, positions, last days, events |
| `dashboard.html`, `summary.json` | The same as a web page and as JSON |
| `trades.csv`, `daily.csv`, `equity.csv`, `events.jsonl` | Append-only ledgers: every fill, every completed day, hourly equity, every alert |
| Push notifications (optional) | Alerts (loss limit, kill switch, stops, data problems), a daily summary, optionally fills |

Notifications: set any of `COMPOUNDER_NTFY_TOPIC` (free phone push via the
ntfy app; use a long random topic), `COMPOUNDER_DISCORD_WEBHOOK`,
`COMPOUNDER_SLACK_WEBHOOK`, or `COMPOUNDER_TELEGRAM_BOT_TOKEN` +
`COMPOUNDER_TELEGRAM_CHAT_ID`, as repository secrets (Actions) or in `.env`.

A tick exits non-zero after three consecutive data failures or on a state or
configuration error, so a failed workflow e-mail reaches you.

## Configuration

`config/paper.toml` documents every key inline. The ones that matter:

| Key | Default | Meaning |
|---|---|---|
| `run.initial_equity` | 100 | Paper deposit in USDT (minimum 100) |
| `strategy.target_vol` | 0.015 | Portfolio daily volatility target: the risk dial |
| `strategy.short_scale` | 0.5 | Size of shorts relative to longs (0 = long only) |
| `universe.size` | 20 | Number of perpetuals traded (must equal `strategy.universe_n`) |
| `risk.max_gross` / `max_weight` | 3.0 / 0.6 | Exposure caps |
| `risk.daily_target` / `target_mode` | 0.02 / `off` | The measured target; `flatten` or `trail` would bank it |
| `risk.daily_loss_limit` | 0.03 | Daily stop |
| `risk.dd_soft` / `dd_hard` / `min_risk_mult` | 0.15 / 0.35 / 0.5 | Drawdown scaling |
| `risk.kill_dd` | 0.40 | Kill switch |
| `costs.*` | 5 bps fee, 3 bps slippage | Paper and backtest cost model |
| `data.sources` | okx, gate | Venue traded, then fallback / cross-check |

A running account refuses to continue if strategy, risk, cost or universe
settings change, because its record would mix two configurations. Start a
new run with `compounder reset --yes`, or continue deliberately with
`compounder tick --accept-config-change`.

## Layout

```
src/compounder/
  engine.py        fills, fees, funding, stops, liquidation, risk governor (one code path for all modes)
  strategy.py      target weights: trend + cross-sectional momentum, volatility-scaled
  signals.py       causal rolling-window building blocks
  backtest.py      full-engine backtest and the fast linear approximation used in research
  forward.py       idempotent tick, universe refresh, catch-up, daemon
  broker.py        live execution through ccxt, with interlocks
  data/            verified archive downloader, point-in-time ranking, OKX and Gate adapters
  state.py, report.py, notify.py, config.py, cli.py, metrics.py
config/            paper (default), aggressive, spot, live template
research/          protocol, candidates, selection and results, with raw JSON
scripts/           the research scripts that produced them
tests/             engine accounting, look-ahead, forward/backtest parity, broker, data, CLI
```

## Tests

```bash
pip install -e ".[dev]"
pytest -m "not network"      # offline suite
pytest -m network            # live exchange smoke tests
```

The suite includes a parity test: hourly ticks through the forward runner
against a simulated exchange reproduce the backtest's equity to nine
significant figures.
