# Trend family: research notes

Module: `src/tradebot/strategies/trend.py` (strategies `trend_sma_band`, `trend_sma_band_regime`, `trend_strength`).
Data: `data/research/BTCUSDT-1h-research.csv` (2017-08-17 .. 2024-12-31). No later data was downloaded or used.
Costs and risk overlay: repository defaults (0.10% fee + 3 bps slippage per side; 10% min rebalance; circuit breaker 10% / 48h; campaign stop -15%; re-arm on).
Score: mean log(1+r) of 30-day campaigns started daily (`research.evaluate_period`).

## Summary

* All three final candidates roughly **double the buy-and-hold score on TRAIN** (0.034-0.037 vs 0.017). They cut the 5th-percentile 30-day outcome from -16.4% to between -8.3% and -11.2%, and continuous max drawdown from 77% to 39-42%. Continuous Sharpe goes from 0.64 to 1.33-1.40.
* **Recommended (pre-registered on TRAIN, before VALID was run): `trend_sma_band_regime`.** Its TRAIN score equals `trend_sma_band` within noise (the difference is 0.0006, standard error about 0.003). It is better on every risk measure: p05, continuous Sharpe, continuous max drawdown, and the 2022 bear year. `trend_sma_band` is the simpler alternative (4 parameters instead of 6).
* VALID 2024 (run once, at the end): `trend_sma_band` 0.046, `trend_sma_band_regime` 0.043, `trend_strength` 0.031, buy-and-hold 0.039. 2024 was a strong bull year. Both band strategies beat buy-and-hold on score with a much smaller loss tail (p05 -8.9% vs -15.6%), but made money in fewer of the 30-day runs (47-49% vs 55%). `trend_strength` lagged.
* The edge is real but not large. The score's standard error is about 0.015. The gap over buy-and-hold is +0.020 with a paired standard error of about 0.012 (roughly 1.7 sigma).
* Bear years are not solved. 2018 and 2022 are still slightly negative (-0.010 to -0.023 log per campaign), but much better than buy-and-hold (-0.068 and -0.050). Positive years: 4 of 6 for every candidate (buy-and-hold: 3 of 6).

## Final candidates

### `trend_sma_band` (4 parameters)
Three simple moving averages of the **hourly** close, at `lookback_days / spread`, `lookback_days` and `lookback_days * spread` (defaults 20, 30 and 45 days). Each one votes:

* long once `close > SMA * (1 + entry_band)` (default +2%),
* flat once `close < SMA * (1 - exit_band)` (default -0.5%),
* otherwise it keeps its previous vote (hysteresis).

Target exposure is the share of long votes (0, 1/3, 2/3 or 1). A wide entry band confirms a breakout before buying. A tight exit band gets out quickly once the trend fails. A single vote change moves exposure by 1/3, so the 10% minimum-rebalance rule never blocks it.

### `trend_sma_band_regime` (6 parameters, recommended)
`trend_sma_band` with the exposure multiplied by `regime_floor` (0.5) whenever the hourly close is below its `regime_days` (150-day) SMA. Until the 150-day SMA exists, the regime is treated as bearish, which is the conservative choice. Warm-up is 3600 bars, more than the default `data.lookback_bars = 3000`. `forward.tick` already fetches `max(lookback_bars, warmup_bars + 2)` bars, so this is handled, but read the slack caveat under *Engine and harness notes*.

### `trend_strength` (3 parameters)
This one runs on a daily cadence, using daily closes from `resample_causal(df, '1D')`:
`z = ln(close_d / SMA_d(40)) / (sigma_d(30) * sqrt(40))` and `target = clip(z / 0.5, 0, 1)`, held for the day. Exposure is fractional and normalised by volatility. Its loss tail is the smallest of the three (p05 -8.3%) and it uses the fewest parameters. Its score is slightly lower, and it was weaker in VALID.

### Engineering properties (all three)
* Finite-memory indicators only (rolling means and standard deviations, no recursive EMAs). With `warmup_bars` of history, the live target is identical to the full-history backtest. I checked this on 300 random cut points per strategy using a window of exactly `warmup_bars`, and also of 3000 bars: 0 mismatches. The one exception is the regime variant with a 3000-bar window, which is why its `warmup_bars` is 3600.
* `research.check_causal(strategy, df, n_checks=30)` returns `[]` for all three, and also for seeds 1, 2 and 3. The repo test suite passes; it includes the contract and causality tests run on every registered strategy.
* `signals()` on the full 64,525-bar dataset takes 0.01-0.03 s.
* `compute()` raises if the candles are not 1h. Every lookback is defined in days, so on other bar sizes the strategy would silently trade the wrong horizon.
* `validate()` rejects out-of-range parameters.

## TRAIN (2018-01-01 .. 2023-12-31), default risk overlay

| | trend_sma_band | trend_sma_band_regime | trend_strength | buy_hold |
|---|---|---|---|---|
| **score** | **0.0374** | **0.0367** | **0.0341** | 0.0168 |
| pct_positive | 43.9% | 42.5% | 35.8% | 47.2% |
| mean return | 4.82% | 4.62% | 4.27% | 3.36% |
| median return | -0.49% | -0.53% | 0.00% | -1.17% |
| p05 | -11.20% | -9.87% | -8.28% | -16.41% |
| worst campaign max DD | 24.4% | 24.4% | 25.8% | 35.7% |
| continuous CAGR | 57.5% | 55.5% | 53.6% | 22.9% |
| continuous Sharpe | 1.33 | 1.40 | 1.40 | 0.64 |
| continuous max DD | 42.0% | 39.4% | 41.3% | 76.9% |
| round trips (continuous, 6y) | 114 | 107 | 98 | 131* |
| trades (continuous, 6y) | 441 | 516 | 487 | 263 |
| time in market | 54% | 55% | 47% | 88% |

\* Buy-and-hold round trips come from the circuit breaker flattening and re-entering.

Per-year score (campaigns grouped by start year), with pct_positive in brackets:

| year | trend_sma_band | trend_sma_band_regime | trend_strength | buy_hold |
|---|---|---|---|---|
| 2018 | -0.0147 (30%) | -0.0149 (26%) | -0.0215 (13%) | -0.0683 (27%) |
| 2019 | +0.0720 (52%) | +0.0713 (53%) | +0.0609 (41%) | +0.0576 (57%) |
| 2020 | +0.1098 (63%) | +0.1076 (63%) | +0.1130 (62%) | +0.1198 (69%) |
| 2021 | +0.0291 (41%) | +0.0226 (40%) | +0.0305 (40%) | -0.0108 (38%) |
| 2022 | -0.0183 (26%) | -0.0099 (22%) | -0.0226 (10%) | -0.0499 (35%) |
| 2023 | +0.0471 (53%) | +0.0441 (52%) | +0.0451 (51%) | +0.0553 (59%) |
| positive years | 4/6 | 4/6 | 4/6 | 3/6 |

pct_positive counts flat campaigns (return exactly 0) as "not positive". Strategies that sit out bear markets therefore show low pct_positive in 2018 and 2022 even when they lose little.

## VALID (2024), evaluated once after the recommendation was fixed

| | trend_sma_band | trend_sma_band_regime | trend_strength | buy_hold |
|---|---|---|---|---|
| **score** | **0.0460** | **0.0431** | **0.0308** | 0.0391 |
| pct_positive | 48.8% | 46.7% | 39.3% | 55.4% |
| mean return | 5.60% | 5.29% | 3.92% | 5.32% |
| median return | -0.23% | -0.56% | -0.87% | +2.65% |
| p05 | -8.91% | -8.84% | -11.40% | -15.63% |
| worst campaign max DD | 19.8% | 19.8% | 23.4% | 23.5% |
| continuous CAGR | 62.0% | 57.7% | 31.4% | 71.3% |
| continuous Sharpe | 1.51 | 1.46 | 0.99 | 1.35 |
| continuous max DD | 27.4% | 29.0% | 35.5% | 33.1% |
| round trips | 14 | 14 | 16 | 14 |
| trades | 73 | 116 | 112 | 29 |
| time in market | 65% | 65% | 61% | 92% |

The ranking matches TRAIN. No parameter was changed after seeing these numbers.

## Parameter sensitivity (TRAIN score; each parameter moved ±25%, others at default)

| strategy | parameter (default) | -25% | default | +25% |
|---|---|---|---|---|
| trend_sma_band | lookback_days (30) | 0.0333 | 0.0374 | 0.0358 |
| | spread (1.5; scaled around 1, so 1.375 / 1.625) | 0.0368 | 0.0374 | 0.0359 |
| | entry_band (0.02) | 0.0380 | 0.0374 | 0.0379 |
| | exit_band (0.005) | 0.0375 | 0.0374 | 0.0367 |
| trend_sma_band_regime | lookback_days (30) | 0.0321 | 0.0367 | 0.0351 |
| | spread (1.5) | 0.0361 | 0.0367 | 0.0356 |
| | entry_band (0.02) | 0.0378 | 0.0367 | 0.0369 |
| | exit_band (0.005) | 0.0373 | 0.0367 | 0.0363 |
| | regime_days (150) | 0.0360 | 0.0367 | 0.0344 |
| | regime_floor (0.5) | 0.0362 | 0.0367 | 0.0374 |
| trend_strength | sma_days (40) | 0.0311 | 0.0341 | 0.0356 |
| | vol_days (30) | 0.0350 | 0.0341 | 0.0345 |
| | full_z (0.5) | 0.0369 | 0.0341 | 0.0316 |

Every ±25% move stays within 0.005 of the default, and every perturbed variant still beats buy-and-hold by about 2x. The coarse grids behind the defaults (scratch, TRAIN):

* **Band ensemble, entry x exit band at lookback 30.** Scores are 0.036-0.039 for entry 1-3% with exit 0-1%. Exit bands of 3-5% fall to 0.031-0.034. With no band at all (plain SMA cross) the score is 0.0295, at 25 trades per campaign.
* **Band ensemble, lookback x spread.** Lookbacks of 20-50 days give 0.031-0.037. 15 and 60 days fall off (0.025-0.031). Spread 1.5 usually gives a p05 1-3 pp better than a single SMA.
* **Regime filter.** 120/150/200 days with floor 0.33/0.5/0.67 give 0.035-0.0375.
* **trend_strength.** Lookbacks of 40-50 days with full_z 0.375-0.5 give 0.032-0.037, and vol_days is flat from 20 to 40. The 30-day lookback is lower (0.027-0.033).

I took the defaults from the centre of these plateaus, not from the best cell. For example, entry 0.75x scores 0.0380, but the default stays at 2%.

## Risk-overlay sensitivity (TRAIN score / p05 / continuous max DD)

| overlay | trend_sma_band | trend_sma_band_regime | trend_strength |
|---|---|---|---|
| **default: CB 10%/48h, campaign stop 15%** | **0.0374 / -11.2% / 42.0%** | **0.0367 / -9.9% / 39.4%** | **0.0341 / -8.3% / 41.3%** |
| CB 10%, no campaign stop | 0.0373 / -10.8% / 42.0% | 0.0366 / -9.8% / 39.4% | 0.0342 / -8.2% / 41.3% |
| CB 15%, campaign stop 15% | 0.0302 / -11.7% / 42.9% | 0.0286 / -10.8% / 42.4% | 0.0305 / -8.7% / 42.4% |
| CB 15%, no campaign stop | 0.0314 / -11.3% / 42.9% | 0.0298 / -10.4% / 42.4% | 0.0315 / -8.4% / 42.4% |
| CB off (0.99), campaign stop 15% | 0.0344 / -11.9% / 42.3% | 0.0329 / -10.7% / 37.5% | 0.0338 / -8.7% / 40.2% |
| CB off, no campaign stop | 0.0350 / -11.5% / 42.3% | 0.0335 / -10.3% / 37.5% | 0.0348 / -8.4% / 40.2% |
| CB 10% / 24h halt | 0.0351 | 0.0356 | 0.0343 |
| CB 10% / 96h halt | 0.0304 | 0.0313 | 0.0283 |

* **The default circuit breaker helps** the band strategies by about 0.003 against no breaker, and is neutral for `trend_strength`. It works like a wide trailing stop that the trend signal re-arms after 48h.
* **A 15% breaker is worse than both 10% and none.** It fires late, near the bottom of a move, and then sits out the rebound.
* **Longer halts (96h) hurt** because they miss re-entries.
* **The campaign stop barely affects the score** (±0.001). It does cap the worst campaign drawdown: 24.4% with it, 30.0% without.
* Defaults are kept.

## Other robustness checks (TRAIN score)

| check | trend_sma_band | trend_sma_band_regime | trend_strength | buy_hold |
|---|---|---|---|---|
| baseline | 0.0374 | 0.0367 | 0.0341 | 0.0168 |
| costs x2 (0.2% fee + 6 bps) | 0.0326 | 0.0323 | 0.0310 | 0.0120 |
| costs x0 | 0.0421 | 0.0412 | 0.0375 | 0.0215 |
| signal delayed 1 bar | 0.0346 | 0.0350 | 0.0325 | 0.0168 |
| signal delayed 6 bars | 0.0334 | 0.0328 | 0.0329 | 0.0168 |
| campaigns start 06:00 / 12:00 / 18:00 UTC | 0.0374 | 0.0368 | 0.0342 | 0.0169-0.0170 |

The edge does not depend on exact bar timing (delaying the signal by 24 bars still scores 0.0325 for `trend_sma_band`) or on campaign start hour, and it survives doubled costs.

## What I tried, including what failed (TRAIN scores)

| idea | result | verdict |
|---|---|---|
| Single filter: close > SMA(N) or N-day momentum > 0, N = 10..200 days, hourly or daily cadence | 0.007-0.031. Best around 20-50 days. p05 stuck near -15% (the campaign stop). 2018/2022 negative | Baseline only. Hourly cadence without a band whipsaws (12-20 trades per campaign) |
| Multi-lookback momentum votes (mean of sign over 3-6 lookbacks, momentum and/or SMA) | 0.022-0.031 | Smoother but not better. More trades from partial-exposure flips |
| Vote threshold ("k of n agree", smooth AND) | 0.018-0.032 | Lower time in market, no score gain |
| Fast AND slow momentum (e.g. 10d AND 40d) | 0.035 at 10/40, but 0.025 at 8/40 and 0.028 at 10/60 | Rejected: a fragile point, not a plateau. Its ensembled version (product of breadths) is stable but only 0.024-0.033 |
| Volatility targeting, exposure x min(1, tv / realised vol), tv 0.4-1.0, vol window 10-30 days | Always lowers the score (for example 0.0374 -> 0.0339 at tv 0.6) while improving p05 by 1-4 pp | Not adopted. BTC's strongest trends (2020-21) are high-vol, and the circuit breaker already bounds the loss. `trend_strength` gets a similar tail benefit through vol-normalised sizing |
| Intrabar ATR trailing stop via `stop_dist` (1.5-5 x daily ATR14) | Monotonically worse the tighter it is (for example 0.0351 -> 0.0255 at 1.5 ATR) | Rejected. Stops fire on normal pullbacks, and the re-arm rule then locks the strategy out of a trend that is still on |
| Hysteresis bands on hourly SMA/momentum | Hourly SMA 20-40d: 0.028-0.031 without a band -> 0.034-0.037 with a 1-2% band, and 3-5 trades per campaign instead of 8-12. Daily cadence gains less | **Adopted** (`trend_sma_band`) |
| Asymmetric bands (entry > exit) on the 3-SMA ensemble | Entry 1-3%, exit 0-1%: 0.036-0.039 | **Adopted** (2% / 0.5%) |
| Long-term regime filter, fully flat in the bear regime (150/200d SMA, SMA slope, 100-200d momentum, 35% below 180/365d high) | 2022 near 0 (-0.012 to +0.005) and 2018 between -0.034 and +0.008, but the overall score drops to 0.020-0.034 from lost bull-market exposure | Rejected as a hard filter |
| Same filters with half exposure in the bear regime | 0.030-0.037 (150d SMA best at 0.0366). Better p05 (-9.4% to -10.5%) and continuous max DD (39%) | **Adopted** as `trend_sma_band_regime` (150d SMA, floor 0.5) |
| Vol-normalised distance from SMA (continuous z-signal) | Daily cadence, 40-50d: 0.032-0.037, p05 -8% to -10%. Hourly cadence: 16-49 trades per campaign, too much fee drag | **Adopted** daily as `trend_strength` |
| Blending the band ensemble with daily momentum breadth (mean / product / min) | 0.031-0.034 | No gain over the band ensemble alone |

## Caveats

* **Statistical power is limited.** The 2,161 TRAIN campaigns overlap heavily: roughly 72 are independent 30-day windows. The score's standard error is about 0.015, and differences under about 0.005 between configurations are noise. I chose between candidates on simplicity and tail risk, not on the third decimal of the score.
* **Losses in a chop.** The strategies still lose money in choppy bear markets. A bad 30-day run is expected to cost 5-15%, limited by the circuit breaker and the campaign stop.
* **Low pct_positive.** The probability that a single 30-day run makes money is below 50%: 43-44% on TRAIN and 47-49% on VALID for the band strategies. The positive score comes from gains being larger than losses (right-skewed returns), not from winning most months.
* **Holdout untouched.** The 2025+ holdout was not touched.

## Engine and harness notes (no code changed)

1. **Too little slack in `forward.tick` when `warmup_bars > data.lookback_bars`.** The window is `lookback = max(cfg.data.lookback_bars, warmup_bars + 2)`, and only bars with `i >= warmup_bars` are replayed. For `trend_sma_band_regime` (warmup 3600 > 3000) the fetched window is 3602 bars, so at most 2 new bars can be processed per tick. If the bot misses 3 or more hourly ticks, the extra bars are skipped with the message "bot was offline longer than the lookback window", even though it was offline only a few hours. Workaround: set `[data] lookback_bars` to at least `warmup_bars + 720` (for example 4400) when running this strategy. Suggested fix: `lookback = max(cfg.data.lookback_bars, strategy.warmup_bars + cfg.data.lookback_bars)` or a fixed slack. `trend_sma_band` (warmup 1416) and `trend_strength` (warmup 1008) are unaffected.
2. **The default strategy name does not exist.** `StrategyConfig.name` defaults to `"ensemble_trend"`, which is not registered (`available()` has no such strategy). A run with the default config would fail with `KeyError` until the selected strategy is set in the config.
3. **Low `worst_max_dd` is expected.** Campaign `max_drawdown` is measured from the campaign's peak equity, so it can exceed the 15% campaign stop: a run that is +10% and then falls 24% from its peak is still above -15% of initial capital. This is not a bug, but read `worst_max_dd` with it in mind.
