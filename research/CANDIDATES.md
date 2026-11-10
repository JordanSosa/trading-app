# Candidates frozen on TRAIN (committed before VALID was looked at)

Everything here used bars before 2024-01-01 only. About 250 configurations
were evaluated on TRAIN (stage 1: 122; turnover controls: 36; smoothed trend
grid: 36; sizing, families and blends: about 55). Raw output is in
`research/results/*_train.json`; the scripts are `scripts/research_signals.py`,
`scripts/research_sweep.py` and `scripts/research_stage2.py`.

## What TRAIN showed

All figures are after 8 bps a side, fast linear backtest, 2020-04 to 2023-12.

* **Costs decide everything.** Before costs every momentum variant has a
  Sharpe ratio near 2. Re-targeted every hour, the fast ones turn the book
  over two to nine times a day and end up between -4 and +0.9. Averaging the
  target weights over 12 hours cuts turnover by about two thirds and is what
  makes the slower variants work (2-32 day trend, 20 symbols, long/short:
  Sharpe 1.14 to 1.64).
* **Trend (time-series momentum) is the core.** With 12-hour smoothing, every
  one of the 27 linear-response configurations with horizons of two days or
  more scores between 1.42 and 1.97. The 1-4 day horizons are worse
  (0.74-1.34) and the sign response is worse than the linear one throughout.
* **Cross-sectional momentum adds to it.** Stand-alone it scores 1.41-2.03
  but needs twice the gross exposure and has 39-49% drawdowns. Added to
  trend it lifts the Sharpe ratio from 1.77 to 1.91-2.17 for every lookback
  and weight tried, and from 1.45 to 1.65 at doubled costs.
* **Carry and reversal are rejected.** Carry is negative stand-alone
  (-0.58 to -0.11). Reversal is negative without a trend filter (-2.5 to
  -0.8) and about zero with one (-1.0 to 0.3). Neither improves the blend
  by more than noise.
* **Nothing is a knife edge.** No-trade band 10/25/50%: 1.78/1.77/1.61.
  Correlation assumption 0.5/0.7/0.9: 1.74/1.77/1.78. Volatility window
  7/14/30 days: 1.75/1.77/1.56. Conviction-weighted sizing instead of
  constant volatility: 1.45-1.63. An 8-hour decision cadence instead of
  smoothing: 1.45-1.87 depending only on which hour the clock starts
  (mean 1.67), which is why smoothing, which has no such phase, is used.
* **Placebos.** Executing 1 / 2 / 6 / 24 hours late: 1.60 / 1.54 / 1.29 /
  1.19 (no dependence on instant execution, no sign of look-ahead). The
  same weights with their weeks shuffled in time: -0.07.
* **Engine cross-check.** The full engine without governor reproduces the
  linear backtest (Sharpe 1.69 against 1.64 for the long/short variant) and
  is positive in each TRAIN year.

## Frozen candidates

Central choices in every dimension rather than the best cell of any grid.
Common to all: 20-symbol universe, 14-day volatility, 12-hour weight
smoothing, constant-volatility sizing at a 2% daily target with correlation
0.7, 25% no-trade band.

| Id | Definition | TRAIN Sharpe |
|---|---|---:|
| T0 | trend: horizons 2, 4, 8, 16, 32 days, linear response, shorts at half size | 1.77 |
| X0 | cross-sectional momentum: 14-day lookback, long/short ranks | 1.92 |
| B0 | T0 + cross-sectional momentum at equal weight, shorts at half size | 1.97 |

## The VALID check (2024, run once)

Each of T0, X0 and B0 is evaluated once on 2024. A family stays only if its
after-cost VALID Sharpe is positive. B0 is used only if it beats its better
member on DEV; otherwise the better member is used alone. The stop distance
and the risk governor are chosen afterwards on DEV with the full engine, as
the protocol states.

## Note added with Amendment 3 (after the audit, before HOLDOUT)

The figures above are from the first run. After the data repair and the
engine fixes described in `SELECTION.md` (Amendment 3) every script was run
again; the JSON files in `research/results/` hold the re-run. No conclusion
changed. The same statements with the re-run figures:

* Hourly re-targeting: -4.15 to +1.80 after costs, 1.74 to 2.83 before,
  turnover up to 9.6x a day. Twelve-hour smoothing takes the 20-symbol
  long/short trend from 1.16 to 1.65.
* Smoothed trend, horizons of two days or more (27 configurations):
  1.42-1.99. Horizons of 1-4 days: 0.77-1.34.
* Cross-sectional momentum alone: 1.44-2.06 with 39-49% drawdowns. Added to
  trend: 1.94-2.18 against 1.78 for trend alone; at doubled costs 1.67
  against 1.46.
* Carry: -0.58 to -0.09. Reversal: -2.53 to -1.36 without a trend filter,
  -1.02 to +0.25 with one.
* No-trade band 10/25/50%: 1.80/1.78/1.60. Correlation 0.5/0.7/0.9:
  1.74/1.78/1.79. Volatility window 7/14/30 days: 1.76/1.78/1.59.
  Conviction-weighted sizing: 1.44-1.64. Eight-hour cadence by starting
  hour: 1.45-1.90, mean 1.69.
* Placebos, now run on T0 itself (the line above was for the symmetric
  long/short variant): as designed 1.78; 1 / 2 / 6 / 24 hours late
  1.74 / 1.66 / 1.38 / 1.38; weeks shuffled 0.05.
* Engine cross-check on T0 without governor or stops: Sharpe 1.74 against
  1.78 for the linear backtest; +100%, +76%, +37% and +78% in the four
  TRAIN years.
* Frozen candidates on TRAIN: T0 1.78, X0 1.98, B0 1.99.
