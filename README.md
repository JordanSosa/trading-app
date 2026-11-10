# compounder

A crypto trading bot that compounds a small account under a strict risk
governor. It trades the 20 most liquid crypto perpetuals, long and short,
with a trend and momentum strategy, sized for a 1% daily swing and measured
against a goal of **12% a year**. Backtests, paper trading on live market
data and (opt-in) live trading run on **one engine**, so the three can be
compared like for like.

It was built to make 2% a day. The research below is why it does not try.

## Read this first: what to expect

**Nothing here is a promise, and the bot has not been run with real
money.** The default configuration is the strategy the research selected,
at two-thirds of the risk it was researched at. The research used six years
of data, with the last 22 months held back and evaluated once.

| Default configuration | Development 2020-04 to 2024-12 (in-sample) | **Holdout 2025-01 to 2026-10 (out-of-sample)** |
|---|---:|---:|
| Per year, from 10,000 USDT | +32.8% | **+15.9%** |
| Per year, from 1,000 USDT | +35.0% | **+11.1%** |
| Maximum drawdown, from 10,000 / 1,000 USDT | 15.3% / 14.3% | **15.1% / 16.2%** |
| Worst day, from 10,000 / 1,000 USDT | -3.5% / -4.2% | -3.4% / -3.2% |
| BTC buy and hold, for scale | +1,360% in total, 76.7% drawdown | -17.1% in total, 53.0% drawdown |

After fees, slippage and funding. The out-of-sample column is the one to
believe. A year ahead, resampled from those out-of-sample days:

| A year from here | From 10,000 USDT | From 1,000 USDT |
|---|---:|---:|
| The middle year | +16% | +11% |
| 5th to 95th percentile | -20% to +75% | -25% to +71% |
| A losing year | 27% | 33% |
| A year of +12% or better (the goal) | 55% | 49% |
| A drawdown of 20% / 25% / 30% or more | 32% / 15% / 6% | 43% / 23% / 10% |

* **The goal is the middle of the range, not a floor.** 12% a year is about
  what the middle year makes; a year in three or four loses money, and
  about one in five has a drawdown deeper than the 25% the default plans
  for. The status page shows the run against the goal's pace and against
  the range that is normal for a run of its length.
* **The evidence is one 22-month period.** The out-of-sample result is
  about one standard error above zero: luck alone produces that roughly one
  time in six. If the edge is not there, the expected result is the cost of
  trading, about 8% of the account a year at this setting.
* **Use about 1,000 USDT or more.** Below about 750 USDT the exchange's
  minimum order size, not the risk setting, decides the positions. Out of
  sample a 100 USDT account lost 22% with a 38% drawdown and a 200 USDT
  account lost 4.5%; from 750 USDT upwards the results were +11% to +18% a
  year. 100 USDT is the smallest deposit that runs, and a small run is told
  so when it starts.
* **Why not 2% a day.** 2% a day compounding is 1,377x a year. At the
  setting the research ran, a 1.5% daily volatility target, the strategy
  compounded at 0.05% a day out of sample: +20% a year from 10,000 USDT
  with a 23% drawdown, one fortieth of the target. Turning the risk up did
  not get there. In-sample, growth peaked at 0.19% a day and fell beyond
  that. Out of sample, twice the research risk cut the result from +39.8%
  to +10.6% with a 38.7% drawdown, and from a 4% volatility target the kill
  switch ended the run 28% to 37% down.
* **"Banking" 2% a day makes it worse.** Closing everything once the day is
  up 2% turned the out-of-sample gain into a 5.5% loss: it caps the few
  large days the profit comes from and keeps every losing day.
* **Costs decide it.** At doubled fees and slippage the out-of-sample
  result at the research setting falls from +39.8% to +8.7%.
* **Why the default is below the research setting.** Return and drawdown
  move together: the risk dial does not make the strategy better or worse.
  At a 1.0% volatility target it kept about four-fifths of the return for
  two-thirds of the drawdown, and the chance of a 25% drawdown within a
  year falls from about one in two to about one in five.
  `config/research.toml` is the 1.5% setting every table in `research/` was
  produced with.
* **Trading it "like a professional" does not add anything.** The bot is
  already a swing trader: it holds a position for 4.6 days on average and
  wins 34% of its trades, with winners 2.35 times the size of losers.
  Fourteen classic swing and day-trading systems with textbook stop and
  target management, put in its place, all did worse in both periods, and
  every day-trading one hit the kill switch once costs were charged
  ([`RESULTS.md`](research/RESULTS.md), section 10). Large professional
  trend-following funds have compounded at about 5.7% a year since 2000,
  at lower risk.

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
  volatility** (`strategy.target_vol`, 1.0% by default). That number is the
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
| Volatility target | 1.0% a day | Sizes the whole book; the main risk setting (the research ran at 1.5%) |
| Exposure caps | 3.0x gross, 0.6x per symbol | Hard limits whatever the signal says |
| Disaster stop | 4 daily sigmas (3%-25%) | A resting reduce-only stop per position; protects while the bot is offline |
| Dead markets | 48 hours without a bar | The position is settled at its last price (a delisting) |
| Daily loss limit | 3% | Down 3% on the UTC day: flat until tomorrow |
| Drawdown scaling | from 15% to 35% | Risk shrinks linearly to half as the drawdown deepens |
| Kill switch | 40% drawdown | Flatten and stop until a person runs `compounder resume` |
| Liquidation model | 2.5% maintenance margin | Backtests and paper runs wipe the account when a real exchange would |
| Data cross-check | 2% | Refuses to trade if the venue's prices disagree with a second venue |
| Goal | +12% a year, drawdowns to 25% | A yardstick on the status page: the pace the goal implies for the time the run has had, and a warning when the drawdown goes past the plan. Nothing trades on it |
| Daily profit target | off | Optional: `risk.daily_target = 0.02` measures every day against the original 2%. Never used to close winners (doing so turned the out-of-sample gain into a loss) |

The kill switch cannot be disabled. It bounds how far a failing strategy
can take the account down, but only as fast as an hourly check can act. At
volatility targets up to 8% a day (eight times the default) every test run
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

The paper run starts with 1,000 USDT. To paper trade a different deposit,
set `run.initial_equity` in `config/paper.toml` (100 USDT minimum; see
above for why about 1,000 is the sensible minimum). Other presets:
`config/research.toml` (the 1.5% setting of the research record, measured
against 2% a day), `config/aggressive.toml` (three times the default risk)
and `config/spot.toml` (long or flat, no leverage). Only the default and
the research setting have an out-of-sample record for the self-audit to
judge a run against.

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
| `STATUS.md` / `README.md` on `paper-state` | Equity, the result against the goal's pace, compound daily return achieved, drawdown against the plan, the self-audit (below), positions, last days, events |
| `dashboard.html`, `summary.json` | The same as a web page and as JSON |
| `trades.csv`, `daily.csv`, `equity.csv`, `events.jsonl` | Append-only ledgers: every fill, every completed day, hourly equity, every alert |
| Push notifications (optional) | Alerts (loss limit, kill switch, stops, data problems), a daily summary, optionally fills |

Notifications: set any of `COMPOUNDER_NTFY_TOPIC` (free phone push via the
ntfy app; use a long random topic), `COMPOUNDER_DISCORD_WEBHOOK`,
`COMPOUNDER_SLACK_WEBHOOK`, or `COMPOUNDER_TELEGRAM_BOT_TOKEN` +
`COMPOUNDER_TELEGRAM_CHAT_ID`, as repository secrets (Actions) or in `.env`.

A tick exits non-zero after three consecutive data failures or on a state or
configuration error, so a failed workflow e-mail reaches you.

### Self-audit: what a run's results can and cannot tell you

The status page judges the run against the research record instead of
leaving a good or bad month to be read as proof. It shows the result so far
beside the range that is normal for a run of that length, and a one-line
reading: too early (under 30 completed days), inside, below or above the
normal range, and whether the result is beyond what a bot with no edge
would reach. The ranges come from resampling the configuration's own
out-of-sample days (`src/compounder/reference.json`, from 1,000 USDT); "no
edge" is the same days with the average gain removed and the trading costs
left in. For the default configuration:

| Run length | Normal if the research holds | Ends at a loss | Normal if the bot has no edge | Ends at a loss | Chance the run tells them apart |
|---|---:|---:|---:|---:|---:|
| 30 days | -8% to +17% | 52% | -10% to +15% | 62% | 7% |
| 90 days | -15% to +29% | 45% | -19% to +22% | 61% | 9% |
| 180 days | -20% to +44% | 40% | -27% to +30% | 63% | 12% |
| 1 year | -25% to +71% | 35% | -39% to +39% | 67% | 18% |
| 2 years | -30% to +126% | 28% | -54% to +50% | 72% | 28% |

Ranges are 5th to 95th percentiles and ignore the kill switch, which would
end the worst paths at a 40% drawdown. The last column is how often a run
with the measured edge ends above what 95% of no-edge runs reach.

* **A few months of paper trading cannot show whether the strategy works.**
  A bot with the measured edge and a bot with none produce nearly the same
  spread of results for a long time; after a year the run settles it about
  one time in five.
* **What a paper run does show, within days, is whether the plumbing is
  sound**: data arriving, orders filling, stops resting, fees in line. The
  audit flags fees running at more than twice the research rate.
* **It applies to the default and to the research setting only.** Those
  are the two configurations with an out-of-sample record. A changed
  strategy, risk or cost setting has none to be judged against, and the
  page says so. An account under about 750 USDT is told that it is smaller
  than the research describes.
* **The audit reports; it never re-tunes the strategy.** A walk-forward
  simulation of a bot that moves to whatever its own recent results favour
  is in [`RESULTS.md`](research/RESULTS.md), section 9. Of the 36 versions
  that judged on six months of results or less, none beat leaving the
  strategy alone over the 5.6 years tested, 19 hit the kill switch, and the
  faster it adapted the worse it did. At day-trading speed ten of eleven
  textbook rules lost 95% or more of the account to costs.

## Configuration

`config/paper.toml` documents every key inline. The ones that matter:

| Key | Default | Meaning |
|---|---|---|
| `run.initial_equity` | 1000 | Paper deposit in USDT (minimum 100; below about 1,000 the book is a handful of positions) |
| `strategy.target_vol` | 0.010 | Portfolio daily volatility target: the risk dial |
| `strategy.short_scale` | 0.5 | Size of shorts relative to longs (0 = long only) |
| `universe.size` | 20 | Number of perpetuals traded (must equal `strategy.universe_n`) |
| `risk.max_gross` / `max_weight` | 3.0 / 0.6 | Exposure caps |
| `goal.annual_return` / `goal.drawdown` | 0.12 / 0.25 | The yardstick on the status page (0 = none). Not part of the trading configuration: changing it does not interrupt a run |
| `risk.daily_target` / `target_mode` | 0 / `off` | Optional daily yardstick (0.02 = the original target); `flatten` or `trail` would bank it |
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
  audit.py         self-audit: a run's result against the range the research makes normal for its length
  reference.json   the out-of-sample daily returns the audit resamples (scripts/make_reference.py)
  state.py, report.py, notify.py, config.py, cli.py, metrics.py
config/            paper (default), research (the setting of the research record), aggressive, spot, live template
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
