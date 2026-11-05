# Short-sleeve long/short research (`ls_sleeve`)

Module: `src/tradebot/strategies/ls_sleeve.py`. It registers `lss_breakdown`
and `lss_breakdown_vol`.

## Answer first

**Is the deployed bot better than buying and holding?** On 2018-2024 (DEV), yes.

| 2018-2024 (DEV) | buy-and-hold | `breakout_dual` (spot, deployed) |
|---|---:|---:|
| 30-day campaign score (mean log return) | 0.0199 | 0.0401 |
| 30-day 5th percentile (p05) | -16.3% | -8.4% |
| continuous CAGR | 29% (31.8% with no risk overlay) | 66% |
| continuous max drawdown | 77% (81% with no overlay) | 32.5% |

Buy-and-hold here runs under the same risk overlay, except where the table
says "no risk overlay". Most of the edge comes from sitting out the 2018 and
2022 bear markets. In strong bull years `breakout_dual` keeps pace or lags:
2020 is one example, and in 2024 the scores were 0.0400 against 0.0391.

**Can a short side add to it?** Yes, if the short is small, used only in a
bear regime, and covered quickly. `lss_breakdown` keeps the deployed long
side exactly as it is. It adds a half-size short that is only on during the
early part of a decline (see below). Results on DEV, from
`scripts/ls_evaluate.py`:

| | DEV score | robust | paired t vs deployed | p05 | years > 0 |
|---|---:|---:|---:|---:|---:|
| deployed `breakout_dual`, spot | 0.0401 | 0.03915 | | -8.36% | 7/7 |
| `lss_breakdown`, perp mode | 0.0491 | 0.0473 | 2.16 | -8.98% | 7/7 |
| `lss_breakdown`, hybrid mode | 0.0523 | 0.0506 | 3.07 | -9.29% | 7/7 |

In perp mode everything trades on perpetuals. In hybrid mode longs are held
on spot and shorts on perpetuals.

**Is the adoption rule met?** At the default parameters, rules 1-4 pass
for `lss_breakdown` in both modes. They also pass for `lss_breakdown_vol` in
hybrid mode; in perp mode it misses rule 2 with t = 1.95. Rule 5 (the
independent audit) is not mine to judge. The evidence behind these passes is
thinner than the pass/fail flags suggest:

* **No out-of-sample confirmation.** 2024 (VALID) had no real bear phase, and
  the sleeve added +0.0008 there. The DEV pass therefore rests on TRAIN, where
  about 800 configurations were searched.
* **A handful of trades.** Five crash trades (Feb 2018, Nov 2018, Mar 2020,
  May 2021, Jan 2022) make up 84% of the short side's summed per-trip
  return (continuous backtest, hybrid mode).
* **Rule 4 is on the edge in hybrid mode.** p05 is -9.29% against a limit of
  -9.36%, and 9 of the 22 robust-sweep variants breach it.
* **The perp-mode pass is fragile.** Only 9 of 22 variants keep t >= 2.
  Without 2018 (no funding data, so funding is zero by protocol) the perp t
  falls to 1.17.

Is the gain driven by a single bear market? No. The short side's
contribution is positive in every DEV year (about zero in 2023 and 2024).
With 2018 and 2022 both removed, its paired t is still 2.1-2.2. But it comes
from a few crash episodes, not from steady month-to-month gains.

## Protocol followed

* **Data.** Only `data/research/BTCUSDT-1h-research.csv` and
  `data/research/BTCUSDT-funding-research.csv` (both end 2024-12-31). No
  network data, and no memory of later prices.
* **Tuning on TRAIN (2018-2023) only.** I used scratch harnesses on
  `tradebot.backtest.run_campaigns(..., signals=..., funding=...)`, with the
  same cost model and overlay as the evaluator. That came to about 800
  distinct configurations and about 1,100 TRAIN campaign-set evaluations
  (grids E1-E6, robust sweeps E7, ablations). Most tuning used perp mode
  (the mode named in my brief); hybrid was checked alongside.
* **VALID (2024) and DEV.** The two candidates were frozen on TRAIN. Only
  then was `scripts/ls_evaluate.py` run on DEV (which includes VALID), once
  per candidate and mode. No parameter was changed afterwards; only docstring
  wording was corrected.
* **Final checks.**
  * `research.check_causal(strategy, df, n_checks=30) == []` (prefix and
    warmup+1 window checks) on the full research file, for both defaults and
    every robust-sweep variant (21 each, 8 checks each).
  * `signals()` on the full 64.5k-bar file: 0.035 s and 0.05-0.08 s.
  * The module's targets equal the TRAIN prototypes exactly (max difference 0).
  * Forward-run parity: `simulate.replay` (the live tick path, state
    re-read every tick) on three 30-day starts per strategy and mode
    (2018-11-10, 2021-05-05, 2022-05-01). Final equity matched
    `run_campaigns` to better than 1e-9, with identical trade counts.
  * `pytest` passes, including the contract and causality tests that run
    over every registered strategy.
* **Parameters.** 5 tunable parameters per strategy. The long side is the
  deployed `breakout_dual`, frozen in `LONG_LEG_PARAMS` and not re-tuned.
  Because the evaluator's robust sweep only moves `default_params`, I also
  ran an extended sweep that moves the six long-leg parameters
  (see "Robustness").
* **Disclosure: sibling research.** Before my grid E2, I read the sibling
  notes `research/ls_breakout.md` and `research/ls_trend.md` (committed in
  the repo). The oversold floor came from `ls_trend.md`, so it is not an
  independent discovery. Those notes also include 2024 results for the
  sibling designs. My final design ended up close to `lst_dual_overlay`. It
  differs in three ways: a 125-day rather than 150-day regime average, a 35%
  rather than 30% floor, and no exit on a regime cross. It also uses strict
  long priority, as `lst_dual_overlay` does. The two should not be counted
  as independent confirmations of each other.
* **Process slip.** Early on, one scratch JSON (`e1.json`) was written into
  the repo root by mistake (my harness `chdir`s to the repo). I moved it to
  the scratch directory within seconds. No repo file other than the two
  deliverables was changed.

## The strategies

`L` is the `breakout_dual` target (0 / 0.5 / 1). `S` is the 0/1 sleeve latch:

* **On:** close < lowest low of the previous `entry_n` bars, **and**
  `-floor < close / SMA(regime_n) - 1 < 0`.
* **Off:** close > highest high of the previous `exit_n` bars, **or**
  `close / SMA(regime_n) - 1 <= -floor`. If both fire on the same bar, off wins.
* **Target:** `L` when `L > 0`, else `-size * S`. Longs always have priority,
  and the strategy is never long and short at once.

| strategy | size | params (5) |
|---|---|---|
| `lss_breakdown` | fixed `short_size` | entry_n=240 (10 d), exit_n=48 (2 d), regime_n=3000 (125 d), floor=0.35, short_size=0.5 |
| `lss_breakdown_vol` | `clip(short_risk / (ATR168/close), 0.25, 1.0)`, sampled when the sleeve switches on and held until it switches off (median about 0.48) | entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004 |

To run one, set `run.instrument = "hybrid"` (recommended) or `"perp"` with
`risk.max_short = 1.0` (0.5 is enough for `lss_breakdown`). On a spot config
the engine clips targets at 0, and both strategies are exactly
`breakout_dual`. `warmup_bars` = 6000, the same as `breakout_dual`.

Why shorts are taken only then, from the TRAIN ablations (perp mode,
size 0.5, around the frozen design):

| variant | combined TRAIN score | p05 | short leg alone (TRAIN score) | short leg alone by year 2018..2023 |
|---|---:|---:|---:|---|
| **frozen `lss_breakdown`** | **0.0521** | **-8.86%** | **0.0147** | +.034 +.005 +.015 +.022 +.011 +.000 |
| no oversold floor | 0.0441 | -9.99% | 0.0067 | +.018 +.005 +.006 +.001 +.009 +.000 |
| no regime gate and no floor | 0.0383 | -11.58% | 0.0009 | +.013 -.006 +.000 -.011 +.009 -.001 |
| + exit when the close crosses back above the SMA | 0.0498 | -8.97% | 0.0123 | |
| exit_n 36 / 72 | 0.0476 / 0.0518 | -8.88% / -9.24% | | |
| entry_n 120 / 480 | 0.0521 / 0.0477 | -9.80% / -9.36% | | |
| short_size 0.25 / 0.35 / 0.65 / 0.75 / 1.0 | .0440 / .0468 / .0542 / .0568 / .0611 | -8.42 / -8.57 / -9.69 / -9.83 / -11.24% | | |
| (long only, perp) | 0.0375 | -8.58% | | |
| (deployed, spot) | 0.0410 | -8.21% | | |

* **Regime gate.** Without it, breakdowns in bull markets are bear traps,
  and the short leg is about zero (negative in 3 of 6 years).
* **Oversold floor.** This was the largest single gain. Capitulation lows,
  where BTC's squeezes start, are excluded. With the 125-day average and the
  35% floor, it binds in 11 spans on TRAIN, which group into seven episodes:
  Feb 2018, Mar-Apr 2018, Nov-Dec 2018, Mar 2020, May-Jul 2021, Jan 2022
  and Jun-Jul 2022. That is few enough that the floor's level is a real
  overfitting risk. The mechanism, short squeezes after capitulation, is
  well known.
* **Fast 2-day-high exit.** Bear-market rallies are fast. A 36-bar exit
  churned. 48-60 bars was a plateau (E6 averages: 36 → 0.0441,
  48 → 0.0466, 60 → 0.0463).
* **Size.** The score keeps rising with size up to 1.0, but the loss tail
  grows faster. 0.5 is the largest fixed size whose TRAIN p05 stayed clearly
  inside the rule-4 limit.

## `scripts/ls_evaluate.py` output (verbatim, robust sweep included)

```
lss_breakdown(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_size=0.5)  (max_short=1.0, mode=perp)
  DEV score +0.0491 (baseline spot +0.0401); robust +0.0473
  30d: win 56.4% lose 43.6% mean +5.83% median +1.83% p05 -8.98% worst -15.99%
  paired vs baseline: mean diff +0.0090 se 0.0042 t +2.16
  years: 2018:+0.034 2019:+0.076 2020:+0.100 2021:+0.043 2022:+0.011 2023:+0.042 2024:+0.038
  continuous: CAGR +81.0% Sharpe 1.70 maxDD 31.8% trades 582 short-time 11% funding paid +105,386
  rule: {'1_robust_plus_0.005': True, '2_paired_t_ge_2': True, '3_years_positive_ge_5': True, '4_p05_within_1pp': True}
lss_breakdown(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_size=0.5)  (max_short=1.0, mode=hybrid)
  DEV score +0.0523 (baseline spot +0.0401); robust +0.0506
  30d: win 57.3% lose 42.7% mean +6.21% median +1.88% p05 -9.29% worst -15.93%
  paired vs baseline: mean diff +0.0122 se 0.0040 t +3.07
  years: 2018:+0.032 2019:+0.075 2020:+0.106 2021:+0.055 2022:+0.012 2023:+0.044 2024:+0.041
  continuous: CAGR +90.1% Sharpe 1.83 maxDD 29.5% trades 574 short-time 11% funding paid -2,708
  rule: {'1_robust_plus_0.005': True, '2_paired_t_ge_2': True, '3_years_positive_ge_5': True, '4_p05_within_1pp': True}
lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004)  (max_short=1.0, mode=perp)
  DEV score +0.0491 (baseline spot +0.0401); robust +0.0477
  30d: win 56.5% lose 43.5% mean +5.85% median +1.85% p05 -9.14% worst -15.99%
  paired vs baseline: mean diff +0.0090 se 0.0046 t +1.95
  years: 2018:+0.041 2019:+0.077 2020:+0.099 2021:+0.034 2022:+0.011 2023:+0.043 2024:+0.037
  continuous: CAGR +81.7% Sharpe 1.72 maxDD 31.2% trades 578 short-time 11% funding paid +110,029
  rule: {'1_robust_plus_0.005': True, '2_paired_t_ge_2': False, '3_years_positive_ge_5': True, '4_p05_within_1pp': True}
lss_breakdown_vol(entry_n=240, exit_n=48, regime_n=3000, floor=0.35, short_risk=0.004)  (max_short=1.0, mode=hybrid)
  DEV score +0.0525 (baseline spot +0.0401); robust +0.0511
  30d: win 57.0% lose 43.0% mean +6.24% median +1.83% p05 -9.13% worst -15.93%
  paired vs baseline: mean diff +0.0124 se 0.0044 t +2.80
  years: 2018:+0.039 2019:+0.076 2020:+0.106 2021:+0.046 2022:+0.012 2023:+0.045 2024:+0.041
  continuous: CAGR +88.1% Sharpe 1.81 maxDD 28.8% trades 573 short-time 11% funding paid -2,950
  rule: {'1_robust_plus_0.005': True, '2_paired_t_ge_2': True, '3_years_positive_ge_5': True, '4_p05_within_1pp': True}
```

### Adoption rule

Thresholds: rule 1 needs robust >= 0.04415, rule 4 needs p05 >= -9.36%.

| candidate | mode | 1 robust | 2 paired t | 3 years > 0 | 4 p05 | rules 1-4 |
|---|---|---|---|---|---|---|
| `lss_breakdown` | perp | 0.0473 yes | 2.16 yes | 7/7 yes | -8.98% yes | **pass** |
| `lss_breakdown` | hybrid | 0.0506 yes | 3.07 yes | 7/7 yes | -9.29% yes | **pass** |
| `lss_breakdown_vol` | perp | 0.0477 yes | 1.95 **no** | 7/7 yes | -9.14% yes | fail |
| `lss_breakdown_vol` | hybrid | 0.0511 yes | 2.80 yes | 7/7 yes | -9.13% yes | **pass** |

Rule 5 is the independent audit. My own estimate of overfit risk is
medium-high (see the conclusion).

Taken literally, the tie-break ("highest robust score wins") picks
`lss_breakdown_vol` in hybrid mode over `lss_breakdown` in hybrid mode,
0.0511 against 0.0506. That gap is about 1/8 of a paired standard error.

## Robustness

### Evaluator robust sweep (DEV scores; each parameter one at a time)

| variant | `lss_breakdown` perp | `lss_breakdown` hybrid | `lss_breakdown_vol` perp | `lss_breakdown_vol` hybrid |
|---|---:|---:|---:|---:|
| default | .0491 | .0523 | .0491 | .0525 |
| entry_n ×0.7 / 0.85 / 1.15 / 1.3 | .0498 / .0497 / .0493 / .0486 | .0534 / .0528 / .0526 / .0521 | .0495 / .0496 / .0496 / .0491 | .0528 / .0531 / .0530 / .0524 |
| exit_n | .0436 / .0470 / .0484 / .0489 | .0468 / .0502 / .0516 / .0520 | .0448 / .0472 / .0481 / .0484 | .0480 / .0505 / .0515 / .0517 |
| regime_n | .0448 / .0446 / .0455 / .0436 | .0480 / .0478 / .0486 / .0467 | .0464 / .0461 / .0470 / .0456 | .0500 / .0497 / .0504 / .0490 |
| floor | .0461 / .0486 / .0475 / .0461 | .0496 / .0518 / .0507 / .0493 | .0466 / .0488 / .0489 / .0468 | .0499 / .0521 / .0522 / .0501 |
| short_size / short_risk | .0447 / .0470 / .0504 / .0510 | .0481 / .0502 / .0533 / .0542 | .0459 / .0474 / .0491 / .0502 | .0492 / .0506 / .0524 / .0533 |
| no circuit breaker | .0458 | .0501 | .0458 | .0500 |
| **mean (n = 22)** | **.0473** | **.0506** | **.0477** | **.0511** |
| min | .0436 | .0467 | .0448 | .0480 |

* Every variant beats the spot baseline's robust score (0.03915).
* The weak spots:
  * `exit_n ×0.7` (34 bars; shorter exits churn).
  * `regime_n` in both directions. The default (125 days) sits on a hump:
    the TRAIN grid averaged 0.0479 at 3000 against 0.045 at 2400 or 3600.
    That is a mild sign of tuning to the sample.
  * Smaller sizes.

### Extended sweep: the frozen long leg moved too

The baseline's robust score moves its own six parameters. To compare like
with like, I moved each `breakout_dual` parameter (entry_n, exit_n, trend_n,
band_k, atr_n, keltner_weight) by ×0.7/0.85/1.15/1.3 with the sleeve on. That
adds 24 DEV runs.

| | long-leg variants mean (min) | evaluator robust | combined mean (n = 46) | combined min |
|---|---:|---:|---:|---:|
| `lss_breakdown` perp | 0.0481 (0.0442) | 0.0473 | **0.0477** | 0.0436 |
| `lss_breakdown` hybrid | 0.0513 (0.0475) | 0.0506 | **0.0509** | 0.0467 |
| `lss_breakdown_vol` perp | 0.0479 (0.0438) | 0.0477 | **0.0478** | 0.0438 |
| `lss_breakdown_vol` hybrid | 0.0513 (0.0474) | 0.0511 | **0.0512** | 0.0474 |

Freezing the long leg does not inflate rule 1. The weakest long-leg
variants are trend_n ×0.7 and exit_n ×0.7, the same weak spots as
`breakout_dual` itself.

### How fragile are rules 2 and 4? (the same 22 variants, DEV)

| | t >= 2 | p05 >= -9.36% | all of rules 1-4 | median t | median p05 |
|---|---:|---:|---:|---:|---:|
| `lss_breakdown` perp | 9/22 | 16/22 | 7/22 | 1.93 | -9.19% |
| `lss_breakdown` hybrid | 21/22 | 13/22 | 13/22 | 2.83 | -9.34% |
| `lss_breakdown_vol` perp | 3/22 | 14/22 | 3/22 | 1.74 | -9.24% |
| `lss_breakdown_vol` hybrid | 20/22 | 14/22 | 13/22 | 2.60 | -9.28% |

* **Hybrid mode.** The paired-t result is robust, but the tail result is a
  coin flip. Larger entries, longer exits, a larger floor, a larger size,
  or a different regime length each push p05 past -9.36%.
* **Perp mode.** The t >= 2 result holds only near the defaults.

## Per-year scores and TRAIN / VALID split

DEV campaign score by start year:

| strategy | mode | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | DEV |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `breakout_dual` (baseline) | spot | +.0016 | +.0698 | +.0914 | +.0332 | +.0003 | +.0442 | +.0401 | +.0401 |
| `breakout_dual` | perp | +.0030 | +.0712 | +.0816 | +.0213 | +.0001 | +.0416 | +.0368 | +.0365 |
| buy_hold | spot | -.0683 | +.0576 | +.1198 | -.0108 | -.0499 | +.0512 | +.0410 | +.0199 |
| `lss_breakdown` | perp | +.0337 | +.0757 | +.0995 | +.0433 | +.0112 | +.0420 | +.0376 | +.0491 |
| `lss_breakdown` | hybrid | +.0322 | +.0750 | +.1057 | +.0553 | +.0116 | +.0445 | +.0408 | +.0523 |
| `lss_breakdown_vol` | perp | +.0413 | +.0774 | +.0987 | +.0343 | +.0110 | +.0427 | +.0373 | +.0491 |
| `lss_breakdown_vol` | hybrid | +.0393 | +.0760 | +.1063 | +.0462 | +.0119 | +.0453 | +.0412 | +.0525 |

| strategy | mode | TRAIN score | TRAIN p05 | TRAIN t | VALID score | VALID p05 | VALID t |
|---|---|---:|---:|---:|---:|---:|---:|
| `breakout_dual` (baseline) | spot | 0.0410 | -8.21% | | 0.0400 | -9.74% | |
| `breakout_dual` | perp | 0.0375 | -8.58% | -3.39 | 0.0367 | -10.04% | -2.58 |
| `lss_breakdown` | perp | 0.0521 | -8.86% | 2.32 | 0.0375 | -9.61% | -0.67 |
| `lss_breakdown` | hybrid | 0.0551 | -9.06% | 3.08 | 0.0407 | -9.19% | +0.24 |
| `lss_breakdown_vol` | perp | 0.0521 | -8.98% | 2.06 | 0.0374 | -9.61% | -0.62 |
| `lss_breakdown_vol` | hybrid | 0.0553 | -9.06% | 2.75 | 0.0411 | -9.19% | +0.26 |

VALID is uninformative about the short side:

* In 2024 the sleeve was short on 8% of bars, but the moves were small, and
  its contribution was +0.0008.
* In perp mode, 2024 trails the spot baseline because of the long leg's
  funding (7.7% of equity on long exposure).

## Short-side vs long-side attribution

### Campaign level (DEV)

The short contribution is the candidate's score minus `breakout_dual` in
the **same** mode. That is the identical long side with no shorts.

| `lss_breakdown` | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | DEV | t (block bootstrap) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| short contribution, perp | +.0307 | +.0045 | +.0179 | +.0220 | +.0111 | +.0003 | +.0008 | **+.0126** | 3.17 |
| short contribution, hybrid | +.0305 | +.0052 | +.0143 | +.0221 | +.0112 | +.0003 | +.0008 | **+.0122** | 3.07 |
| perp long leg vs spot (funding drag net of lower fees) | +.0014 | +.0014 | -.0098 | -.0119 | -.0002 | -.0026 | -.0033 | -.0036 | -3.73 |

* **Share of the score.** The short side supplies about a quarter of the DEV
  score: 0.0126 of 0.0491 in perp mode, 0.0122 of 0.0523 in hybrid. It is
  short about 11% of the time.
* **Share by year** (perp): 2018 35%, 2021 25%, 2020 21%, 2022 13%, 2019 5%,
  2023-24 1%.
* **The short leg on its own** (the same target with the long part zeroed):
  * mean 30-day return +1.4%, p05 -3.7%, DEV score 0.0126;
  * positive in 29% of campaigns, negative in 24%, never short in 47%.

### Is it one bear market? (paired block-bootstrap t, DEV)

| years left out | `lss_breakdown` perp vs spot baseline | perp: short part only | `lss_breakdown` hybrid vs spot baseline (= short part) | `lss_breakdown_vol` hybrid |
|---|---:|---:|---:|---:|
| none | +.0090, t 2.16 | +.0126, t 3.17 | +.0122, t 3.07 | +.0124, t 2.80 |
| 2018 | +.0052, t 1.17 | +.0096, t 2.27 | +.0091, t 2.16 | +.0081, t 2.19 |
| 2022 | +.0087, t 2.00 | +.0129, t 3.10 | +.0124, t 2.97 | +.0125, t 2.58 |
| 2018 and 2022 | +.0040, t 0.90 | +.0092, t 2.22 | +.0087, t 2.08 | +.0074, t 2.06 |
| 2020 | +.0092, t 2.21 | +.0117, t 2.95 | +.0118, t 2.99 | +.0119, t 2.58 |
| 2024 | +.0108, t 2.26 | +.0144, t 3.15 | +.0140, t 3.03 | +.0141, t 2.73 |

* **The short sleeve itself.** Its gain does not depend on 2018 or 2022
  alone: t stays above 2 with both removed, carried by the March 2020 and
  May 2021 crashes.
* **Perp mode versus the deployed spot strategy.** The improvement needs
  2018. Without it, the long leg's funding drag cancels about half the
  short gain, and t falls to 1.17. Funding is zero before 2020 by protocol,
  so 2018 carries no funding cost on either side.

### By episode (continuous DEV backtest, hybrid)

63 short trips, 44% winners, median hold 83 h. Contribution of each trip =
P&L / equity at entry.

| top trips | entry → exit | price | contribution |
|---|---|---|---:|
| Mar 2020 crash | 2020-03-08 → 03-13 | 8,278 → 4,801 | +25.1% |
| May 2021 crash | 2021-05-13 → 05-23 | 49,522 → 32,247 | +21.0% |
| Feb 2018 | 2018-01-31 → 02-06 | 9,827 → 6,448 | +19.1% |
| Nov 2018 | 2018-11-14 → 11-23 | 6,307 → 4,295 | +18.7% |
| Jan 2022 | 2022-01-21 → 01-24 | 40,669 → 33,287 | +9.5% |

The top 5 trips are **84%** of the summed short contribution. The worst
trips are small, -2% to -4% each: Apr 2020, Apr 2022, Jul 2021, Jul 2018 and
Sep 2022. They are bear-market rallies covered at the 2-day high. By
quarter, the sleeve lost in 2018Q3, 2019Q1, 2020Q2, 2022Q3 and 2024Q4.

### P&L and funding by side (continuous DEV backtest from 10,000 USDT)

| strategy | mode | long trips: n, P&L, win | short trips: n, P&L, win | funding: longs paid / shorts received | fees long / short | time long / short |
|---|---|---|---|---|---|---|
| `lss_breakdown` | perp | 132, +627,141, 29% | 64, +105,927, 44% | **107,404** / 2,019 | 25,171 / 5,306 | 49% / 11% |
| `lss_breakdown` | hybrid | 127, +765,537, 31% | 63, +123,709, 44% | 0 / 2,708 | 62,998 / 6,803 | 49% / 11% |
| `lss_breakdown_vol` | perp | 133, +645,042, 29% | 62, +110,146, 48% | 112,545 / 2,515 | 26,070 / 6,046 | 49% / 11% |
| `lss_breakdown_vol` | hybrid | 127, +707,574, 31% | 61, +116,576, 49% | 0 / 2,950 | 56,658 / 6,842 | 49% / 11% |

Trip P&L is realised and net of fees, excluding funding. USDT figures
compound, so later years dominate them. Final equity: 637,683 (perp) and
901,954 (hybrid), against 349,800 for the spot baseline.

**Funding is not where shorts earn.** Shorts receive almost nothing,
because bear-regime funding is low:

| funding as % of equity (exposure × rate) | 2020 | 2021 | 2022 | 2023 | 2024 |
|---|---:|---:|---:|---:|---:|
| shorts received (`lss_breakdown`) | 0.02% | 0.22% | 0.06% | 0.11% | 0.25% |
| longs paid in perp mode | 14.1% | 20.4% | 1.8% | 5.0% | 7.7% |

All-perp longs give back about 0.0036 per campaign to funding. That is why
hybrid mode (longs on spot) is the right way to run any long/short version.
It is also why the perp-mode pass is much weaker than the hybrid one.

## What was tried (TRAIN 2018-2023, perp cost model unless stated)

1. **E1: breakdown sleeve at full size (144 configs).**
   * Grid: entry 72-480 × exit 24-168 × regime SMA none/1200-4800 × optional
     regime exit.
   * Best TRAIN score: 0.0448 (144/48/2400), with **p05 about -15%**
     everywhere. That is the -15% campaign stop binding: full-size shorts get
     squeezed in bear-market rallies.
   * Factor averages: exit 48 was best (24 churned, 0.0331); regime SMA
     2400-3600 best (none 0.0336); entry length mattered little.
2. **E2: size and oversold floor (100 configs).**
   * Floors 0.15-0.35 below the regime average, fixed sizes 0.25-0.75, vol
     sizing.
   * Average score by floor: none 0.0416, 0.15 0.0419, 0.25 0.0451,
     0.35 0.0471. The floor also shrank the tail (average p05 -10.6% →
     -9.8%).
   * Size trades score against the tail almost linearly. Vol sizing gave a
     slightly better score per unit of tail than fixed sizing.
3. **E3 and E6: plateau grids at size 0.5 (162 + 288 configs).**
   * Best region: entry 168-240, exit 48-60, regime 3000 (125 d), floor
     0.30-0.35, no regime exit.
   * Neighbourhood-smoothed top: (168/240, 48/60, 3000, 0.35) at
     0.0508-0.0514.
   * Regime 1800 was clearly worse (0.0414 on average).
   * The regime-exit flag made no difference on average (0.0458 vs 0.0455).
4. **E4: exits and "clearly bearish" confirmations (failed).**
   * Chandelier exits (lowest low + k·ATR24): k = 5 cost 0.005, while k = 7-10
     made no difference.
   * A maximum holding time (168-720 h) and an EMA(168/336) exit: mostly
     within ±0.001, at worst -0.003. Noise, not adopted.
   * Requiring EMA600 < SMA(regime): 0.0441 vs 0.0482. Requiring a falling
     regime SMA: 0.0402. Both are for base 240/48/2400/floor 0.35.
   * Both confirmations *hurt*. They delay entry until late in the bear, and
     late-bear breakdowns are the ones that get squeezed. TRAIN trip study
     (63 trips of that base sleeve): shorts entered when the regime SMA was
     already falling fastest averaged -2.1% per trip; entries in the other
     three quartiles averaged +3.1% to +4.9%. This study was used only to
     interpret results. No filter was built from it: a "regime SMA not
     falling fast" filter scored higher on TRAIN (0.0502), but it was mined
     from this same study, so I did not adopt it.
5. **E5: crash-momentum sleeve (failed).**
   * Entry rule: short after a K-bar drop of X% (K 24/72/168, X 5-15%)
     below the regime average.
   * With the floor: 0.0373-0.0481. Without it: 0.0325-0.0406, with p05 of
     -10.8% to -12.8%.
   * It was always worse than the 10-day-low breakdown. Shorting *after* a
     big drop is shorting into the squeeze zone. In the trip study, trips
     entered after a weekly drop of about 10% or more averaged +0.3%, against
     +2.6% overall.
6. **Sleeve armed only from flat (failed to matter).** Here the sleeve can
   only switch on while the long leg is flat, rather than as a background
   latch that takes over when the long exits. The results were identical or
   within 0.0001.
7. **E7: TRAIN robust sweeps (evaluator perturbations, TRAIN only).**

   Seven finalists:

   | finalist | perp robust | hybrid robust |
   |---|---:|---:|
   | P1 = `lss_breakdown` | 0.0499 | 0.0531 |
   | 168-bar entry | 0.0505 | 0.0540 |
   | regime 3600 / floor 0.30 (close to ls_trend's design) | 0.0484 | 0.0515 |
   | regime 3000 / floor 0.30 | 0.0489 | 0.0521 |
   | 60-bar exit | 0.0499 | 0.0531 |
   | size 0.65 | 0.0523 | 0.0553 |
   | vol sizing | 0.0500 | 0.0533 |

   How I chose:
   * The 168-bar entry and size 0.65 versions scored higher but had TRAIN
     p05 of -9.7%, outside the rule-4 limit. Rejected.
   * Among the rest, P1, the 60-bar exit and the vol-sized version tied on
     TRAIN robust score (0.0499-0.0500 perp, 0.0531-0.0533 hybrid).
   * P1 beat the 60-bar exit on TRAIN p05 (-8.86% vs -9.01%) and t (2.32 vs
     2.23).
   * The regime 3000 / floor 0.30 version had a marginally higher t (2.40 /
     3.36) but a lower score and robust score.
   * So P1 became `lss_breakdown`, and the volatility-sized version (clipped
     to 0.25-1.0: TRAIN 0.0521, t 2.06, p05 -8.98%) became the second
     candidate, because it sizes down after volatility spikes. The choice
     among these near-ties is itself a small selection effect.

## Honest conclusion

* **Does the sleeve improve on `breakout_dual`?**
  * In-sample, yes. Adding a half-size, bear-regime, fast-exit short raised
    the DEV 30-day score from 0.0401 to 0.0491 (perp) or 0.0523 (hybrid).
    Max drawdown did not get worse, and every DEV year stayed positive.
  * The short side's own contribution is positive in every year. It is
    statistically clear (t about 3.1) and survives removing both 2018 and
    2022 (t about 2.2).
* **Is it robust enough to adopt?** At the default parameters it formally
  meets rules 1-4. But I would not call the evidence strong:
  1. **No out-of-sample test of the short side.** 2024 had no bear phase:
     its contribution was +0.0008. The 2025-26 holdout is a known decline,
     so it cannot serve as a clean test either (protocol).
  2. **Search breadth.** About 800 TRAIN configurations went into the
     search. The final t values are the best of that search, so the true t
     values are lower. The design also converged with a sibling family
     (`lst_dual_overlay`) whose notes I had read, so the two are not
     independent evidence.
  3. **Concentration.** Five crash trades supply 84% of the short P&L. The
     strategy is a crash catcher, not a steady earner.
  4. **Knife edges in the rules.**
     * Hybrid p05 is -9.29% against a -9.36% limit, and 9 of 22 variants
       breach it.
     * Perp t = 2.16 holds in only 9 of 22 variants. It falls to 1.17
       without 2018, a year with no funding data.
  5. `regime_n` = 3000 sits on a small hump of the TRAIN grid.
* **My overfit-risk estimate is medium-high.** If an auditor accepts the
  formal pass, the right way to run it is `lss_breakdown` in **hybrid**
  mode (`run.instrument = "hybrid"`, `risk.max_short = 0.5` or more).
  * Hybrid is the only mode where both the paired-t result and the absence
    of a funding drag are solid.
  * I prefer `lss_breakdown` to `lss_breakdown_vol`. It passes in both modes
    and has a higher t (3.07 vs 2.80). Its sizing does not depend on ATR.
  * The strict tie-break (highest robust score) would pick
    `lss_breakdown_vol` in hybrid mode, 0.0511 against 0.0506. That gap is
    noise, and the choice belongs to the protocol owner, together with any
    sibling-family candidates.
* **Expected effect.** Treat the gain as an option on crashes. Expect a
  short sleeve that sits idle in bull years, loses a few percent in
  bear-market rallies, and occasionally pays off in a crash. It should not
  be expected to "profit on both sides" every month.

## Engine / harness concerns

1. **The evaluator's robust sweep only moves `default_params`.** A wrapper
   strategy that freezes `breakout_dual`'s parameters escapes the
   long-parameter perturbations that the baseline's 0.03915 includes. I
   measured the effect above (combined 46-variant sweep) and it is small,
   but the evaluator should optionally move frozen sub-strategy parameters.
2. **Residual liquidation in `run_campaigns` charges `params.fee_rate`
   regardless of side.** The line is
   `final = cash + q*px - |q|*px*fee_rate`. In hybrid mode `fee_rate` is
   the spot fee (0.10%), so a residual *short* would pay the spot fee
   rather than `short_fee_rate`. This only matters when a campaign does not
   end flat (irregular data, or a residue below min-notional). I saw no
   effect in these runs.
3. **Funding is zero before 2020 by protocol.** This flatters perp mode in
   2018-2019: longs pay nothing, and shorts neither receive nor pay. 2018
   is the single largest contributor to the perp-mode pass, which is not
   significant without it.
4. **Campaign start times drift after data gaps.** `run_campaigns` schedules
   the next start from the *actual* start bar (`t = start_ms + step_ms`
   after `searchsorted`), so every gap in the candles shifts all later
   starts. By 2024, DEV campaigns start at 18:00 UTC, while VALID campaigns
   start at 00:00. The "2024" per-year DEV score is therefore not the VALID
   score. For buy_hold they are 0.0410 vs 0.0391, and the same 336
   campaigns differ only in start hour. It is not a bug in the engine, but
   per-year DEV numbers and period numbers are not interchangeable, and the
   start-hour sensitivity (about 0.002 here) is a reminder of how noisy
   single-year scores are.
5. **The paired t in perp mode mixes two effects.** It combines the
   instrument effect on the unchanged long leg (-0.0036, t -3.7) with the
   short sleeve's contribution. For short-sleeve designs, a same-mode
   comparison (candidate against `breakout_dual` on the same instrument)
   isolates what the shorts add. I report both.

No look-ahead or accounting errors were found in the engine paths these
strategies use:

* long → short flips close the long and open the short in one rebalance at
  the next open;
* short → long covers, then opens the long;
* funding is settled on the position at the bar close;
* forward-run replay matches `run_campaigns` exactly.

## Scratch scripts

These are not in the repo:
`/tmp/claude-0/-home-user-trading-app/51d8e21b-b4d0-5c80-97c0-ba14790b0d74/scratchpad/ls-research/ls_sleeve/`

| file | purpose |
|---|---|
| `common.py` | harness: real-engine campaigns from target arrays, sleeve builders, robust-sweep helpers |
| `e1.py`-`e6.py` (+ `.log` / `.json`) | TRAIN grids described above |
| `e7.py` | TRAIN robust sweeps of the finalists (perp, hybrid) |
| `tail.py`, `trips.py` | tail decomposition and trip-feature study (TRAIN) |
| `ablate.py` | ablations of the frozen design (TRAIN) |
| `loo.py`, `attrib.py`, `episodes.py` | leave-years-out t, side attribution and funding, per-episode contributions |
| `longsweep.py`, `p05sweep.py` | long-leg sweep; rule 2/4 fragility across variants (DEV) |
| `verify.py`, `parity.py` | module == prototype, causality, timing; forward-run replay parity |
| `eval_*.txt` / `eval_*.json` | verbatim `scripts/ls_evaluate.py` outputs |
