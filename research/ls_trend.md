# Long/short trend research (angle: ls-trend)

Module: `src/tradebot/strategies/ls_trend.py`. It registers two strategies,
`lst_dual_bear` and `lst_dual_overlay`.

## Short answer

**Is the bot better than buying and holding?** Yes, on 2018-2024 data
(DEV), with the caveats in "Honest conclusion". The deployed long-only
`breakout_dual` (spot) has a DEV score of 0.0401. Buy-and-hold under the same
risk overlay scores 0.0199. The score is the mean log growth of a 30-day run.
Over the whole period, `breakout_dual` returned 35x (CAGR 66%, max drawdown
32.5%). Holding BTC returned 6.9x (CAGR 31.8%, max drawdown 81.4%).

**Can shorting add to that?** Yes, but only a little, and only when the
shorts are built to be asymmetric. Mirroring the long trend follower on the
short side loses money on BTC. Everything symmetric that I tried lost too.
What worked has four conditions:

* shorts only in a bear regime (price below its 150-day average);
* a short opens only on a fresh 10-day-low breakdown;
* the short is covered fast, on a 2-day high;
* no short at all once price is more than 30% below the 150-day average.
  Capitulation lows are where BTC's violent short squeezes start.

`lst_dual_bear` adds this short leg to `breakout_dual`. It also caps all bear-regime exposure at 0.75.
In hybrid mode (spot longs, perp shorts) its DEV results are:

* score 0.0535, robust score 0.0508;
* paired t of 2.79 against the deployed strategy;
* positive in 7 of 7 years;
* 5th-percentile 30-day return (p05) of -8.70%.

**Adoption rule** (research/LONG_SHORT_PROTOCOL.md):

* Both candidates meet rules 1-4 in hybrid mode.
* `lst_dual_overlay` also meets rules 1-4 in perp mode.
* `lst_dual_bear` in perp mode misses rule 2: its t is 1.98, against 2.00 needed.
* Rule 5 needs the independent audit, which I cannot provide myself.

Under the rule, the candidate with the highest robust score wins: that is
`lst_dual_bear` in **hybrid** mode. The main reasons for caution are below. The
evidence is mostly in-sample. The one out-of-sample year (2024) was slightly
negative for both candidates. The short-side gain comes mostly from about five
crash episodes.

## Protocol followed

* Data: `data/research/BTCUSDT-1h-research.csv` and
  `data/research/BTCUSDT-funding-research.csv`, both ending 2024-12-31. No other
  market data was used, and no memory of later prices.
* Design and tuning used **TRAIN 2018-2023 only**. The scratch harness ran
  `tradebot.backtest.run_campaigns` with `funding=` on the TRAIN period. Each
  campaign set was compared, paired, with `breakout_dual` spot TRAIN campaigns
  (TRAIN score 0.0410, p05 -8.21%). Most tuning ran in hybrid mode, the
  realistic deployment (protocol amendment 1). Perp mode was checked alongside.
* Search size: about 600 TRAIN campaign-set evaluations, covering about 550
  distinct configurations including sensitivity checks. This breadth matters
  when reading the t-statistics; see the honest conclusion.
* VALID 2024 and the DEV evaluator were run **once**, for the two frozen
  candidates. Nothing was changed afterwards.
* Final checks:
  * `research.check_causal(strategy, df, n_checks=30) == []` on the research
    data. This covers the prefix check and the warmup+1 window check. It passes
    for both defaults and for **every robust-sweep variant** of both strategies.
    It also passes with three extra seeds at 60 checks each.
  * Live-window test: 60 random 6720-bar windows give 0 mismatches. That window
    is the bot's `max(lookback_bars, warmup + 720)` fetch.
  * `signals()` on the full 64.5k-bar file takes 0.04 s.
  * `tests/` pass, including the contract and causality tests that run over
    all strategies.
  * Forward-run parity: `simulate.replay` (the live tick path) was run on three
    30-day starts in both modes (2018-11, 2020-03, 2022-05). Its final cash
    equals the campaign backtest's to the cent.
* Parameter count: each strategy has **5 tunable parameters**. The long leg is
  the deployed `breakout_dual` with its selected defaults, frozen in
  `LONG_LEG_PARAMS`. Its 6 parameters were tuned in the earlier long-only
  research and were not re-tuned here. The evaluator's robust sweep therefore
  perturbs only the 5 new parameters. I added an extended sweep over the long
  leg's parameters as well; see "Extra robustness".

## The strategies

Shared short leg `S`, a 0/1 latch built in `bear_short_state`:

* **Bear regime:** `dev = close / SMA(regime_days * 24 hourly closes) - 1 < 0`.
* **Entry:** close < lowest low of the previous `short_entry_n` bars, and
  `-floor < dev < 0`.
* **Exit:** close > highest high of the previous `short_exit_n` bars, or
  `dev <= -floor` (oversold floor), or `dev >= 0` (regime over).
* **Longs have priority:** `S` is forced to 0 while the long leg holds anything.

The two strategies differ in how they combine the legs:

| strategy | target | default params (5 tunable) |
|---|---|---|
| `lst_dual_bear` | `L * (bear_exposure if dev < 0 else 1) - bear_exposure * S` | short_entry_n=240 (10 d), short_exit_n=48 (2 d), regime_days=150, floor=0.30, bear_exposure=0.75 |
| `lst_dual_overlay` | `L - short_weight * S` (long leg untouched) | short_entry_n=240, short_exit_n=48, regime_days=150, floor=0.30, short_weight=0.5 |

`L` is the `breakout_dual` target: 0, 0.5 or 1. Exposure levels:

* `lst_dual_bear`: 0 / 0.5 / 1 above the 150-day average; -0.75 / 0 / 0.375 / 0.75 below it.
* `lst_dual_overlay`: -0.5 / 0 / 0.5 / 1.

To run either one, set `run.instrument = "hybrid"` (or `"perp"`) and
`risk.max_short >= 0.75` (or `0.5` for the overlay). On a spot config the
engine clips targets at 0, so the strategy reduces to its long leg.

Why each piece is there:

1. **Bear-regime gate.** On TRAIN, the next-day return averaged +19.7 bp/day
   above the 150-day SMA and -8.6 bp/day below it. Shorting above the average
   fights BTC's drift.
2. **Event entry (a fresh 10-day low), not a level entry.** When the trigger
   was a level condition (SMA band, momentum sign, Keltner band), the short
   re-entered straight after every exit. Those triggers scored 0.040-0.049 on
   TRAIN, against 0.053-0.057 for the Donchian event trigger, and had p05 of
   -10% to -15%.
3. **Fast exit on a 2-day high.** Bear-market rallies are short and violent.
   A 1-day exit (24 bars) churned and scored about 0.007 lower. Exits of 36-96
   bars all scored within 0.003 of each other.
4. **Oversold floor.** This is the key piece. Next-day returns on TRAIN by
   distance from the 150-day SMA:

   | distance from SMA | share of TRAIN | mean next-day return |
   |---|---:|---:|
   | below -40% | 2% | +52.5 bp/day |
   | -40% to -25% | 12% | +33.4 bp/day |
   | -25% to -15% | 16% | -15.5 bp/day |
   | -15% to -5% | 15% | -36.5 bp/day |
   | -5% to 0% | 6% | -24.1 bp/day |
   | 0% to +10% | 11% | +13.1 bp/day |

   Shorting within 25-30% below the average catches the trend. Shorting deeper
   catches the squeeze. Adding the floor lifted the paired TRAIN gain of a
   Donchian short from +0.004 (t 0.7) to between +0.011 and +0.016 (t 1.8-2.5),
   for every floor between 0.15 and 0.30.
5. **Bear-regime exposure cap (`lst_dual_bear` only).** In choppy bear months
   both sides whipsaw at once: the long gets stopped at a failed breakout, then
   the short gets squeezed. Their losses add up in the same 30-day run, and the
   -15% campaign stop then forfeits the recovery. Scaling bear-regime longs and
   shorts to 0.75 moved TRAIN p05 from -9.8% to -8.6% at no cost in score
   (0.0573 to 0.0575).

## What I tried (TRAIN 2018-2023, hybrid unless noted)

Compared with `breakout_dual` spot TRAIN: score 0.0410, p05 -8.21%. "d" and
"t" are the paired 30-day difference against it.

| # | idea | result | verdict |
|---|---|---|---|
| 1 | Mirrored `breakout_dual` short legs (Keltner, Donchian, both). Ungated, or gated below the 100d/200d SMA | Short-only score -0.007 to +0.002. Combined with longs, at best 0.0422 (d +0.001, t 0.15), with p05 -15% | Fails. Bear rallies plus fees eat the bear trends. |
| 2 | SMA-band short votes: 3 SMAs (13-68 d), 2-10% entry band, 0-2% exit band, with/without 150d gate | Short-only best +0.0056; ungated versions mostly negative. p05 -10% to -15% | Fails. |
| 3 | Donchian shorts with tight exits (entry 72-240, exit 24-96, 150d gate) | Short-only best +0.0056 (144/48 and 240/48). A 24-bar exit is always negative | Better exit; still weak. |
| 4 | ATR trailing stops on shorts (`stop_dist` only while short; 4/6/8/10 × ATR168) | 6×: short-only band leg 0.0073, but combined t ≤ 1. With the floor, stops cut the score (0.0619 → 0.0559 at 6×) | Rejected. The re-arm rule blocks re-entry, as in the long-only research. |
| 5 | TSMOM: short when the 1w, 1m and 3m returns are all negative | Combined 0.021-0.035 (d -0.006 to -0.020) | Fails. The 1-week sign flips constantly. |
| 6 | Falling-SMA gate (150d SMA below its value 30 d ago) | -0.003 to -0.009 versus the plain gate | Rejected. It lags too much. |
| 7 | Vol-scaled short size `min(1, vt / vol30)`, vt 0.3-0.8 | Small tail gain, no score gain. With the floor: 0.0563-0.0571 against 0.0575 at fixed 0.75 | Rejected (adds a parameter and churn). |
| 8 | No short until the long leg has been flat 3/7/14 d; time stop at 5/10/20 d; death-cross or SMA100/200 regimes | d +0.001 to +0.006, t ≤ 1 | Nothing robust. |
| 9 | **Oversold floor** (from the conditional-return table above), applied as a mask, with hysteresis, or as a latch exit | d +0.009 to +0.021, t 1.8-2.9 | **Kept** (latch form, which removes floor churn). |
| 10 | Short size 0.5 / 0.75 / 1.0 | p05 -9.0% / -9.8% / -11.0%. d +0.012 / +0.016 / +0.021 (floor 0.3) | Size is a tail/score trade-off. |
| 11 | 270-point grid: floor 0.2-0.4 × size {0.5, 0.75} × regime {100, 150, 200} × entry {144, 240, 360} × exit {24, 48, 72} | Regime 100 ≈ 150 (mean 0.0475 / 0.0474); 200 worse (0.0438). Exit 24 worse. Entry 144-360 flat | Picked regime 150, exit 48, entry 240. |
| 12 | Bear-regime long scaling 0.5 / 0.75 | p05 -8.0% / -8.6% versus -9.8% at size 0.75; score 0.0557 / 0.0575 versus 0.0573 | **Kept 0.75** and tied it to the short size (one parameter). |
| 13 | Fine grid: regime 100-175 × floor 0.22-0.38, then regime 135-165 × floor 0.27-0.33 | Plateau 0.051-0.059 for regime 125-150 and floor 0.22-0.34. Floor 0.30 at regime 150 is a mild local peak (neighbours 0.053-0.056) | Kept 0.30; the peak is disclosed. |
| 14 | `bear_exposure` 0.5 / 0.6 / 0.75 / 0.9 / 1.0 | 0.0512 / 0.0535 / 0.0575 / 0.0610 / 0.0619; p05 -7.5 / -8.0 / -8.6 / -9.6 / -11.0% | Chose 0.75: the largest value whose TRAIN p05 stays within ~0.5 pp of the baseline. |
| 15 | Same short leg on other long legs (Keltner, Donchian, `trend_sma_band`) | Gains of +0.014 / +0.018 / +0.013 over each long leg alone. The SMA-band version's p05 is -11.4% | The short edge is not specific to the dual long leg. The SMA-band long leg fails the tail. |
| 16 | Long-leg robustness: `breakout_dual` params at ±15/30% under the overlay | Gain +0.0155 to +0.0177 in all 25 variants | Robust. |

TRAIN numbers for the frozen candidates:

| candidate | mode | score | d | t | p05 | per-year scores 2018-2023 |
|---|---|---:|---:|---:|---:|---|
| `lst_dual_bear` | hybrid | 0.0575 | +0.0165 | 3.01 | -8.62% | +0.029 +0.080 +0.108 +0.045 +0.035 +0.046 |
| `lst_dual_bear` | perp | 0.0540 | +0.0130 | 2.28 | -8.7% | +0.030 +0.082 +0.099 +0.033 +0.034 +0.045 |
| `lst_dual_overlay` | hybrid | 0.0531 | +0.0121 | 3.33 | -9.02% | +0.021 +0.076 +0.100 +0.047 +0.025 +0.048 |

Both modes also ran without the circuit breaker. For `lst_dual_bear` hybrid that
gives TRAIN 0.0542, against 0.0371 for the baseline without the breaker.

## VALID 2024 (looked at once, for the frozen candidates)

| | hybrid | perp |
|---|---:|---:|
| `breakout_dual` | 0.0400 | 0.0367 |
| `lst_dual_bear` | 0.0347 (d -0.0052, t -0.92, p05 -9.3%) | 0.0317 |
| `lst_dual_overlay` | 0.0391 (d -0.0008, t -0.26, p05 -9.2%) | 0.0359 |

2024 was a bull year with few bear-regime days. The short leg lost a little in
the August-September correction, and the bear cap trimmed some long gains.
**The only out-of-sample year gives no support for the short edge.** It does
not refute it either: the difference is well inside one standard error.

## Full `scripts/ls_evaluate.py` output (DEV 2018-2024, run once per candidate and mode)

```
$ python3 scripts/ls_evaluate.py lst_dual_bear --max-short 1.0 --mode hybrid
lst_dual_bear(short_entry_n=240, short_exit_n=48, regime_days=150, floor=0.3, bear_exposure=0.75)  (max_short=1.0, mode=hybrid)
  DEV score +0.0535 (baseline spot +0.0401); robust +0.0508
  30d: win 56.9% lose 43.1% mean +6.29% median +2.26% p05 -8.70% worst -17.48%
  paired vs baseline: mean diff +0.0134 se 0.0048 t +2.79
  years: 2018:+0.029 2019:+0.080 2020:+0.108 2021:+0.045 2022:+0.035 2023:+0.041 2024:+0.035
  continuous: CAGR +94.2% Sharpe 1.90 maxDD 30.3% trades 710 short-time 8% funding paid -3,313
  rule: {'1_robust_plus_0.005': True, '2_paired_t_ge_2': True, '3_years_positive_ge_5': True, '4_p05_within_1pp': True}

$ python3 scripts/ls_evaluate.py lst_dual_bear --max-short 1.0 --mode perp
lst_dual_bear(short_entry_n=240, short_exit_n=48, regime_days=150, floor=0.3, bear_exposure=0.75)  (max_short=1.0, mode=perp)
  DEV score +0.0500 (baseline spot +0.0401); robust +0.0473
  30d: win 56.4% lose 43.6% mean +5.88% median +2.16% p05 -8.82% worst -17.39%
  paired vs baseline: mean diff +0.0099 se 0.0050 t +1.98
  years: 2018:+0.030 2019:+0.082 2020:+0.099 2021:+0.033 2022:+0.034 2023:+0.038 2024:+0.032
  continuous: CAGR +83.7% Sharpe 1.76 maxDD 31.7% trades 714 short-time 8% funding paid +113,803
  rule: {'1_robust_plus_0.005': True, '2_paired_t_ge_2': False, '3_years_positive_ge_5': True, '4_p05_within_1pp': True}

$ python3 scripts/ls_evaluate.py lst_dual_overlay --max-short 1.0 --mode hybrid
lst_dual_overlay(short_entry_n=240, short_exit_n=48, regime_days=150, floor=0.3, short_weight=0.5)  (max_short=1.0, mode=hybrid)
  DEV score +0.0503 (baseline spot +0.0401); robust +0.0486
  30d: win 57.3% lose 42.7% mean +5.96% median +1.63% p05 -9.04% worst -16.33%
  paired vs baseline: mean diff +0.0102 se 0.0032 t +3.22
  years: 2018:+0.021 2019:+0.076 2020:+0.100 2021:+0.047 2022:+0.025 2023:+0.042 2024:+0.039
  continuous: CAGR +85.8% Sharpe 1.80 maxDD 29.0% trades 565 short-time 8% funding paid -1,799
  rule: {'1_robust_plus_0.005': True, '2_paired_t_ge_2': True, '3_years_positive_ge_5': True, '4_p05_within_1pp': True}

$ python3 scripts/ls_evaluate.py lst_dual_overlay --max-short 1.0 --mode perp
lst_dual_overlay(short_entry_n=240, short_exit_n=48, regime_days=150, floor=0.3, short_weight=0.5)  (max_short=1.0, mode=perp)
  DEV score +0.0473 (baseline spot +0.0401); robust +0.0454
  30d: win 56.6% lose 43.4% mean +5.59% median +1.62% p05 -8.93% worst -16.25%
  paired vs baseline: mean diff +0.0072 se 0.0034 t +2.09
  years: 2018:+0.023 2019:+0.077 2020:+0.094 2021:+0.035 2022:+0.025 2023:+0.040 2024:+0.036
  continuous: CAGR +76.3% Sharpe 1.66 maxDD 31.3% trades 569 short-time 8% funding paid +88,704
  rule: {'1_robust_plus_0.005': True, '2_paired_t_ge_2': True, '3_years_positive_ge_5': True, '4_p05_within_1pp': True}
```

Rule 1 needs a robust score of at least 0.04415, which is 0.03915 + 0.005.
Rule 4 needs p05 ≥ -9.36%. "funding paid" is for the continuous 10k-USDT run,
which compounds; a negative number means funding was received.

### Robust sweep (DEV score per variant)

| variant | `lst_dual_bear` hybrid | `lst_dual_bear` perp | `lst_dual_overlay` hybrid | `lst_dual_overlay` perp |
|---|---:|---:|---:|---:|
| default | 0.0535 | 0.0500 | 0.0503 | 0.0473 |
| short_entry_n ×0.7 / ×0.85 | 0.0533 / 0.0536 | 0.0498 / 0.0501 | 0.0510 / 0.0507 | 0.0474 / 0.0477 |
| short_entry_n ×1.15 / ×1.3 | 0.0516 / 0.0507 | 0.0488 / 0.0480 | 0.0497 / 0.0493 | 0.0465 / 0.0461 |
| short_exit_n ×0.7 / ×0.85 | 0.0493 / 0.0521 | 0.0458 / 0.0487 | 0.0465 / 0.0495 | 0.0433 / 0.0464 |
| short_exit_n ×1.15 / ×1.3 | 0.0511 / 0.0517 | 0.0477 / 0.0484 | 0.0497 / 0.0501 | 0.0466 / 0.0470 |
| regime_days ×0.7 / ×0.85 | 0.0469 / 0.0526 | 0.0434 / 0.0491 | 0.0461 / 0.0500 | 0.0429 / 0.0469 |
| regime_days ×1.15 / ×1.3 | 0.0488 / 0.0457 | 0.0453 / 0.0423 | 0.0470 / 0.0449 | 0.0436 / 0.0416 |
| floor ×0.7 / ×0.85 | 0.0492 / 0.0479 | 0.0457 / 0.0444 | 0.0465 / 0.0467 | 0.0435 / 0.0434 |
| floor ×1.15 / ×1.3 | 0.0480 / 0.0460 | 0.0445 / 0.0425 | 0.0468 / 0.0458 | 0.0437 / 0.0427 |
| size ×0.7 / ×0.85 (`bear_exposure` / `short_weight`) | 0.0489 / 0.0509 | 0.0454 / 0.0475 | 0.0469 / 0.0484 | 0.0436 / 0.0452 |
| size ×1.15 / ×1.3 | 0.0565 / 0.0563 | 0.0529 / 0.0531 | 0.0522 / 0.0526 | 0.0492 / 0.0496 |
| no circuit breaker | 0.0520 | 0.0477 | 0.0482 | 0.0439 |
| **robust mean (n = 22)** | **0.0508** | **0.0473** | **0.0486** | **0.0454** |
| min | 0.0457 | 0.0423 | 0.0449 | 0.0416 |

In hybrid mode every variant of both candidates exceeds 0.04415. The weakest
directions are a longer regime average (×1.3) and any change to the floor.
**The default floor of 0.30 is a local peak:** ±15% costs about 0.005. That
matches the mild peak seen on TRAIN, and it is the clearest overfitting
signature in this design.

## Per-year DEV scores (difference vs `breakout_dual` spot in brackets)

| year | `breakout_dual` spot | `breakout_dual` perp | `lst_dual_bear` hybrid | `lst_dual_bear` perp | `lst_dual_overlay` hybrid | `lst_dual_overlay` perp |
|---|---:|---:|---:|---:|---:|---:|
| 2018 | +0.0016 | +0.0030 | +0.0293 (+0.0277) | +0.0303 (+0.0287) | +0.0215 (+0.0198) | +0.0230 (+0.0214) |
| 2019 | +0.0698 | +0.0712 | +0.0805 (+0.0107) | +0.0819 (+0.0122) | +0.0762 (+0.0064) | +0.0768 (+0.0071) |
| 2020 | +0.0914 | +0.0816 | +0.1081 (+0.0167) | +0.0988 (+0.0074) | +0.1000 (+0.0086) | +0.0941 (+0.0027) |
| 2021 | +0.0332 | +0.0213 | +0.0449 (+0.0117) | +0.0330 (-0.0001) | +0.0474 (+0.0142) | +0.0355 (+0.0023) |
| 2022 | +0.0003 | +0.0001 | +0.0346 (+0.0343) | +0.0345 (+0.0342) | +0.0249 (+0.0246) | +0.0249 (+0.0246) |
| 2023 | +0.0442 | +0.0416 | +0.0406 (-0.0036) | +0.0381 (-0.0060) | +0.0421 (-0.0021) | +0.0395 (-0.0046) |
| 2024 | +0.0401 | +0.0368 | +0.0346 (-0.0054) | +0.0316 (-0.0085) | +0.0392 (-0.0008) | +0.0360 (-0.0041) |

The improvement comes from the bear and crash years: 2018, 2022, the
November 2019 and March 2020 sell-offs, and May 2021. It is negative in
2023 and 2024, the two most recent years, both bull markets.

## Attribution: short side, long side and funding (DEV)

Campaign attribution. "Long part" is the same strategy with its target
clipped at ≥ 0. "Short contribution" is the full score minus the long part's
score.

| | baseline spot | long-side change | short contribution | full |
|---|---:|---:|---:|---:|
| `lst_dual_bear` hybrid | 0.0401 | -0.0008 (bear cap) | **+0.0141** | 0.0535 |
| `lst_dual_bear` perp | 0.0401 | -0.0042 (cap + long funding) | **+0.0141** | 0.0500 |
| `lst_dual_overlay` hybrid | 0.0401 | 0.0000 | **+0.0102** | 0.0503 |
| `lst_dual_overlay` perp | 0.0401 | -0.0036 (long funding) | **+0.0107** | 0.0473 |

Short contribution by year for `lst_dual_bear` hybrid (campaign score):

| 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 |
|---:|---:|---:|---:|---:|---:|---:|
| +0.031 | +0.009 | +0.017 | +0.015 | +0.034 | -0.003 | -0.003 |

The bear cap's effect on the long part, year by year, is between -0.003 and +0.002.

* **The whole margin over the baseline comes from shorts.** In hybrid mode the
  long side is the baseline. `lst_dual_bear` gives up 0.0008 on the long side
  for a better tail: the long part alone has p05 -8.08%, against -8.36% for the
  baseline.
* **The short leg alone** (target clipped at ≤ 0):
  * `lst_dual_bear`: DEV score +0.0139, p05 -4.05%.
  * `lst_dual_overlay`: +0.0105, p05 -2.70%.
  * Half of all 30-day campaigns contain at least one short.
  * The bot is short 8% of the time and long 50% of the time.
* **Round trips in the continuous DEV run, hybrid mode:**
  * `lst_dual_bear`: 64 shorts with a 50% win rate and a mean trip return of
    +2.0%, against 124 longs with a 29% win rate and +2.5%.
  * `lst_dual_overlay`: 62 shorts, 47% win rate, +2.4%.
* **Concentration.** The total log contribution of short trips in the
  continuous run is +1.25 for `lst_dual_bear` (+0.87 for the overlay).
  * The 5 best trips supply +0.97 of that (+0.68 for the overlay). Without
    them it is still positive, at +0.27 (+0.19).
  * The best trips: 2020-03-08 to 03-12 (+0.245), 2018-11-14 to 11-20
    (+0.232), 2019-11-12 to 11-25 (+0.179), 2022-05-05 to 05-11 (+0.179), and
    2018-02-05 to 02-06 (+0.137).
  * By year (log): 2018 +0.39 (19 trips), 2019 +0.15 (6), 2020 +0.19 (3),
    2021 +0.17 (7), 2022 +0.43 (17), 2023 -0.04 (5), 2024 -0.03 (7).
* **Funding.** Figures are mean USDT per 10,000-USDT 30-day campaign; a
  positive number means paid.

  | | longs | shorts | total |
  |---|---:|---:|---:|
  | `lst_dual_bear` hybrid | 0 | -0.9 | -0.9 |
  | `lst_dual_bear` perp | +63.8 | -0.9 | +62.9 |
  | `lst_dual_overlay` hybrid | 0 | -0.6 | -0.6 |
  | `lst_dual_overlay` perp | +64.8 | -0.6 | +64.0 |

  * Perp longs pay about 0.64% of equity per campaign.
  * Shorts receive under 0.01% per campaign. They run in bear markets, when
    funding is near zero: 2022 averaged +4%/yr against +31% in 2021.
  * Continuous DEV run in perp mode, with compounding equity:
    * `lst_dual_bear`: longs paid 116,168, shorts received 2,365, net +113,803.
    * `lst_dual_overlay`: longs paid 90,049, shorts received 1,345, net +88,704.
  * **Funding is a cost of perp longs, not a source of short profit.** This is
    why hybrid mode is the better way to run these strategies.
* **Fees** (USDT per campaign, `lst_dual_bear` hybrid): 42.5 in total, against
  36.0 for the long part alone and 6.4 for the short leg alone.

## Extra robustness (after the candidates were frozen)

* **Long-leg perturbation on DEV.** Each `breakout_dual` parameter was moved
  ±15/30% inside the strategy (25 variants), in hybrid mode:

  | | mean | min |
  |---|---:|---:|
  | `breakout_dual` alone | 0.0392 | 0.0352 |
  | `lst_dual_bear` | **0.0526** | 0.0488 |
  | `lst_dual_overlay` | 0.0493 | 0.0456 |

  The gain is between +0.012 and +0.015 in every variant, and p05 stays
  between -8.6% and -9.9%. The evaluator's robust number is therefore not
  flattered by freezing the long leg.
* **Cost stress, DEV hybrid:**

  | | `breakout_dual` | `lst_dual_bear` | `lst_dual_overlay` |
  |---|---:|---:|---:|
  | fees doubled (0.2% spot, 0.1% perp) | 0.0366 | 0.0492 | 0.0461 |
  | fees doubled + 10 bp slippage | 0.0332 | 0.0456 | 0.0431 |
* **Continuous DEV run:**

  | | CAGR | Sharpe | max DD |
  |---|---:|---:|---:|
  | `breakout_dual` spot | 66% | 1.56 | 32.5% |
  | `lst_dual_bear` hybrid | 94% | 1.90 | 30.3% |
  | `lst_dual_overlay` hybrid | 86% | 1.80 | 29.0% |
  | BTC buy-and-hold | 31.8% | | 81.4% |

## Honest conclusion

**Rules 1-4 are met in hybrid mode by both candidates. `lst_dual_overlay` also
meets them in perp mode. `lst_dual_bear` in perp mode fails rule 2 (t 1.98).**
If the independent audit (rule 5) finds no look-ahead and does not rate the
overfit risk as high, the rule selects `lst_dual_bear` in hybrid mode, with the
highest robust score (0.0508).

How confident I am, and why not more:

1. **Mostly in-sample.** The design was tuned on TRAIN, which is 6 of the 7
   DEV years, after about 550 configurations. The key feature, the oversold
   floor, was found by reading a TRAIN conditional-return table. The DEV t of
   2.8-3.2 is therefore optimistic. A rough multiple-testing haircut would
   bring it near or below 2.
2. **The one clean year is not supportive.** On VALID 2024 the gain was
   -0.0052 (t -0.9) for `lst_dual_bear` and -0.0008 for the overlay. That is
   not significant, but it is not confirmation either.
3. **The gain comes from crashes.** About 78% of the short-side log gain comes
   from five episodes (Feb 2018, Nov 2018, Nov 2019, Mar 2020, May 2022). The
   rest is still positive. Like most trend following, the edge depends on
   future bear markets having a few fast legs down that are not already
   oversold.
4. **The floor parameter is at a local peak.** Its ±15% variants lose about
   0.005. They still clear the bar in hybrid mode, but this is the part an
   auditor should probe hardest.
5. **What argues for it:**
   * The mechanism is simple and asymmetric for an economic reason (BTC's
     drift and its squeeze dynamics).
   * The short leg adds a similar amount to every long leg tried.
   * The gain survives every long-leg and short-leg perturbation and doubled
     costs.
   * The short contribution is positive in 5 of 7 years.
   * It holds bear-regime exposure at 0.75, so the loss tail is no worse than
     the long-only strategy's (DEV p05 -8.70% against -8.36%).
6. **The 2025-26 holdout is no clean test** for a short design. Per the
   protocol it is already known to be a decline, so it cannot change this
   decision. I did not touch it.

If I had to deploy one today, I would choose **`lst_dual_overlay` in hybrid
mode** as the lower-risk step, even though the rule ranks `lst_dual_bear`
higher. The overlay never changes the long trades. It passes in both modes.
It has the highest paired t (3.22) and the smaller VALID shortfall. Its cost
is a thinner p05 margin: -9.04% against a limit of -9.36%. That choice is the
reviewer's to make; under the pre-declared rule, `lst_dual_bear` wins.

## Engine and harness notes

* `metrics.performance`:
  * `time_in_market` counts only `exposure > 0.01`, so short time is left out.
  * `avg_exposure` is the signed mean, so longs net against shorts.
  * `ls_evaluate.py` reports short time separately, but `continuous.time_in_market`
    in its JSON is long-only time.
* `ls_evaluate.perp_cfg("perp")` still sets `costs.fee_rate = 0.0005`. Since the
  hybrid change, perp mode takes every fee from `costs.perp_fee_rate`, so that
  override does nothing. It is harmless today because both are 0.05%.
* The robust sweep perturbs only `default_params`. A strategy that embeds a
  frozen sub-strategy, as this one does, faces an easier sweep than the
  baseline, whose 6 parameters are all perturbed. I covered this with the
  extended long-leg sweep above.
* Hybrid mode keeps one pooled equity. In practice it needs a spot wallet and
  a futures wallet, and a long-to-short flip within one bar assumes an instant,
  free transfer between them. Perp margin and liquidation are not modelled,
  which is acceptable at |exposure| ≤ 1.
* Constant-fraction rebalancing on shorts: a losing short's |exposure| grows,
  and once it drifts 10% from target the engine buys back into the rally. A
  winning short gets added to. This is consistent behaviour, not a bug, but it
  adds fills during squeezes.
* The residual liquidation in `run_campaigns` uses `params.fee_rate`, the spot
  fee in hybrid mode, even for a residual short. This only matters if a
  campaign is not flat at its end, which normally does not happen.
* The `engine.last_equity` stored by `simulate.replay` is the mark before the
  final flatten fill. The final cash is the number to compare with
  `run_campaigns`, and it matches exactly.

Scratch research scripts (harness, experiments e01-e17, analyses a01-a11 and
final checks v01-v11) live outside the repo at
`/tmp/claude-0/-home-user-trading-app/51d8e21b-b4d0-5c80-97c0-ba14790b0d74/scratchpad/ls-research/ls_trend/`.
Evaluator JSON outputs are in its `final/` subfolder.
