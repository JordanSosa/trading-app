# Long/short breakout family (`ls_breakout`): research notes

Module: `src/tradebot/strategies/ls_breakout.py`. It registers three strategies:
`lsb_mirror`, `lsb_bear_chandelier` and `lsb_bear_chandelier_lite`.

## Answer first

**Is the deployed strategy better than buy-and-hold?** On the pre-declared
score (mean log-return of 30-day campaigns) over 2018-2024 it is, by about 2x.
`breakout_dual` on spot scores 0.0401, against 0.0199 for buy-and-hold under
the same risk overlay. Its 30-day 5th percentile is -8.4%, against -16.3% for
buy-and-hold. In continuous terms it made a 66% CAGR with a 32% max drawdown;
pure buy-and-hold made 31.6% CAGR (6.8x) with an 81% drawdown. The gap comes
from avoiding most of the 2018 and 2022 bear markets. In bull years it only
keeps pace: in 2024 it scored 0.0400 against 0.0391 for buy-and-hold, and in
2020 it lost to buy-and-hold.

**Can shorting add to it?** A little, but only with an asymmetric short side,
and not by enough to pass the pre-declared adoption rule.

* The naive approach, mirroring the long rules for shorts, adds nothing. Its
  shorts lose money in every year except 2018 and 2022, and they widen the
  loss tail.
* `lsb_bear_chandelier` keeps the `breakout_dual` long side unchanged. It adds
  a short that is only entered in a bear regime, is covered by a fast trailing
  (chandelier) exit, and is sized by volatility (median size 0.48).
  * In hybrid mode (longs on spot, shorts on perps) it scores 0.0482 on DEV
    (robust 0.0467) against 0.0401, and is positive in all 7 years.
  * The improvement is not statistically reliable: paired t = 1.73, where the
    rule needs at least 2.
  * Its 30-day loss tail is wider: p05 is -10.2%, where the rule allows -9.36%.
  * About three quarters of the short-side gain comes from a single year (2018).
* **The adoption rule is not met by any candidate in either mode. The
  deployment stays long-only.** The code remains available as an opt-in.

## Protocol followed

* Data: `data/research/BTCUSDT-1h-research.csv` and
  `data/research/BTCUSDT-funding-research.csv`, both ending 2024-12-31. No other
  market data was used, and nothing after 2024 was looked at.
* All design and tuning used TRAIN (2018-2023) campaign scores only. That
  meant about 630 TRAIN configurations plus 104 TRAIN robust-sweep runs, all
  through `tradebot.backtest.run_campaigns`. The perp cost model was used
  throughout: 0.05% per side, 3 bps slippage and funding, with the default
  risk overlay.
* The three candidates were frozen on TRAIN. Only then were they run through
  `scripts/ls_evaluate.py` on DEV, which includes VALID 2024. After that run,
  nothing was changed.
* **Amendment 1 (hybrid mode) arrived in the repo while this work was in
  progress.** It was committed after the TRAIN selection and is the reason
  `--mode` was added to the evaluator. In line with the amendment, the same
  frozen candidates are reported in both modes. Nothing was re-tuned for
  hybrid mode.
* Checks on every final candidate:
  * `check_causal(strategy, df, n_checks=30) == []`. This covers the prefix
    check and the warmup+1 window check, on the full research file.
  * `signals()` on the full 64.5k-bar file takes 0.03-0.06 s.
  * `warmup_bars` = 6000 for all three, the same as `breakout_dual`. The
    regime filter is a *simple* MA because an EMA(3600) would need 36k bars
    of live warm-up.
  * `tests/test_strategies.py` passes with the new strategies registered.

## Final candidates

All three keep the `breakout_dual` long side exactly as it is. That side has a
Keltner leg (on above EMA600 + 3·ATR168, off below EMA600) and a Donchian leg
(on above the previous 144-bar high while above EMA600, off below the previous
72-bar low), weighted 0.5 each. Target = long exposure − short exposure, and
the legs net against each other.

| strategy | short side | tunable params (defaults) |
|---|---|---|
| `lsb_mirror` (reference) | Mirror image of the long side: a Keltner short (close < EMA − band_k·ATR, cover > EMA) and a Donchian short (close < previous entry_n low and < EMA, cover > previous exit_n high), × `short_weight`. | entry_n 144, exit_n 72, trend_n 600, band_k 3.0, atr_n 168, short_weight 0.5 (6) |
| `lsb_bear_chandelier` | **Entry:** close < lowest low of the previous `entry_n` bars **and** close < SMA(`regime_n`). **Exit:** close > lowest low of the previous `exit_n` bars + `chand_k`·ATR(24). **Size**, fixed at entry: min(1, `short_risk` / (ATR168/close)): median 0.48, and 9% of short bars are at the 1.0 cap (calm markets). | entry_n 144, exit_n 72, trend_n 600, regime_n 3600 (150 d), short_risk 0.004, chand_k 8.0 (6) |
| `lsb_bear_chandelier_lite` | The same logic with `short_risk` 0.003, a short about a quarter smaller (median 0.36, never at the cap). | as above, short_risk 0.003 (6) |

The constants are fixed and are not swept by the evaluator. Keltner band 3.0,
ATR 168 and leg weight 0.5 are inherited from `breakout_dual`. The chandelier
ATR length is 24. A separate DEV sweep of these constants (each at
×0.7/0.85/1.15/1.3) found no cliffs:

* perp mode: 0.0439-0.0451, mean 0.0444
* hybrid mode: 0.0473-0.0485, mean 0.0478

Why each asymmetric piece is there (all TRAIN evidence, detailed below):

* **The regime gate (150-day SMA).** Short breakdowns in bull markets are
  mostly bear traps. The gate only applies to entries.
* **The chandelier exit.** After a breakdown, a mirrored "previous N-bar
  high" exit sits back at the pre-crash price, so V-shaped rebounds take back
  the whole gain. The chandelier level follows the new lows instead. ATR(24)
  matters here: it widens at once after a volatility spike, so post-crash noise
  does not close the short.
* **Volatility sizing, fixed at entry.** This gives smaller shorts after
  crashes, which is when squeezes are largest. Fixing the size at entry means
  it does not churn.

## `scripts/ls_evaluate.py` output (verbatim, robust sweep included)

Hybrid mode (`--mode hybrid --max-short 1.0`): longs on spot (0.10% fees, no
funding), shorts on perps (0.05% fees, funding).

```
lsb_bear_chandelier(entry_n=144, exit_n=72, trend_n=600, regime_n=3600, short_risk=0.004, chand_k=8.0)  (max_short=1.0, mode=hybrid)
  DEV score +0.0482 (baseline spot +0.0401); robust +0.0467
  30d: win 55.8% lose 44.2% mean +5.81% median +1.80% p05 -10.21% worst -18.64%
  paired vs baseline: mean diff +0.0081 se 0.0047 t +1.73
  years: 2018:+0.043 2019:+0.065 2020:+0.101 2021:+0.033 2022:+0.014 2023:+0.036 2024:+0.045
  continuous: CAGR +79.5% Sharpe 1.65 maxDD 34.3% trades 623 short-time 18% funding paid -2,248
  rule: {'1_robust_plus_0.005': True, '2_paired_t_ge_2': False, '3_years_positive_ge_5': True, '4_p05_within_1pp': False}
lsb_bear_chandelier_lite(entry_n=144, exit_n=72, trend_n=600, regime_n=3600, short_risk=0.003, chand_k=8.0)  (max_short=1.0, mode=hybrid)
  DEV score +0.0470 (baseline spot +0.0401); robust +0.0454
  30d: win 55.2% lose 44.8% mean +5.68% median +1.49% p05 -9.46% worst -17.96%
  paired vs baseline: mean diff +0.0069 se 0.0042 t +1.65
  years: 2018:+0.044 2019:+0.066 2020:+0.096 2021:+0.033 2022:+0.010 2023:+0.039 2024:+0.042
  continuous: CAGR +78.5% Sharpe 1.66 maxDD 34.3% trades 615 short-time 19% funding paid -1,747
  rule: {'1_robust_plus_0.005': True, '2_paired_t_ge_2': False, '3_years_positive_ge_5': True, '4_p05_within_1pp': False}
lsb_mirror(entry_n=144, exit_n=72, trend_n=600, band_k=3.0, atr_n=168, short_weight=0.5)  (max_short=1.0, mode=hybrid)
  DEV score +0.0408 (baseline spot +0.0401); robust +0.0392
  30d: win 56.4% lose 43.6% mean +5.00% median +1.78% p05 -11.61% worst -18.18%
  paired vs baseline: mean diff +0.0007 se 0.0040 t +0.16
  years: 2018:+0.023 2019:+0.063 2020:+0.086 2021:+0.033 2022:+0.008 2023:+0.041 2024:+0.031
  continuous: CAGR +63.9% Sharpe 1.37 maxDD 34.1% trades 906 short-time 41% funding paid -4,996
  rule: {'1_robust_plus_0.005': False, '2_paired_t_ge_2': False, '3_years_positive_ge_5': True, '4_p05_within_1pp': False}
```

Perp mode (`--max-short 1.0`, the default `--mode perp`): everything on
perps, so longs pay funding.

```
lsb_bear_chandelier(entry_n=144, exit_n=72, trend_n=600, regime_n=3600, short_risk=0.004, chand_k=8.0)  (max_short=1.0, mode=perp)
  DEV score +0.0448 (baseline spot +0.0401); robust +0.0432
  30d: win 55.2% lose 44.8% mean +5.42% median +1.79% p05 -10.16% worst -18.58%
  paired vs baseline: mean diff +0.0047 se 0.0049 t +0.97
  years: 2018:+0.044 2019:+0.066 2020:+0.093 2021:+0.021 2022:+0.014 2023:+0.033 2024:+0.042
  continuous: CAGR +69.9% Sharpe 1.51 maxDD 36.4% trades 628 short-time 18% funding paid +73,120
  rule: {'1_robust_plus_0.005': False, '2_paired_t_ge_2': False, '3_years_positive_ge_5': True, '4_p05_within_1pp': False}
lsb_bear_chandelier_lite(entry_n=144, exit_n=72, trend_n=600, regime_n=3600, short_risk=0.003, chand_k=8.0)  (max_short=1.0, mode=perp)
  DEV score +0.0436 (baseline spot +0.0401); robust +0.0419
  30d: win 54.8% lose 45.2% mean +5.27% median +1.34% p05 -9.57% worst -17.76%
  paired vs baseline: mean diff +0.0035 se 0.0044 t +0.78
  years: 2018:+0.045 2019:+0.066 2020:+0.087 2021:+0.021 2022:+0.010 2023:+0.037 2024:+0.038
  continuous: CAGR +68.8% Sharpe 1.52 maxDD 36.4% trades 620 short-time 19% funding paid +75,179
  rule: {'1_robust_plus_0.005': False, '2_paired_t_ge_2': False, '3_years_positive_ge_5': True, '4_p05_within_1pp': False}
lsb_mirror(entry_n=144, exit_n=72, trend_n=600, band_k=3.0, atr_n=168, short_weight=0.5)  (max_short=1.0, mode=perp)
  DEV score +0.0375 (baseline spot +0.0401); robust +0.0359
  30d: win 55.8% lose 44.2% mean +4.63% median +1.59% p05 -11.75% worst -17.97%
  paired vs baseline: mean diff -0.0026 se 0.0043 t -0.61
  years: 2018:+0.024 2019:+0.064 2020:+0.077 2021:+0.021 2022:+0.008 2023:+0.039 2024:+0.028
  continuous: CAGR +56.4% Sharpe 1.26 maxDD 37.8% trades 911 short-time 41% funding paid +42,773
  rule: {'1_robust_plus_0.005': False, '2_paired_t_ge_2': False, '3_years_positive_ge_5': True, '4_p05_within_1pp': False}
```

Reference: `breakout_dual` long-only on perps (`--max-short 0 --no-robust`).
This run was made before the evaluator printed the mode.

```
breakout_dual(entry_n=144, exit_n=72, trend_n=600, band_k=3.0, atr_n=168, keltner_weight=0.5)  (max_short=0.0)
  DEV score +0.0365 (baseline spot +0.0401); robust +nan
  30d: win 46.1% lose 49.3% mean +4.48% median +0.00% p05 -8.59% worst -15.99%
  paired vs baseline: mean diff -0.0036 se 0.0010 t -3.73
  years: 2018:+0.003 2019:+0.071 2020:+0.082 2021:+0.021 2022:+0.000 2023:+0.042 2024:+0.037
  continuous: CAGR +57.1% Sharpe 1.41 maxDD 34.7% trades 443 short-time 0% funding paid +46,050
```

### Adoption rule check

The rule is in `research/LONG_SHORT_PROTOCOL.md`. Baseline robust score
0.03915: rule 1 needs at least 0.04415, and rule 4 needs a p05 of at least
-9.36%.

| candidate | mode | 1 robust ≥ 0.04415 | 2 paired t ≥ 2 | 3 years > 0 ≥ 5/7 | 4 p05 ≥ -9.36% | passes |
|---|---|---|---|---|---|---|
| lsb_bear_chandelier | hybrid | **0.0467 yes** | 1.73 no | 7/7 yes | -10.21% no | **no** |
| lsb_bear_chandelier_lite | hybrid | **0.0454 yes** | 1.65 no | 7/7 yes | -9.46% no | **no** |
| lsb_mirror | hybrid | 0.0392 no | 0.16 no | 7/7 yes | -11.61% no | no |
| lsb_bear_chandelier | perp | 0.0432 no | 0.97 no | 7/7 yes | -10.16% no | no |
| lsb_bear_chandelier_lite | perp | 0.0419 no | 0.78 no | 7/7 yes | -9.57% no | no |
| lsb_mirror | perp | 0.0359 no | -0.61 no | 7/7 yes | -11.75% no | no |

Rule 5, the independent audit, is not mine to judge. It would not change the
outcome.

TRAIN / VALID split, from the same campaigns. Paired t is against
`breakout_dual` spot over the same period.

| strategy | mode | TRAIN score | TRAIN p05 | TRAIN t | VALID 2024 score | VALID p05 | VALID t |
|---|---|---:|---:|---:|---:|---:|---:|
| breakout_dual (baseline) | spot | 0.0410 | -8.2% | | 0.0400 | -9.7% | |
| buy_hold | spot | 0.0168 | -16.4% | -2.66 | 0.0391 | -15.6% | -0.06 |
| breakout_dual | perp | 0.0375 | -8.6% | -3.39 | 0.0367 | -10.0% | -2.58 |
| lsb_bear_chandelier | hybrid | 0.0497 | -10.3% | 1.57 | 0.0448 | -9.2% | 0.95 |
| lsb_bear_chandelier_lite | hybrid | 0.0489 | -9.4% | 1.58 | 0.0415 | -9.4% | 0.53 |
| lsb_mirror | hybrid | 0.0431 | -11.6% | 0.45 | 0.0310 | -11.6% | -2.38 |
| lsb_bear_chandelier | perp | 0.0464 | -10.3% | 0.94 | 0.0417 | -9.6% | 0.30 |
| lsb_bear_chandelier_lite | perp | 0.0454 | -9.5% | 0.85 | 0.0384 | -9.7% | -0.44 |
| lsb_mirror | perp | 0.0399 | -11.8% | -0.22 | 0.0282 | -11.4% | -2.96 |

The asymmetric short side held up in the one out-of-sample year (2024): it
added +0.005 in both modes. The mirror lost clearly in 2024.

## Per-year campaign scores (DEV)

| strategy | mode | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | DEV |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| breakout_dual (baseline) | spot | +0.0016 | +0.0698 | +0.0914 | +0.0332 | +0.0003 | +0.0442 | +0.0401 | +0.0401 |
| breakout_dual | perp | +0.0030 | +0.0712 | +0.0816 | +0.0213 | +0.0001 | +0.0416 | +0.0368 | +0.0365 |
| buy_hold | spot | -0.0683 | +0.0576 | +0.1198 | -0.0108 | -0.0499 | +0.0512 | +0.0410 | +0.0199 |
| lsb_bear_chandelier | hybrid | +0.0433 | +0.0646 | +0.1006 | +0.0329 | +0.0142 | +0.0365 | +0.0448 | +0.0482 |
| lsb_bear_chandelier_lite | hybrid | +0.0435 | +0.0656 | +0.0955 | +0.0331 | +0.0099 | +0.0393 | +0.0418 | +0.0470 |
| lsb_mirror | hybrid | +0.0228 | +0.0627 | +0.0857 | +0.0327 | +0.0079 | +0.0414 | +0.0312 | +0.0408 |
| lsb_bear_chandelier | perp | +0.0443 | +0.0660 | +0.0929 | +0.0211 | +0.0141 | +0.0333 | +0.0417 | +0.0448 |
| lsb_bear_chandelier_lite | perp | +0.0449 | +0.0663 | +0.0868 | +0.0212 | +0.0100 | +0.0368 | +0.0384 | +0.0436 |
| lsb_mirror | perp | +0.0240 | +0.0644 | +0.0771 | +0.0211 | +0.0077 | +0.0391 | +0.0283 | +0.0375 |

## Robust sweeps (DEV, from the evaluator JSON)

Each numeric parameter is moved one at a time to ×0.7/0.85/1.15/1.3, plus one
run with the circuit breaker off. That gives 26 runs per candidate and mode.

| variant | chandelier hybrid | chandelier perp | lite hybrid | lite perp |
|---|---:|---:|---:|---:|
| default | +0.0482 | +0.0448 | +0.0470 | +0.0436 |
| entry_n ×0.7 / 0.85 / 1.15 / 1.3 | .0482 / .0487 / .0473 / .0447 | .0446 / .0453 / .0439 / .0418 | .0478 / .0472 / .0460 / .0440 | .0442 / .0436 / .0426 / .0407 |
| exit_n | .0419 / .0472 / .0456 / .0451 | .0389 / .0440 / .0421 / .0418 | .0405 / .0458 / .0436 / .0435 | .0373 / .0424 / .0401 / .0400 |
| trend_n | .0432 / .0462 / .0460 / .0472 | .0388 / .0431 / .0425 / .0435 | .0411 / .0445 / .0443 / .0458 | .0379 / .0415 / .0409 / .0421 |
| regime_n | .0471 / .0487 / .0483 / .0472 | .0435 / .0453 / .0450 / .0437 | .0463 / .0476 / .0471 / .0461 | .0427 / .0441 / .0436 / .0427 |
| short_risk | .0463 / .0475 / .0474 / .0479 | .0427 / .0441 / .0438 / .0444 | .0456 / .0457 / .0474 / .0478 | .0417 / .0422 / .0439 / .0444 |
| chand_k | .0460 / .0492 / .0470 / .0446 | .0425 / .0457 / .0435 / .0411 | .0432 / .0472 / .0457 / .0436 | .0398 / .0435 / .0422 / .0399 |
| no circuit breaker | +0.0475 | +0.0432 | +0.0457 | +0.0414 |
| **mean** | **+0.0467** | **+0.0432** | **+0.0454** | **+0.0419** |
| min | +0.0419 | +0.0388 | +0.0405 | +0.0373 |

`lsb_mirror` scores 0.0392 in hybrid mode and 0.0359 in perp mode.

* Its worst variant is trend_n ×0.7, at 0.0350 hybrid and 0.0316 perp.
* All 26 variants are listed in the evaluator JSON.
* With the circuit breaker off it drops to 0.0360 hybrid and 0.0320 perp. The
  mirror needs the breaker more than any other candidate, because its shorts
  trip it.

The most sensitive parameters are the long side's `exit_n` and `trend_n`.
These have the same weak spots as `breakout_dual`. On the short side the
results are flat, except that `chand_k` falls off on both sides of 6.8-8. Its
TRAIN cliff sits at chand_k ≤ 5.

## Short-side vs long-side attribution

The short-side contribution is the candidate's score minus `breakout_dual`'s
score in the same mode. That is the exact long side with no shorts: the spot
baseline for hybrid mode, and the perp long-only run for perp mode.

| strategy | mode | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | DEV |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lsb_bear_chandelier | hybrid | +0.0416 | -0.0052 | +0.0092 | -0.0002 | +0.0139 | -0.0077 | +0.0047 | **+0.0081** |
| lsb_bear_chandelier_lite | hybrid | +0.0419 | -0.0042 | +0.0041 | -0.0001 | +0.0096 | -0.0048 | +0.0017 | **+0.0069** |
| lsb_mirror | hybrid | +0.0211 | -0.0071 | -0.0057 | -0.0004 | +0.0076 | -0.0028 | -0.0088 | +0.0007 |
| lsb_bear_chandelier | perp | +0.0413 | -0.0052 | +0.0113 | -0.0002 | +0.0140 | -0.0083 | +0.0049 | +0.0083 |
| lsb_bear_chandelier_lite | perp | +0.0419 | -0.0049 | +0.0051 | -0.0001 | +0.0099 | -0.0048 | +0.0016 | +0.0070 |
| lsb_mirror | perp | +0.0210 | -0.0068 | -0.0045 | -0.0002 | +0.0076 | -0.0026 | -0.0085 | +0.0010 |

* For `lsb_bear_chandelier` the short side supplies about 17% of the hybrid
  DEV score (+0.0081 of 0.0482). The long side supplies the rest.
* **2018 alone accounts for about 73% of the short-side gain** (0.0416/7 of
  0.0081 per campaign).
  * 2018 is pre-perp, so funding is zero by protocol, and it was the deepest
    bear market in the sample.
  * Over 2019-2024 the short side adds about +0.0025 per campaign. It loses in
    2019 and 2023 (choppy years), and is about flat in 2021.
* Run on its own, the short leg returns +0.98% on an average 30-day campaign
  (p05 -4.3%). It is positive in 27.5% of campaigns, negative in 29.4%,
  and flat (never short) in 43%.

Continuous DEV backtest: one run from 10,000 USDT. USDT P&L compounds, so
later years dominate it.

| strategy | mode | long trips: n, P&L, win% | short trips: n, P&L, win% | funding: longs paid / shorts received | fees long / short | time long / short |
|---|---|---|---|---|---|---|
| lsb_bear_chandelier | hybrid | 124, +531,517, 30% | 75, +59,301, 39% | 0 / 2,248 | 43,515 / 6,502 | 49% / 18% |
| lsb_bear_chandelier_lite | hybrid | 126, +525,065, 30% | 73, +41,745, 38% | 0 / 1,747 | 43,937 / 4,757 | 49% / 19% |
| lsb_mirror | hybrid | 127, +333,722, 29% | 117, **-30,121**, 23% | 0 / 4,996 | 25,273 / 6,458 | 49% / 41% |
| lsb_bear_chandelier | perp | 128, +426,969, 28% | 75, +45,570, 39% | **74,786** / 1,667 | 16,822 / 4,897 | 49% / 18% |
| lsb_bear_chandelier_lite | perp | 130, +423,979, 28% | 73, +33,002, 38% | 76,447 / 1,268 | 17,026 / 3,581 | 49% / 19% |
| lsb_mirror | perp | 131, +281,909, 28% | 117, **-19,469**, 23% | 46,665 / 3,892 | 10,222 / 5,071 | 49% / 41% |

Trip P&L is realised P&L net of fees, excluding funding. Mean short exposure
while short: 0.50 (chandelier), 0.38 (lite) and 0.39 (mirror).

### Funding

* **Shorts receive almost no funding.** Funding as a share of equity, from
  exposure × 8h rate, by year:

  | strategy | 2020 | 2021 | 2022 | 2023 | 2024 | mean |
  |---|---:|---:|---:|---:|---:|---:|
  | lsb_bear_chandelier (shorts) | -0.30% | +0.04% | +0.59% | +0.13% | +0.18% | **+0.13%/yr** |
  | lsb_mirror (shorts) | | | | | | 0.74%/yr |

  * In 2020 the chandelier's shorts paid funding, because rates were negative
    during the March crash.
  * The bear-regime shorts are open when funding is lowest. Average funding by
    how far price sits above EMA600: 1.8%/yr in the lowest decile, 43%/yr in
    the highest.
* **All-perp longs pay a lot.** Exposure-weighted funding per year:

  | 2020 | 2021 | 2022 | 2023 | 2024 |
  |---:|---:|---:|---:|---:|
  | 14.0% | 22.3% | 1.8% | 5.0% | 8.0% |

  This costs `breakout_dual` 0.0036 per campaign on perps (paired t -3.7
  against spot), even though perp fees are half of spot fees. This is why
  hybrid mode is the only sensible way to run a long/short version.

## What was tried (TRAIN 2018-2023, perp cost model)

Scores are TRAIN campaign scores. For reference, long-only `breakout_dual`
scores 0.0375 on perps and 0.0410 on spot. "Short 0.5" means the short
exposure is capped at 0.5.

1. **Symmetric mirror (the angle's starting point).**

   | short weight | score | p05 |
   |---|---:|---:|
   | 1.0 | 0.0350 | -15.3% |
   | 0.5 | 0.0399 | -11.8% |

   * The mirrored short legs alone score 0.0006. Only 2018 (+0.036) and 2022
     (+0.008) are positive.
   * BTC bear-market rallies and V-bottoms squeeze a short whose exit is the
     previous N-bar high. In the tail campaigns the mirror's shorts and the
     long breakouts lost in the *same* choppy months (Dec 2018, Mar 2020, Jul
     2022). The correlation of the two legs' 30-day returns is -0.03, so the
     tails add rather than offset.
2. **Short legs alone, half size, coarse grids.** 48 Donchian, 33 Keltner and
   25 regime configurations.

   | short leg | best score |
   |---|---:|
   | Donchian, entry 72-144 / exit 48 / EMA2400 filter | +0.004-0.005 |
   | Keltner, EMA600 − 6·ATR (strict entry) | +0.0049 |
   | Keltner, EMA600 − 3·ATR (mirror) | +0.0020 |

   * A slow (2400-bar) trend filter beat the long side's 600 EMA for
     Donchian shorts.
   * Faster Keltner exits (EMA − 1-2·ATR) were worse.
   * SMA-slope regime filters (price below SMA *and* SMA falling) mostly
     hurt, at -0.008 to +0.003.
3. **Long side plus the best short legs.**
   * Long + Donchian short (144/48, EMA2400) scored 0.0412 at short 0.5 and
     0.0437 at short 1.0. Its p05 was -11.5% and -15.3%: the score is there
     but the tail breaks rule 4.
   * Keltner short add-ons hurt at short 1.0 (0.0262-0.0333).
4. **Tail control that failed.**
   * *Engine protective stops* (`stop_dist`) on shorts, at 3-9·ATR168 or
     4-9·ATR24: best 0.0418 / 0.0451 (6·ATR24), with p05 unchanged at -11% /
     -15%. Tight stops (3·ATR168) cost 0.004-0.009. The engine's re-arm rule
     blocks re-entry until the latch resets, as on the long side.
   * *Faster Donchian exits* (24/36 bars): 0.0330-0.0381, worse.
   * *No shorts when ATR24/ATR720 > 1.25-2*: no change.
   * *Take-profit band* (cover when the close is k·ATR below EMA600): lowered
     p05 to -8.6% but cut the score to 0.037-0.040.
   * *Smaller fixed size* (short 0.25): 0.0384, p05 -9.6%.
5. **Circuit-breaker interaction.**
   * Removing the breaker costs long-only perp 0.005 (0.0375 → 0.0328).
   * Shorts that lose can trip it and block the longs for 48h (seen in April
     2020). The interaction is small once the chandelier exit is used: the
     tail decomposes almost additively.
6. **Funding-aware long sizing (failed).** I capped the long at 0.5 or 0 while
   price was 8-25% above EMA600, where funding averages 25-43%/yr.
   * Nine of the ten settings scored worse, at 0.0104-0.0372 against 0.0375.
     The exception (flat above +25%) scored 0.0389, a single point next to
     0.0372 at cap 0.5, so it is noise rather than a plateau. The strongest
     trends earn far more than the funding they pay.
   * This is a long-side idea and is not in the module. Hybrid mode solves
     the funding problem properly.
7. **Chandelier exit (the step that worked).** Exit when close > lowest low of
   the previous cx bars + m·ATR(24), with the short sized by volatility and the
   size fixed at entry:

   | change | TRAIN score |
   |---|---:|
   | Donchian short + chandelier (48, 6·ATR24), short 0.5 | 0.0412 → 0.0425 |
   | Same, short 1.0 | 0.0437 → 0.0471 |
   | Average over a 216-config grid, with vs without chandelier | 0.0451 vs 0.0418 |

   * In the 216-config grid (ref × entry × exit × EMA filter × chandelier):
     * Entry length (96-216), Donchian exit length and filter length
       (1200-3600) were flat.
     * The size reference set the score/tail trade-off.
     * No configuration with p05 ≥ -9.2% scored above 0.0447.
   * In a 96-config chandelier grid (lookback × multiplier × ATR length):
     * ATR24 beat ATR48 (0.0433 vs 0.0422 on average).
     * Score by multiplier: 4 → 0.0401, 5 → 0.0422, 6 → 0.0446, 8 → 0.0441.
     * Lookback 24-96 was flat.
     * The Donchian "previous-high" exit became redundant (0.0427 vs 0.0428),
       so it was dropped.
   * A finer grid at lookback 72:
     * m = 6-10 → 0.0451-0.0472, and m = 5 → 0.0410, a cliff.
     * The chandelier is useless with ATR168 instead of ATR24 (0.0357-0.0411).
8. **Structure checks.**
   * Regime filter: 2400-4800 forms a plateau (0.0435-0.0444 on average) and
     7200 is worse (0.0408). An SMA at 2400-6000 matched the EMA (0.0451-0.0464)
     and needs far less live history, so SMA(3600) was chosen.
   * Stricter regimes (EMA600 < regime MA, or a falling regime MA): 0.0435-0.0443
     against 0.0472. They miss the March 2020 crash.
   * Keltner-style short entry instead of the Donchian breakdown: 0.0417-0.0439.
   * A second (Keltner) short leg: 0.0434-0.0458.
   * Fixed short size 0.35 / 0.5 / 0.75: 0.0416 / 0.0441 / 0.0476, with p05
     -10.2% / -11.2% / -13.6%. Volatility sizing gives a better score per unit
     of tail.
   * Size cap 0.5: worse. Sizing on ATR24 instead of ATR168: the same.
9. **TRAIN robust sweep of the module code** (the evaluator's perturbations,
   TRAIN only):

   | variant | TRAIN robust | min |
   |---|---:|---:|
   | chand_k 8 | 0.0447 | 0.0402 |
   | chand_k 7 | 0.0447 | 0.0400 (falls to 0.0404 at chand_k×0.7) |
   | lite | 0.0435 | |
   | mirror | 0.0380 | |

   chand_k = 8 was chosen because its downward perturbation stays off the
   cliff. `short_risk` 0.004 is the default; 0.003 (lite) is the setting
   closest to the p05 limit on TRAIN (-9.5%).

## Why the rule is not met, and what would be needed

* **Rule 2 (t ≥ 2).** The short side adds +0.008 per campaign, but its
  month-to-month variance is large, giving a block-bootstrap standard error of
  0.0047. Reaching t = 2 would need roughly +0.0095 with the same variance.
  The only way there would be a more aggressive short, and that breaks rule 4.
* **Rule 4 (p05).** The 30-day tail is set by choppy months in which the long
  breakouts and the short breakdowns *both* whipsaw: Sep and Dec 2018, Jul
  2022, Aug 2023. No exit, stop, regime or volatility filter tried here
  separated those months from the profitable bear months on TRAIN. On
  TRAIN, no configuration with p05 ≥ -9.2% scored above 0.0447. The lite
  candidate (TRAIN p05 -9.5%) ended at -9.46% on DEV, 0.1 pp outside the
  limit.
* **Concentration.** About 73% of the short-side gain is 2018, a single deep
  bear market with no funding data. Even setting the rules aside, I would not
  call the evidence for the short side strong.

## Overfitting assessment

**Search breadth: medium to high.** About 630 TRAIN configurations went into
one family. The decisive design choice, the ATR24 chandelier at multiplier
6-10, was found by search, and it has a cliff below 6.

**What reduces the risk:**

* The six swept parameters and the four fixed constants are flat in DEV
  sweeps.
* The regime and entry lengths are wide plateaus.
* The short side was positive in the one out-of-sample year (2024: +0.005).
* The candidates were frozen before DEV was seen, and were not re-tuned for
  hybrid mode.

**What increases it:**

* The 2018 concentration.
* The chand_k cliff.
* 2019-2024 alone gives only +0.0025 per campaign for the short side.

## Recommendation

* Keep `breakout_dual` on spot as the deployment. This is what the protocol
  requires, and the margin found here would not justify the extra operational
  risk of a futures account in any case.
* If a long/short paper run is wanted as an experiment, use
  `lsb_bear_chandelier` with `run.instrument = "hybrid"` and
  `risk.max_short = 1.0`. All-perp mode gives up about 0.0035 per campaign to
  funding.
* The 2025-26 holdout should not be used to promote it. As the protocol notes,
  that period is a known decline and would flatter any short design.

## Scratch scripts

These are not in the repo:
`/tmp/claude-0/-home-user-trading-app/51d8e21b-b4d0-5c80-97c0-ba14790b0d74/scratchpad/ls-research/ls_breakout/`

| file | purpose |
|---|---|
| `common.py`, `grid.py` | harness and prototypes |
| `e1.py`-`e14.py` | experiments (logs and JSON alongside) |
| `train_robust.py` | TRAIN robust sweeps |
| `verify.py` | module equals prototypes; causality and timing |
| `eval_*.json/txt`, `evalh_*.json/txt` | evaluator outputs, perp and hybrid |
| `attrib.py` | attribution |
| `consts.py` | sweep of the fixed constants |
| `splits.py` | TRAIN/VALID splits |
| `bh.py` | buy-and-hold reference |
| `tables.py` | report tables |
| `fundstudy.py` | funding versus price extension |
| `tail.py`, `tail2.py` | tail decomposition |
