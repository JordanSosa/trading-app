# Regime family: research notes

Module: `src/tradebot/strategies/regime.py`. Strategies: `regime_volswitch` and `regime_confirm`.
Data: `data/research/BTCUSDT-1h-research.csv` (2017-08-17 .. 2024-12-31). No later market data was downloaded or used.
Costs and risk overlay: repository defaults. That is 0.10% fee plus 3 bps slippage per side, fills at the next open, and a 0.10 minimum rebalance. The circuit breaker goes flat for 48h when equity is 10% below its high-water mark. The campaign stop is at -15%. Re-arm is on.
Score: mean log(1+r) of 30-day campaigns started daily (`research.evaluate_period`).

## Summary

* Both final candidates are regime switches. A 150-day SMA and its 5-day slope classify every bar as **bull**, **transition** or **bear**. In a bull regime the strategy follows a *fast* trend rule (20-day SMA) at full size. In the other two regimes it needs a *slow*, confirmed trend signal (32-day SMA plus a 2% band). That position is sized by a volatility target (80% annualised, 7-day realised vol, in steps of 0.25).
* **TRAIN (2018-2023):** score 0.0440 (`regime_volswitch`) and 0.0430 (`regime_confirm`), against 0.0168 for buy-and-hold. Against buy-and-hold:
  * p05 is -10.5% / -9.7% (buy-and-hold -16.4%).
  * P(30-day loss > 10%) is 6.1% / 4.7% (32.7%).
  * Continuous max DD is 40.5% / 36.6% (76.9%).
  * Continuous Sharpe is 1.61 / 1.59 (0.64).
  * Paired difference vs buy-and-hold: +0.027 log per campaign. The Newey-West standard error (lag 30) is 0.008, so t is about 3.2. This number is before any correction for the search I ran.
* **Recommended (pre-registered on TRAIN, before VALID was run): `regime_confirm`.** Its TRAIN score equals `regime_volswitch` within noise: the paired difference is 0.0009, with a standard error of 0.0017. It is better on p05, P(loss>10%), continuous max DD and both bear years (2018 -0.0008 vs -0.0125, 2022 -0.0075 vs -0.0101). Its worst campaign is identical, and its pct_positive is 1 point lower.
* **VALID 2024 (run once):** `regime_volswitch` 0.0395, `regime_confirm` 0.0366, buy-and-hold 0.0391. 2024 was a strong bull year. The regime strategies matched buy-and-hold's score with a much smaller loss tail (p05 -10% vs -15.6%, P(loss>10%) about 5% vs 20.5%). They made money in fewer of the 30-day runs (48-49% vs 55%). `regime_volswitch` had a larger continuous max DD than buy-and-hold in 2024 (34.6% vs 33.1%). The drawdown ran from the 5 March peak to a 13 October trough, while BTC moved sideways between about 50k and 73k.
* **pct_positive is not improved.** On TRAIN it is 45-46% vs 47% for buy-and-hold. These strategies make money by cutting the left tail and riding trends, not by winning more often. About 8% of TRAIN campaigns end exactly flat.
* **Bear years are roughly break-even, not positive:** 2018 -0.0008 and 2022 -0.0075 for `regime_confirm`, against -0.068 / -0.050 for buy-and-hold. Positive TRAIN years: 4 of 6 for both candidates (buy-and-hold: 3 of 6).
* **Most "regime" ideas did not add value on their own.** The ones that failed:
  * dip-buying / mean reversion, standalone or blended into the trend exposure
  * cutting exposure when volatility spikes, applied everywhere
  * efficiency-ratio and ADX regime classifiers
  * price-drawdown crash filters
  * multi-lookback ensembles

  Three ideas worked: (1) a *faster* trend rule inside an established uptrend than outside it, (2) the SMA-plus-slope regime classifier, and (3) volatility targeting applied *only outside* the bull regime.
* **The edge leans on the default circuit breaker.** With the circuit breaker at 15% or disabled, the TRAIN score drops from 0.044 to 0.034-0.035. Keep `circuit_breaker_dd = 0.10`.

## Final candidates

| strategy | logic | parameters (6 tunable) |
|---|---|---|
| `regime_volswitch` | **bull** (close > SMA150 *and* SMA150 > its value 5 days ago): long while close > SMA20, flat once close < SMA20 x 0.995, full size. **transition / bear**: long once close > SMA32 x 1.02, flat once close < SMA32 x 0.995. Size = floor(min(1, 0.8 / vol7d) / 0.25) x 0.25, times `bear_weight` in bear. | regime_days=150, fast_days=20, slow_days=32, slow_band=0.02, vol_target=0.80, bear_weight=1.0 |
| `regime_confirm` (recommended) | Same, except that outside the bull regime **both** the SMA32 rule and the SMA20 rule must be long. Entry needs confirmation from both; exit comes on the first failure. | same defaults |

Fixed constants (not tuned): slope lookback 5 days (insensitive: 2-20 days gives 0.0437-0.0441 for `regime_volswitch` and 0.0427-0.0430 for `regime_confirm`), exit band 0.5%, vol window 7 days, size step 0.25, and 21 days of latch-sync slack in `warmup_bars`.

Engineering checks (both strategies):
* `research.check_causal(strategy, df, n_checks=30)` returns `[]`, including its window check at `warmup_bars + 1` bars (seeds 7, 1, 2).
* **Live-window invariance.** I took 300 random cut points per strategy. For each, I computed `signals()` on a window of exactly `max(3000, warmup_bars + 2)` = 4227 bars, the smallest window `forward.tick` accepts, and compared it with the full-history target. The result was 0 mismatches. I also compared every post-warm-up bar of 30 windows: again 0 mismatches. Only finite-memory rolling windows are used, and the hysteresis latches are fixed by any bar that crosses a band. The longest "ambiguous" stretch in the data is 106 bars for the slow latch, against 504 bars of sync slack.
* `signals()` on the full 64,525-bar file takes about 0.02 s.
* `compute()` raises if the bars are not 1h. `validate()` rejects out-of-range or inconsistent parameters, for example `fast_days >= regime_days`.
* Exposure takes the values 0, 0.5, 0.75 or 1, so every change is at least 0.25 and the 10% minimum-rebalance rule never strands a decision.
* `tests/test_strategies.py` passes. It includes the contract and causality tests run on every registered strategy.
* Cost robustness on TRAIN (`regime_confirm` / `regime_volswitch`):

  | fee + slippage per side | `regime_confirm` | `regime_volswitch` | buy-and-hold |
  |---|---|---|---|
  | 0.075% + 3 bps | 0.0442 | 0.0450 | 0.0179 |
  | 0.15% + 5 bps | 0.0391 | 0.0408 | 0.0140 |
  | 0.20% + 10 bps | 0.0351 | 0.0368 | 0.0104 |

**Deployment note.** `warmup_bars` = 4225 (155 days of regime window plus 21 days of sync slack), which is more than the default `data.lookback_bars = 3000`. The current `forward.tick` fetches `max(lookback_bars, warmup_bars + CATCHUP_BARS)` = 4945 bars, so an outage of up to 720 missed bars is still replayed with full warm-up. That is 5 requests per tick on Binance, or about 50 with OKX (100-bar pages) as the fallback provider. The live-window invariance check above was repeated with 4945-bar windows: 0 mismatches.

## TRAIN (2018-01-01 .. 2023-12-31), default risk overlay

| | regime_volswitch | regime_confirm | buy_hold |
|---|---|---|---|
| **score** | **0.0440** | **0.0430** | 0.0168 |
| pct_positive | 45.8% | 44.8% | 47.2% |
| pct exactly flat | 8.0% | 8.3% | 0.0% |
| mean | 5.52% | 5.38% | 3.36% |
| median | 0.00% | 0.00% | -1.17% |
| p05 | -10.5% | -9.7% | -16.4% |
| P(loss > 10%) | 6.1% | 4.7% | 32.7% |
| worst campaign | -16.9% | -16.9% | -29.8% |
| worst campaign max DD | 25.4% | 25.4% | 35.7% |
| mean trades / campaign | 5.2 | 5.7 | 4.2 |
| continuous CAGR | 76.7% | 74.6% | 22.9% |
| continuous Sharpe | 1.61 | 1.59 | 0.64 |
| continuous max DD | 40.5% | 36.6% | 76.9% |
| round trips (continuous, 6y) | 140 | 159 | 131* |
| trades (continuous, 6y) | 317 | 355 | 263 |
| time in market | 45% | 44% | 88% |
| avg exposure | 45% | 43% | 88% |
| avg hold (h) | 170 | 145 | 333 |

\* Buy-and-hold round trips come from the circuit breaker flattening and re-entering.

Per-year score (campaigns grouped by start year), with pct_positive in brackets:

| year | regime_volswitch | regime_confirm | buy_hold |
|---|---|---|---|
| 2018 | -0.0125 (30%) | -0.0008 (32%) | -0.0683 (27%) |
| 2019 | +0.0919 (64%) | +0.0862 (62%) | +0.0576 (57%) |
| 2020 | +0.1168 (62%) | +0.1049 (58%) | +0.1198 (69%) |
| 2021 | +0.0339 (38%) | +0.0312 (35%) | -0.0108 (38%) |
| 2022 | -0.0101 (30%) | -0.0075 (30%) | -0.0499 (35%) |
| 2023 | +0.0438 (53%) | +0.0443 (53%) | +0.0553 (59%) |

Regime occupancy on TRAIN (150-day SMA): bull 41%, transition 18%, bear 41%. 2022 was 96% bear and 2023 was 77% bull.

**Start-of-data caveat.** The data begins on 2017-08-17. The 150-day SMA plus its 5-day slope is therefore undefined until 2018-01-19, and the strategies are flat for the first 19 days of TRAIN, which were falling. I recomputed with a 40-day minimum-period regime SMA (research only). The TRAIN score becomes 0.0434 / 0.0424 instead of 0.0440 / 0.0430, and 2018 becomes -0.016 / -0.004. The conclusions do not change. The same artefact affects every strategy whose warm-up is longer than about 137 days.

## VALID (2024-01-01 .. 2024-12-31), run once

| | regime_volswitch | regime_confirm | buy_hold |
|---|---|---|---|
| **score** | **0.0395** | **0.0366** | 0.0391 |
| pct_positive | 48.2% | 48.8% | 55.4% |
| pct exactly flat | 0.9% | 0.9% | 0.0% |
| mean | 4.99% | 4.63% | 5.32% |
| median | -0.24% | -0.03% | 2.65% |
| p05 | -10.2% | -9.8% | -15.6% |
| P(loss > 10%) | 5.4% | 4.8% | 20.5% |
| worst campaign | -15.9% | -15.9% | -16.7% |
| worst campaign max DD | 19.9% | 19.9% | 23.5% |
| mean trades / campaign | 6.1 | 6.6 | 3.7 |
| continuous CAGR | 46.8% | 52.2% | 71.3% |
| continuous Sharpe | 1.21 | 1.32 | 1.35 |
| continuous max DD | 34.6% | 29.1% | 33.1% |
| round trips (continuous) | 33 | 35 | 14 |
| time in market | 59% | 56% | 92% |
| per-year score 2024 | +0.0395 (48%) | +0.0366 (49%) | +0.0391 (55%) |

VALID is a single bull year with a long sideways stretch in the middle (5 March to 14 October 2024, BTC between 49.8k and 73.6k). That stretch is the regime this family handles worst.

* Of the 22 round trips opened in that stretch, 17 were bull-regime trades on the fast rule. They lost money in total: a summed log return of -0.09 for `regime_volswitch` and -0.18 for `regime_confirm`.
* `regime_volswitch` lost another -0.13 on 5 counter-trend trades in transition and bear regimes. The confirmation rule cut that to -0.05.
* The circuit breaker fired 7 times in 2024.

No parameter was changed after seeing these numbers.

## Parameter sensitivity (TRAIN, one parameter moved +/-25%, everything else at default)

#### regime_volswitch (default 0.0440)
| change | score | d score | pct_pos | p05 | cont. max DD | 2018 | 2022 |
|---|---|---|---|---|---|---|---|
| regime_days 150 -> 112 | 0.0393 | -0.0047 | 43.5% | -10.7% | 40.5% | -0.0251 | -0.0101 |
| regime_days 150 -> 188 | 0.0390 | -0.0049 | 43.0% | -10.7% | 44.2% | -0.0186 | -0.0101 |
| fast_days 20 -> 15 | 0.0374 | -0.0066 | 44.5% | -10.7% | 45.4% | -0.0164 | -0.0101 |
| fast_days 20 -> 25 | 0.0392 | -0.0048 | 45.3% | -11.4% | 40.6% | -0.0152 | -0.0101 |
| slow_days 32 -> 24 | 0.0428 | -0.0011 | 45.8% | -11.0% | 53.3% | +0.0035 | -0.0265 |
| slow_days 32 -> 40 | 0.0409 | -0.0031 | 42.9% | -10.5% | 48.6% | -0.0125 | -0.0187 |
| slow_band 0.02 -> 0.015 | 0.0424 | -0.0016 | 45.8% | -10.5% | 44.1% | -0.0100 | -0.0177 |
| slow_band 0.02 -> 0.025 | 0.0435 | -0.0005 | 44.9% | -10.3% | 42.2% | -0.0038 | -0.0140 |
| vol_target 0.8 -> 0.6 | 0.0410 | -0.0030 | 45.3% | -10.3% | 42.4% | -0.0133 | -0.0128 |
| vol_target 0.8 -> 1.0 | 0.0428 | -0.0012 | 45.7% | -11.2% | 45.6% | -0.0187 | -0.0086 |
| bear_weight 1.0 -> 0.75 (cap 1) | 0.0417 | -0.0023 | 45.4% | -9.9% | 39.5% | -0.0147 | -0.0094 |
| vol_target -> 0 (no vol management) | 0.0414 | -0.0026 | 45.1% | -11.4% | 50.0% | -0.0218 | -0.0086 |

#### regime_confirm (default 0.0430)
| change | score | d score | pct_pos | p05 | cont. max DD | 2018 | 2022 |
|---|---|---|---|---|---|---|---|
| regime_days 150 -> 112 | 0.0401 | -0.0029 | 43.2% | -10.1% | 36.6% | -0.0137 | -0.0075 |
| regime_days 150 -> 188 | 0.0387 | -0.0043 | 42.4% | -9.9% | 40.5% | -0.0070 | -0.0075 |
| fast_days 20 -> 15 | 0.0366 | -0.0065 | 44.1% | -11.1% | 35.9% | -0.0083 | -0.0006 |
| fast_days 20 -> 25 | 0.0383 | -0.0047 | 44.9% | -11.4% | 40.6% | -0.0100 | -0.0083 |
| slow_days 32 -> 24 | 0.0423 | -0.0008 | 45.4% | -10.7% | 51.6% | +0.0089 | -0.0254 |
| slow_days 32 -> 40 | 0.0411 | -0.0019 | 42.1% | -10.4% | 50.5% | +0.0005 | -0.0125 |
| slow_band 0.02 -> 0.015 | 0.0414 | -0.0017 | 44.7% | -10.2% | 40.6% | +0.0015 | -0.0147 |
| slow_band 0.02 -> 0.025 | 0.0426 | -0.0004 | 44.0% | -9.8% | 37.5% | +0.0079 | -0.0110 |
| vol_target 0.8 -> 0.6 | 0.0405 | -0.0026 | 45.2% | -9.6% | 35.7% | -0.0020 | -0.0068 |
| vol_target 0.8 -> 1.0 | 0.0415 | -0.0015 | 44.7% | -10.6% | 38.8% | -0.0085 | -0.0060 |
| bear_weight 1.0 -> 0.75 (cap 1) | 0.0414 | -0.0016 | 44.7% | -9.4% | 34.8% | -0.0030 | -0.0032 |
| vol_target -> 0 (no vol management) | 0.0399 | -0.0031 | 44.0% | -10.8% | 43.7% | -0.0118 | -0.0060 |

(The continuous max DD in these tables comes from the continuous backtest, which has the circuit breaker on and no campaign stop.)

How to read these tables:
* Every +/-25% neighbour scores between 0.037 and 0.044, which is 2.2-2.6x buy-and-hold.
* The defaults sit on the top of a hill rather than on a flat plateau. The typical neighbour is about 0.004 lower, so a fair forward expectation is closer to 0.040 than 0.044.
* `fast_days` is the most sensitive parameter. The finer grid (score by `fast_days`, other parameters near default, without the vol target) was 14: 0.033, 16: 0.037, 18: 0.039, **20: 0.041**, 22: 0.039, 24: 0.038, 27: 0.035. That is a smooth hill; 20 is its centre, not an isolated spike.
* `slow_days` x `slow_band` is a broad plateau: 0.037-0.042 over 26-38 days x 1.5-3% (grid run with bear weight 0.5 and no vol target). 32 days is the middle of it, not its best point (30 and 36 were slightly higher).
* The vol target is flat over 0.6-1.0 x 3-14-day windows (0.040-0.044, `regime_volswitch`). 0.8 x 7 days is its centre.

## Risk-overlay sensitivity (TRAIN; `dataclasses.replace` on `cfg.risk`)

| overlay | volswitch score | pct_pos | p05 | worst camp. DD | confirm score | pct_pos | p05 | worst camp. DD | buy_hold score |
|---|---|---|---|---|---|---|---|---|---|
| **default** (CB 10%/48h, campaign stop -15%) | **0.0440** | 45.8% | -10.5% | 25.4% | **0.0430** | 44.8% | -9.7% | 25.4% | 0.0168 |
| circuit_breaker_dd = 0.15 | 0.0349 | 44.6% | -11.9% | 31.3% | 0.0345 | 43.8% | -11.1% | 31.3% | 0.0086 |
| circuit_breaker_dd = 0.99 (off) | 0.0344 | 44.1% | -12.5% | 29.9% | 0.0344 | 43.3% | -11.4% | 29.9% | 0.0176 |
| campaign_stop_loss = 0.99 (off) | 0.0442 | 46.0% | -10.4% | 27.7% | 0.0433 | 45.0% | -9.6% | 27.7% | - |
| CB off + campaign stop off | 0.0359 | 44.5% | -11.4% | 30.0% | 0.0361 | 43.7% | -10.8% | 30.0% | - |

* **The 10% circuit breaker helps these strategies a lot:** +0.009 score, plus a better p05 and a better worst-campaign drawdown.
  * In a bull regime the strategy is 100% long, and its fast exit (0.5% under the 20-day SMA) can sit far below price after a parabolic run. The breaker then acts as a profit-protecting trailing stop with a 48h cool-off.
  * A 15% breaker is as bad as none. With the breaker on, a strategy-side price crash filter (flat when the close is X% below its N-day high) added nothing; that test gave 0.018-0.036 against 0.037.
  * The regime/trend part and the breaker are complementary. **Do not loosen `circuit_breaker_dd`.**
* The campaign stop-loss almost never fires: 1.3-1.5% of TRAIN campaigns, against 28% for buy-and-hold. Removing it changes the score by +0.0002/+0.0003 (noise). It is a harmless backstop; keep it.

## What I tried (TRAIN only), in order

Scores are TRAIN scores under the default overlay unless noted. "Fast vote" means the latch on the 20-day SMA with a 0% entry band and a 0.5% exit band. "Slow vote" means the latch on a 30-32-day SMA with a 2% entry band.

1. **Baselines.**
   * Price > SMA(n days): best n=20 at 0.031 (50d: 0.027, 150d: 0.027, 10d: 0.008).
   * Single SMA with hysteresis band: 20d/0% 0.0365, 30d/4% 0.0374.
2. **Conditional forward returns** on TRAIN (exploration only; labels never used in a signal):
   * Above SMA150 *and* above SMA20 had by far the best forward 1-week return (+1.6%).
   * Low-vol periods (7-day vol in the bottom 20% of its trailing year) had *negative* forward returns.
   * Dips (z-score < -1.5) only bounced when the 50-day SMA was rising steeply. In flat or falling markets they kept falling: momentum, not reversion.
   * High efficiency ratio above the SMA20 meant persistent trends.
3. **Long-MA regime scaling (`regime_a`)** (target 1 when bull and above SMA20, `w_dip` when bull and below, `w_bear` when bear and above, 0 otherwise): best 0.0367 at L=150 / M=20 / w_dip=0 / w_bear=0.5.
   * *Holding through pullbacks in a bull regime hurt*: w_dip 0.5 gave 0.034.
   * Scaling the bear regime to 0.5 helped the tail more than the score.
4. **Volatility management everywhere: failed.**
   * A vol-target scaler on all exposure lost score monotonically as the target was lowered (3/7/14-day vol windows): vt 1.0 0.033-0.035, 0.8 0.031-0.032, 0.6 0.028-0.030, 0.4 0.016-0.022, against 0.0367 without it.
   * Cutting exposure when 7-day vol is above its trailing-year 80th/90th/95th percentile: 0.023-0.036.
   * BTC's high-vol bull phases are upside volatility, so cutting them costs more than it saves.
   * Cutting in *low*-vol regimes (bottom 10-30%): 0.025-0.037, no gain.
5. **Efficiency-ratio sizing (failed)** (full size only when ER(10/20d) is high, otherwise 0.5-0.75): 0.021-0.033.
6. **Dip-buying in the bull regime (no gain).**
   * Buying when close < SMA(3-10d) x (1-k) while above SMA150: best 0.0375 (k=10%), within noise of 0.0367.
   * Shallower dips: 0.028-0.035.
   * "Hold through dips while the 50-day SMA slope is steep": no gain (0.029-0.035).
7. **Standalone mean reversion (failed).**
   * Z-score dip entries (n = 2/5/10 days, z = 1.5-2.5), exit at the mean or at +1 sigma.
   * Gated to the bull regime: -0.018 to +0.005. Ungated: -0.017 to -0.045.
   * Mean reversion on hourly BTC does not survive 0.26% round-trip costs.
8. **Blend of slow trend and mean reversion into one exposure (failed).** `w * trend(SMA30/50 band) + (1-w) * MR`: w=1 (pure trend) 0.036, w=0.67 0.020-0.024, w=0.5 0.010-0.019.
9. **Price-drawdown crash filters (failed).** Flat while (or for 48h after) close is 6-13% below its 3/7/14-day high: 0.018-0.036.
10. **Two-speed trend (the breakthrough).** Fast vote in the bull regime, slower confirmed vote (SMA30 +2%) in the bear regime: **0.0410**, against 0.036-0.037 for either speed alone. In an established uptrend a quick exit around a short SMA pays. Outside it, an entry needs a bigger move above a longer SMA.
11. **Regime classifier choice** for the two-speed rule:

    | classifier | score |
    |---|---|
    | SMA150 rising (5-20 day slope) | 0.040-0.041 |
    | close > SMA150 | 0.041 |
    | both (3-state) | **0.043** |
    | 150-day momentum | 0.043 (same as slope) |
    | drawdown from 90-365-day high as second condition | 0.038-0.040 |
    | efficiency ratio (10-30 days) | 0.029-0.036 |
    | ADX (rolling, 24h candles) | 0.028-0.031 |
    | vol percentile | 0.035-0.036 |
    | an ER filter on bear-regime entries | 0.029-0.036 |

12. **Bear/transition sizing.** In the 3-state rule, bear weight 1 gave 0.043, 0.5 gave 0.040 and 0 gave 0.035. Bear weight 0 means flat in bear, and then about 1/3 of campaigns end exactly flat, so pct_positive falls to 34-35%.
    * Variant with a 200-day regime and bear weight 0: 0.038, with every TRAIN year at or above zero. Part of its positive 2018 was the start-of-data artefact; after correcting for it, 2018 was -0.001. Its pct_positive of 34% made it unattractive, so it was dropped.
13. **Vol targeting outside the bull regime only (kept).**
    * Scaling only transition/bear exposure by min(1, vt/vol7d): 0.0440 against 0.0414 without, with lower continuous max DD (40.5% vs 50.0%) and a better p05.
    * The vt x window plateau was 0.041-0.044.
    * Also vol-capping the bull regime hurt: vt_bull 1.0 gave 0.041, 1.5 gave 0.042.
14. **Vote combinations by regime:**

    | bull / non-bull rule | score |
    |---|---|
    | fast / slow | **0.0440** (`regime_volswitch`) |
    | fast / (fast AND slow) | 0.0430 (`regime_confirm`; better tail and bear years) |
    | (fast+slow)/2 / slow | 0.042 |
    | fast / fast, i.e. no regime switch but vol-targeted outside bull | 0.041 |
    | max(fast, slow) / slow | 0.039 |
    | slow / slow | 0.038 |

15. **Multi-lookback ensembles (failed).** Averaging three SMA lengths inside the two-speed rule lowered the score at every window pair: 0.029-0.037 in total. For the 20/30-day pair the score was 0.041 with single SMAs, 0.037 at spread 1.25 and 0.033 at spread 1.5. The fractional 1/3 steps diluted the bull-regime exposure.
16. **Volatility trailing stops** via `stop_dist` (k x daily sigma x price), tested on a bear-weight-0.5 base scoring 0.0391:
    * k=4 was neutral: 0.0398 outside the bull regime, 0.0395 in every regime.
    * Tighter stops (k = 1.5-3) cost 0.002-0.007 outside the bull regime and 0.008-0.027 in every regime.

## Engine and harness notes

No engine or harness file was modified.

1. **Forward slack for long warm-ups (resolved upstream).** Earlier versions of `forward.tick` fetched `max(lookback_bars, warmup_bars + 2)` bars, which left a strategy with a long warm-up only 2 bars of catch-up slack. The current version fetches `warmup_bars + CATCHUP_BARS` (720), which fixes this. No action is needed beyond keeping that behaviour.
2. **Start-of-TRAIN warm-up artefact.** Data starts 137 days before TRAIN. Strategies with longer warm-ups are forced flat at the start of the January 2018 crash, which flatters their 2018 and TRAIN scores. Here the effect is about +0.0006 score and +0.004 on 2018.
3. **Start-of-window dependence (resolved upstream).** The earlier `check_causal` only compared prefixes that share the same first bar, so it could not see indicators that depend on where the history starts. The current version adds a window check with `L = warmup_bars + 1`. Both candidates pass it: `check_causal(..., n_checks=30)` returns `[]` for seeds 7, 1 and 2.
4. **Harness revision.** The final tables (TRAIN, VALID, sensitivity, overlay, costs) were computed on the current harness (HEAD `60ff231`). A few early exploration grids ran on the previous revision. The changes in between affect campaign ends only on irregular data, CAGR (calendar time) and trip-return denominators, not the score on this data. Configurations re-run on both revisions gave identical scores, for example 0.0430 for the 3-state rule.
