# Mean-reversion family: research notes

Module: `src/tradebot/strategies/meanrev.py` (strategies `meanrev_dip`, `meanrev_dip_multi`).
Data: `data/research/BTCUSDT-1h-research.csv` (2017-08-17 .. 2024-12-31). No later data was downloaded or used.
Costs and risk overlay: repository defaults (0.10% fee + 3 bps slippage per side; 10% min rebalance; circuit breaker 10% / 48h; campaign stop -15%; re-arm on). Primary numbers use these defaults.
Score: mean log(1+r) of 30-day campaigns started daily (`research.evaluate_period`). Tuning used TRAIN (2018-2023) only. VALID (2024) was run once, after the recommendation was fixed.

## Summary

* **Short-horizon mean reversion is a weak standalone strategy for BTC/USDT, and it does not beat buy-and-hold on score.** The best candidate, `meanrev_dip`, scores 0.0145 on TRAIN against 0.0168 for buy-and-hold. The gap is well inside noise (paired standard error about 0.02). On VALID 2024 it scores 0.0084 against 0.0391 for buy-and-hold.
* **Its risk profile is very different.** On TRAIN, `meanrev_dip` is in the market 4.9% of the time. It loses money in 5.5% of 30-day runs (buy-and-hold: 53%). Its p05 is -0.5% (buy-and-hold: -16.4%). Continuous max drawdown is 15% (buy-and-hold: 77%), and continuous Sharpe is 1.06 (buy-and-hold: 0.64). It had no losing year: 2018 and 2022 are 0, because the trend filter kept it flat.
* **Why pct_positive is low.** pct_positive is only 16%, because 78.5% of runs never trade (return exactly 0 counts as "not positive"). Of the runs that do trade, 74% make money.
* **The edge rests on very few trades.** There are 18 round trips in 6 years (continuous), with mean +6.7%, median +2.5%, 72% winners and a t-statistic of 2.4. The score's standard error is about 0.006. Every number in this file should be read with that in mind.
* **Parameter sensitivity is high.** Moving any one parameter by ±25% gives TRAIN scores of 0.003-0.014. The default is a local peak inside a plateau whose average is about 0.010.
* **The most useful finding is complementarity with trend following.** `max(trend_sma_band, meanrev_dip)` scores 0.0399 on TRAIN against 0.0374 for trend alone (CAGR 57% -> 66%, Sharpe 1.33 -> 1.43, same max drawdown). The repo's `blend` strategy takes a weighted *average*, and that dilutes both signals: a 2:1 average scores 0.0276. See *Combining with trend*.
* **Recommended (pre-registered on TRAIN): `meanrev_dip`.** `meanrev_dip_multi` is kept as the higher-activity variant. It is dominated on TRAIN, and on VALID it was flat (0.0023).

## How the strategies work

Dip measure (both strategies), with `n` in hours:

`z = ln(close / SMA_n(close)) / (sigma_1h * sqrt(n))`

`sigma_1h` is the standard deviation of hourly log returns over the last 30 days. It is fixed and was not tuned. `z = -1.5` means the close is 1.5 typical `n`-hour moves below its `n`-hour mean, in any volatility regime.

### `meanrev_dip` (4 parameters, recommended)

* A **dip bar** is one where `z_72 < -1.5` *and* the close is above its 125-day SMA of hourly closes (the uptrend filter).
* `target = 1` from a dip bar until 192 hours (8 days) after the **most recent** dip bar, and 0 otherwise. A dip that keeps deepening therefore keeps the position open.
* Defaults: `lookback_hours=72, entry_z=1.5, hold_hours=192, trend_days=125`.

### `meanrev_dip_multi` (4 parameters)

Same rule, but a dip on **any** of three horizons triggers: `base_hours` x (1, 3, 7) = 24 / 72 / 168 hours. Defaults: `base_hours=24, entry_z=1.5, hold_hours=192, trend_days=125`. It makes about 2.5x as many entries, with a smaller gain per trade.

### Engineering properties (both)

* Finite-memory rolling windows only. `warmup_bars` = 3192. For 300 random cut points, a window of exactly `warmup_bars` reproduces the full-history target with 0 mismatches.
* `research.check_causal(strategy, df, n_checks=30)` returns `[]` for seeds 7, 1, 2 and 3. The repo's `tests/test_strategies.py` contract and causality tests pass.
* `signals()` on the full 64,525-bar dataset takes 0.02 s.
* `compute()` raises on non-1h candles, because every window is defined in hours or days. `validate()` rejects out-of-range parameters.
* The target is NaN (flat) until every indicator exists. After that it is exactly 0 or 1, so the 10% minimum-rebalance rule never blocks a trade.

## Why it works (TRAIN event study)

Forward returns are measured from the next bar's open. Regime: close above or below its 150-day SMA.

| condition (72h z) | next 24h | next 72h | next 168h |
|---|---|---|---|
| uptrend, all bars | +0.26% | +0.75% | +1.53% |
| uptrend, z in (-2, -1.5] | +1.82% | +4.13% | +6.27% |
| uptrend, z <= -2 | +2.08% | +3.10% | +2.89% |
| downtrend, all bars | -0.01% | -0.01% | +0.16% |
| downtrend, z in (-2, -1.5] | -0.19% | -2.03% | -2.97% |
| downtrend, z <= -2 | -0.66% | -3.01% | -5.75% |

Dips inside an uptrend are followed by a week of returns several times the unconditional drift. Dips inside a downtrend keep falling (momentum). The trend filter is therefore not optional. Without it, all 9 variants tested lost money (score -0.025 to -0.003).

In an uptrend, *overbought* readings (z > 1.5) are also followed by above-average returns. That is momentum, so "sell the rip" variants were not pursued.

## TRAIN (2018-01-01 .. 2023-12-31), default risk overlay

| | meanrev_dip | meanrev_dip_multi | buy_hold |
|---|---|---|---|
| **score** | **0.0145** | **0.0107** | 0.0168 |
| pct_positive | 16.0% | 24.6% | 47.2% |
| runs with no trade | 78.5% | 60.5% | 0% |
| runs losing money | 5.5% | 14.9% | 52.8% |
| mean return | +1.61% | +1.35% | +3.36% |
| median return | 0.00% | 0.00% | -1.17% |
| p05 | -0.46% | -10.65% | -16.41% |
| worst campaign | -15.3% | -23.8% | -29.8% |
| worst campaign max DD | 21.5% | 26.2% | 35.7% |
| continuous CAGR | 19.5% | 19.0% | 22.9% |
| continuous Sharpe | 1.06 | 0.76 | 0.64 |
| continuous max DD | 15.3% | 32.4% | 76.9% |
| round trips (continuous, 6y) | 18 | 49 | 131* |
| trades (continuous, 6y) | 36 | 98 | 263 |
| entries per 30-day run | 0.28 | 0.75 | 2.1 |
| time in market | 4.9% | 12.4% | 88.0% |
| trip win rate / profit factor | 72% / 5.6 | 61% / 1.8 | 40% / 1.1 |
| average hold | 143 h | 133 h | 333 h |
| round trips closed by circuit breaker | 5 of 18 | 22 of 49 | most |

\* Buy-and-hold round trips come from the circuit breaker flattening and re-entering.

Per-year score (campaigns grouped by start year), with pct_positive in brackets:

| year | meanrev_dip | meanrev_dip_multi | buy_hold |
|---|---|---|---|
| 2018 | 0.0000 (0%) | +0.0071 (6%) | -0.0683 (27%) |
| 2019 | +0.0308 (21%) | +0.0043 (25%) | +0.0576 (57%) |
| 2020 | +0.0168 (30%) | +0.0359 (58%) | +0.1198 (69%) |
| 2021 | +0.0122 (29%) | -0.0091 (30%) | -0.0108 (38%) |
| 2022 | 0.0000 (0%) | 0.0000 (0%) | -0.0499 (35%) |
| 2023 | +0.0285 (17%) | +0.0272 (29%) | +0.0553 (59%) |
| positive / negative years | 4 / 0 | 4 / 1 | 3 / 3 |

`meanrev_dip` made no trades in 2018 or 2022: the 125-day filter kept it out of both bear markets.

Statistical caveat: the score's standard error is 0.006 for `meanrev_dip` and 0.009 for the multi variant. I estimated it from non-overlapping 30-day blocks, averaged over the 30 block offsets. The paired difference against buy-and-hold is -0.002 ± 0.021. The continuous round trips have mean +6.7%, median +2.5% and standard deviation 11.9%, so t = 2.4 on 18 trips. The best two trips (+35% and +32%) supply a large share of the total.

## VALID (2024), evaluated once after the recommendation was fixed

| | meanrev_dip | meanrev_dip_multi | buy_hold |
|---|---|---|---|
| **score** | **0.0084** | **0.0023** | 0.0391 |
| pct_positive | 16.7% | 27.1% | 55.4% |
| runs with no trade | 73.8% | 46.4% | 0% |
| runs losing money | 9.5% | 26.5% | 44.6% |
| mean return | +0.89% | +0.30% | +5.32% |
| median return | 0.00% | 0.00% | +2.65% |
| p05 | -1.97% | -6.65% | -15.63% |
| worst campaign max DD | 9.6% | 16.2% | 23.5% |
| continuous CAGR | 7.0% | -3.4% | 71.3% |
| continuous Sharpe | 0.48 | 0.00 | 1.35 |
| continuous max DD | 9.8% | 19.9% | 33.1% |
| round trips | 4 | 13 | 14 |
| trades | 8 | 26 | 29 |
| time in market | 9.0% | 19.6% | 92.3% |

The ranking matches TRAIN (`meanrev_dip` ahead of `meanrev_dip_multi`). Both stay positive with small tails, but neither comes close to buy-and-hold in a strong bull year. `meanrev_dip`'s four 2024 trades returned -2.0%, +4.3%, +6.9% and -2.1%. No parameter was changed after seeing these numbers.

## Parameter sensitivity (TRAIN score; each parameter moved ±25%, others at default)

| strategy | parameter (default) | -25% | default | +25% |
|---|---|---|---|---|
| meanrev_dip | lookback_hours (72) | 0.0067 | 0.0145 | 0.0126 |
| | entry_z (1.5) | 0.0032 | 0.0145 | 0.0098 |
| | hold_hours (192) | 0.0076 | 0.0145 | 0.0141 |
| | trend_days (125) | 0.0087 | 0.0145 | 0.0106 |
| meanrev_dip_multi | base_hours (24) | 0.0029 | 0.0107 | 0.0056 |
| | entry_z (1.5) | -0.0026 | 0.0107 | 0.0089 |
| | hold_hours (192) | 0.0032 | 0.0107 | 0.0079 |
| | trend_days (125) | 0.0144 | 0.0107 | 0.0058 |

The averages over the 9-point ±25% star are 0.0097 for `meanrev_dip` and 0.0063 for the multi variant. The weakest direction is always a **lower `entry_z`**: shallower dips add many low-quality trades, more of them crash continuations that trip the circuit breaker.

Plateau evidence behind the defaults (engine, TRAIN):

* **Coarse grid**, 81 cells: lookback 48/72/96 x entry_z 1.25/1.5/1.75 x hold 120/168/216 x trend 100/150/200 days. Mean 0.0050, range -0.0033..0.0127.
  * Hold length matters most: 120 h averages 0.0020, 168-216 h average 0.0063.
  * A 200-day trend filter is worse (average 0.0032) than 100-150 days (0.0057).
* **Fine grid** at entry_z 1.5: lookback 60/72/84 x hold 144-240 x trend 125/150. The block lookback 72-84, hold 168-240, trend 125 scores 0.0115-0.0162.
* **Choosing the centre by star average.** I also ranked candidate centres by their ±25% star average instead of the centre value: (84, 1.5, 192, 125) 0.0106; **(72, 1.5, 192, 125) 0.0097**; (84, 1.5, 216, 125) 0.0090; (72, 1.5, 216, 125) 0.0083; (84, 1.5, 216, 150) 0.0077. These differences are noise. I kept the rounder 72 h / 8-day choice.

## Risk-overlay sensitivity (TRAIN score / p05 / worst campaign max DD)

| overlay | meanrev_dip | meanrev_dip_multi | buy_hold |
|---|---|---|---|
| **default: CB 10% / 48h, campaign stop 15%** | **0.0145 / -0.5% / 21.5%** | **0.0107 / -10.7% / 26.2%** | **0.0168 / -16.4% / 35.7%** |
| CB 10%, no campaign stop | 0.0146 / -0.4% / 21.5% | 0.0119 / -9.4% / 34.3% | |
| CB 15%, campaign stop 15% | 0.0117 / -1.5% / 20.0% | 0.0003 / -15.5% / 29.0% | |
| CB 15%, no campaign stop | 0.0136 / -0.5% / 20.7% | 0.0027 / -11.6% / 41.4% | |
| CB off (0.99), campaign stop 15% | 0.0156 / 0.0% / 17.0% | 0.0040 / -15.8% / 31.5% | |
| CB off, no campaign stop | 0.0156 / 0.0% / 17.0% | 0.0065 / -10.3% / 56.2% | 0.0139 / -29.5% / 62.3% |
| CB 10% / 24h halt | 0.0134 | 0.0082 | |
| CB 10% / 96h halt | 0.0124 | 0.0056 | |

Continuous CAGR / max DD for `meanrev_dip`: CB 10% 19.5% / 15.3%; CB 15% 17.8% / 27.6%; CB off 21.1% / 17.0%.

* **For `meanrev_dip`, the default circuit breaker costs a little** (0.0145 vs 0.0156 with the breaker off) and does not improve its tail. A dip buy is *expected* to sit through an adverse move before it rebounds. A 10% breaker sometimes flattens it near the low, then pauses 48h through the rebound. 5 of the 18 continuous round trips ended this way.
* **For `meanrev_dip_multi`, the 10% breaker helps a lot** (0.0107 vs 0.004-0.007 without it). Its 24-hour horizon catches the early part of real crashes, and the breaker cuts those losses.
* **A 15% breaker is the worst setting for both.** It fires late, near the bottom of a move, which is the same pattern the trend family found.
* **The campaign stop barely matters for `meanrev_dip`.** It fires in 0.05% of campaigns.
* Primary numbers keep the defaults. If `meanrev_dip` were deployed alone, `circuit_breaker_dd = 0.99` would be the better setting on TRAIN. The difference is about 0.2 standard errors, so I would not change the default for it.

## Fee drag and other robustness checks (TRAIN score)

| check | meanrev_dip | meanrev_dip_multi | buy_hold |
|---|---|---|---|
| baseline | 0.0145 | 0.0107 | 0.0168 |
| costs x0 (no fee, no slippage) | 0.0153 | 0.0123 | 0.0215 |
| costs x2 (0.2% fee + 6 bps) | 0.0137 | 0.0088 | |
| signal delayed 1 bar | 0.0129 | 0.0087 | |
| signal delayed 6 bars | 0.0127 | 0.0043 | |
| campaigns anchored at 00/06/12/18 UTC | 0.0144-0.0146 | 0.0104-0.0107 | 0.0170-0.0180 |

* **Fee drag is small because the strategy trades rarely.** It costs 0.0008 of score (5%) for `meanrev_dip` and 0.0016 (15%) for the multi variant. That is about 0.06% and 0.15% of capital per 30-day run. Each round trip costs about 0.26%, against a mean gain per trip of about 6.7% for `meanrev_dip` and 2.5% for the multi variant.
* **Short-horizon MR is where fees bite.** The most active variant tried had 24h z-score entries, a reversion exit and no trend filter, at about 7 fills per 30-day run. It paid about 0.9% of capital per run in costs and scored -0.018. Hourly RSI(3) < 20 with a 24h hold traded 338 times in 6 years, at 15% of capital per year in costs, for +0.33% net per trade.
* The anchored-start rows use a scratch runner with a fixed daily start hour; see harness note 2 for why that was needed.

## Combining with trend (TRAIN only, information for model selection)

| | score | p05 | continuous CAGR | max DD | Sharpe |
|---|---|---|---|---|---|
| trend_sma_band | 0.0374 | -11.2% | 57.3% | 42.0% | 1.33 |
| **max(trend_sma_band, meanrev_dip)** | **0.0399** | -11.5% | 66.3% | 42.0% | 1.43 |
| trend_sma_band_regime | 0.0367 | -9.9% | 55.3% | 39.4% | 1.40 |
| **max(trend_sma_band_regime, meanrev_dip)** | **0.0392** | -10.4% | 64.1% | 35.2% | 1.50 |
| blend 2/3 trend_sma_band + 1/3 meanrev_dip (repo `blend`) | 0.0276 | -7.6% | 41.4% | 28.0% | 1.31 |
| max(trend_sma_band, meanrev_dip_multi) | 0.0307 | -15.3% | 48.7% | 55.0% | 1.12 |

The two exposures are uncorrelated (correlation 0.03 on TRAIN). When `meanrev_dip` is long, the trend strategy is not fully invested 52% of the time, with an average exposure of 0.54, because a sharp dip is exactly when the band ensemble steps out. Taking the **maximum** of the two targets lets the dip sleeve fill that gap. That adds +0.0025 of score with a paired standard error of 0.0026, so the evidence is weak, about 1 SE. 2023 gains the most. A **weighted average** (the only combiner in `strategies/blend.py`) halves both signals and loses about 0.01. This combination was *not* evaluated on VALID. I left VALID untouched for the selection step. A "max" combiner would need a change to `blend.py`, which was outside this task.

## What I tried, including what failed (TRAIN)

Engine = official `run_campaigns` score with the default overlay. Fast screen = continuous CAGR from a scratch vectorised backtest with the same 10% / 48h circuit breaker. I validated the screen against the engine's continuous CAGR to 0.1 pp (for example 8.9% vs 8.9%, and 6.2% vs 6.2%).

| idea | result | verdict |
|---|---|---|
| Vol-normalised dip (z of 24/72/168h), exit when z > 0, 72h time stop, **no trend filter** | Engine score -0.025 to -0.004. All years negative or flat | Rejected. Dips in downtrends continue |
| Same with a 150-day trend filter | -0.007 to +0.007. Time in market 1-9% | Exit too early, see next row |
| **Reversion exits** on the final candidate: exit at z > 0 / 0.5 / 1, else time stop | 0.0035 / 0.0056 / 0.0105 vs **0.0145** with a time stop only | Rejected. The post-dip drift continues well past the mean |
| **Protective stops** (`stop_dist`): 4 / 6 / 8 / 12% of price; 1x daily ATR | 0.0003 / 0.0023 / 0.0045 / 0.0138; 0.0035. Wider stops never trigger | Rejected. The stop plus re-arm locks the strategy out of the rebound |
| Hold-time profile (72h z, k 1.5) | Fast screen with CB: 48h 7.9%, 96h 11.1%, 120h 7.7%, 168h 17.9%, 192h 18.6%, 240h 14.8%, 336h 12.6%. Holding for months turns it into trend following | Hold of about 1 week **adopted** |
| Regime filter type: close > SMA 100/150/200d, SMA rising, SMA50 > SMA150, 90-200d momentum, close > SMA50 and SMA150 | Single-horizon designs swing from -6% to +21% CAGR across filter types (fast screen). Close > SMA 100-150d is best on average; 200d is clearly worse | **Adopted**: close > SMA, 125 days (centre of 100-150) |
| Uptrend margin (close > SMA x 1.05 / 1.10 / 1.20) | All lower than margin 0 | Rejected |
| Entry confirmation: first green bar, z hooking up by 0.5-1, no new 6/12-bar low | No gain on average. Fewer trades, worse fills | Rejected |
| Panic exit (z < -k-1 .. -k-2, then wait to re-arm), exit on trend break | No gain (the best was +0.9 pp CAGR at one cell) | Rejected |
| Volatility-targeted sizing, min(1, 40-80% / realised vol) | Always lower, for example 0.0122 -> 0.0045-0.0087 | Rejected. Dips happen in high volatility, so sizing down cuts the edge |
| Volatility regime filters (30d vol < 60/80/100%, vol-ratio caps) | No gain; 2021 is still the weak year | Rejected |
| Linear exposure decay after the dip (full for 0-96h, then down to 0 at 168-336h), instead of a hard time stop | Best cell 14.3% CAGR, against 17.9-18.6% for the hard stop (fast screen with CB) | Rejected |
| **Multi-horizon dip** (any of 24/72/168h, or 16-48h x 1/3/7) | Engine -0.001 to 0.0125 for entry_z >= 1.5. More trades, more robust to the choice of trend filter (fast screen), lower score | Kept as `meanrev_dip_multi` |
| Parameter ensembles (mean or max of 9 sub-signals) | Mean 0.0023-0.0070, max 0.0018-0.0117 | Rejected. Fractional exposure adds fee churn and dilutes the edge |
| **Connors-style daily RSI(2/3) < 10-30**, daily close > SMA 100-200d, exit when the close is above the 5/10-day SMA or after 5/10 days | Fast screen without CB up to 19.7% CAGR; engine **0.0032** with the default overlay (0.0082 with CB off, 0.0116 with CB and campaign stop off) | Rejected. It buys falling knives, so the overlay stops it out at the low |
| Hourly RSI(2/3/5) < 5-20 with a trend filter | Up to 35% CAGR (fast screen without CB), but at about 50% time in market, because hourly RSI(2) < 10 fires every few days | Not mean reversion. It turns into the trend filter itself (engine 0.0269 for "close > SMA150d, always long") |
| N consecutive down closes (daily 2-4, 4h 4-6) in an uptrend | Engine 0.0088 for 3 daily down closes with a 168h hold. The fast-screen grid is noisy (-7% to +26% CAGR). Daily k=2 drifts into trend following (29% time in market) | Rejected. Not robust |
| Drawdown from the 3-14 day high (5-12%), exit on a new high or a 120/240h time stop | -5% to +12% CAGR without CB (fast screen) | Rejected. Weaker than the vol-normalised z |
| Unions of different MR signals (z-dips, daily RSI, streaks) | Engine 0.006-0.010 | Rejected. Each added signal brings more circuit-breaker losses than gains |

## Caveats

* **Small sample.** There are 18 TRAIN round trips (4 in VALID). The difference between `meanrev_dip` and buy-and-hold, and between neighbouring parameter cells, is mostly noise.
* **Bull-market dependence.** All of the edge comes from dips inside uptrends. In bear markets the strategy sits flat, which is good for the tail, but no score is earned there.
* **Low pct_positive.** pct_positive is low by construction: most 30-day runs never trade. Read it together with "runs losing money": 5.5% TRAIN and 9.5% VALID for `meanrev_dip`.
* **Holdout untouched.** The 2025+ holdout was not touched.

## Engine and harness notes (no code changed)

1. **Forward-run slack when `warmup_bars > data.lookback_bars`.** This is the same issue the trend family reported. Both strategies have `warmup_bars = 3192`, which is above the default `lookback_bars = 3000`. `forward.tick` fetches `max(3000, 3192 + 2)` = 3194 bars and replays only bars with index >= 3192, so if 3 or more hourly ticks are missed, the extra bars are skipped as "offline longer than the lookback window". Workaround: set `[data] lookback_bars` to about 4000 when running a meanrev strategy.
2. **The `run_campaigns` start schedule drifts after data gaps.** Each next start is `start_ms + step`, where `start_ms` is the first bar found at or after the intended time. After a gap, every later campaign shifts to the post-gap hour. On TRAIN only 39 of 2,161 campaigns start at 00:00 UTC; most start at 18:00, 12:00, 17:00 or 13:00. This does not bias the score (a calendar-anchored scratch runner gives the same numbers within 0.001). It does mean that passing a different start hour to `run_campaigns` converges to the same schedule after the first gap, so it is not an independent start-time test. Suggested fix: `t = first_start + k * step` (anchored), not `t = start_ms + step`.
3. **The harness changed during this research.** `engine.py`, `backtest.py` and `metrics.py` changed: trip-return basis, campaign end index, and the CAGR year count. All numbers in the tables above were produced on the current code (buy-and-hold TRAIN still reproduces 0.0168). Some early exploratory grids ran before the change. Campaign returns are unaffected on gap-free data, and I re-checked the final candidates on the current code.
4. **Design note, not a bug: the circuit breaker treats a dip buy's normal adverse excursion as a failure.** For dip-buying the default 10% breaker is roughly neutral to slightly harmful (see the overlay table). If the overlay ever becomes per-strategy, mean-reversion sleeves should get a wider or disabled breaker.
