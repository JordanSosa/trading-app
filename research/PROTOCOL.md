# Research protocol (committed before any strategy result was produced)

This file fixes how the strategy is researched, selected and judged. It was
written and committed before the first backtest on real data was run, so the
results that follow cannot have shaped the rules.

## 1. Objective

Maximise the long-run **compound daily growth** of a small account (from
100 USDT) that trades liquid crypto USDT perpetuals, subject to survival
constraints. The brief asks for 2% a day, compounding, without blowing up.
The research measures how close a robust strategy gets to that number and
reports the measured figure, whatever it is.

Reference arithmetic, fixed in advance:

* 2% a day compounds to 1.02^365 = 1,377x a year.
* For a strategy with annualised Sharpe ratio S, the largest possible compound
  growth (full Kelly leverage) is S^2 / 2 a year in log terms. 2% a day is
  7.2 log-units a year, which needs S = 3.8 held at full Kelly, i.e. about 20%
  daily volatility with routine drawdowns above 50%.

So the target is a measuring stick, not an expectation. Nothing in this
protocol may be bent to make a backtest reach it.

## 2. Data

* Binance USD-M perpetual futures, 1-hour bars and funding rates, from the
  public archive (`data.binance.vision`), integrity-checked.
* **Point-in-time universe.** Every Monday 00:00 UTC, symbols are ranked by
  mean daily USDT volume over the previous 30 days. A symbol needs 30 days of
  history to be ranked. The universe is the top N. Delisted symbols (LUNA,
  FTT, ...) are included for as long as they traded: no survivorship bias.
* Crypto underlyings only: stablecoins, exchange indexes, tokenised equities,
  ETFs, metals and FX perpetuals are excluded by underlying
  (`src/compounder/data/universe.py`).

## 3. Periods

| Name | Dates (bar open, UTC) | Use |
|---|---|---|
| TRAIN | 2020-04-01 to 2023-12-31 | design and parameter choice |
| VALID | 2024-01-01 to 2024-12-31 | confirm each choice once; reject what fails |
| DEV | TRAIN + VALID | risk-level and governor choice, robustness |
| HOLDOUT | 2025-01-01 to 2026-10-31 | evaluated **once**, after the configuration is frozen and committed |

## 4. Execution and cost model

* Signals use closed bars only. Orders fill at the **next bar's open**.
* Taker fee 0.05% and slippage 0.03% per side (8 bps per side in total).
* Funding is paid or received at the actual historical rate.
* Protective stops are simulated as resting stop-market orders against each
  bar's high/low. They fill at the trigger, minus 25% of the distance to the
  bar's extreme, minus a further 0.10%.
* Liquidation: if worst-case intrabar equity (every long at its low, every
  short at its high) falls below a 2.5% maintenance margin, everything is
  closed there with a 1.5% liquidation fee and the account stops.
* Venue minimum order 5 USDT; positions below 10 USDT are not opened. Results
  are reported from 100 USDT and from 10,000 USDT starting equity.
* Cost sensitivity: every headline result is repeated with fees and slippage
  doubled.

## 5. Candidate signals (the whole search space)

All are volatility-scaled and computed on finite trailing windows.

| Family | Idea | Grid |
|---|---|---|
| trend | risk-adjusted time-series momentum, averaged over horizons | horizons {1-4d, 2-32d, 4-16d, 8-32d} x response {linear, sign} x N {10, 20, 30} x short scale {0, 0.5, 1} |
| carry | position against the funding rate | window {3d, 7d, 14d} x N {20, 30} |
| reversal | fade a sharp move against the prevailing trend | lookback {12h, 24h, 48h} x trigger {1.5, 2, 2.5 sigma} x hold {24h, 48h} x trend filter {on, off} |
| xsmom | cross-sectional momentum, market-neutral | lookback {3d, 7d, 14d, 30d} x N {20, 30} |

Sizing: inverse-volatility weights scaled to a portfolio volatility target with
a constant-correlation model. Variants: constant-volatility ("target") against
conviction-weighted ("budget"); stop distance {none, 2, 3, 4 daily sigmas};
no-trade band {10%, 25%, 50%}.

## 6. Selection rules

1. **Signals** are compared on TRAIN by the after-cost Sharpe ratio of daily
   returns at unit risk (fast linear backtest). A configuration is a
   candidate only if its grid neighbours are also positive (no isolated
   peaks).
2. Each family's candidate is checked **once** on VALID. A family joins the
   final blend only if its after-cost VALID Sharpe is positive. A blend is
   used only if it beats its best member on DEV.
3. **Risk level.** On DEV with the full engine, the portfolio volatility
   target is the highest tested level that satisfies all of:
   * maximum drawdown (measured on every bar) no more than 35%;
   * block-bootstrap probability of a 50% drawdown within one year no more
     than 5%;
   * no liquidation and no kill-switch event.
   The full sweep of risk levels is reported, including the levels that blow
   up, because it shows where compound growth peaks.
4. **Risk governor.** The daily target lock {off, flatten at +2%, trail after
   +2%}, the daily loss limit {none, 2%, 3%, 5%} and drawdown scaling
   {off, on} are compared on DEV. A setting is adopted if it raises compound
   daily growth, or if it lowers maximum drawdown while costing no more than
   10% of compound growth. The kill switch (flat at a 40% drawdown) is always
   on.
5. The frozen configuration is committed. Only then is HOLDOUT run, once.

## 7. What is reported

For DEV and HOLDOUT, from 100 and 10,000 USDT: compound daily return, mean
daily return, daily volatility, Sharpe, maximum drawdown, share of days at or
above +2%, worst day, yearly returns, fees and funding paid, and bootstrap
ruin probabilities. Plus the doubled-cost run and a no-governor run.

The number of configurations searched is stated next to the selected one.
In-sample figures are inflated by that search; the HOLDOUT figure is the one
to believe, and it covers a single 22-month market regime.
