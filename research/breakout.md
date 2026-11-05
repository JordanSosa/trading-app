# Breakout family: research notes

Module: `src/tradebot/strategies/breakout.py`. It has three registered strategies:
`breakout_donchian`, `breakout_keltner` and `breakout_dual`.

**Recommendation: `breakout_dual` with its default parameters.** It is the 50/50
blend of a Keltner leg and a Donchian leg. On TRAIN its score (0.0410) is 2.4x
buy-and-hold's, and it has the best loss tail of the three: p05 is -8.2%, against
-16.4% for buy-and-hold, and only 2.6% of 30-day runs lose more than 10%. Every
TRAIN year has a non-negative score, including the 2018 and 2022 bear markets. On
VALID (2024, a strong bull year) it matched buy-and-hold's score (0.0400 vs
0.0391) with a smaller tail. `breakout_donchian` is a strong alternative. It was
the best of the three on VALID, but the weakest on TRAIN.

## Protocol

* Data: `data/research/BTCUSDT-1h-research.csv` (2017-08-17 .. 2024-12-31). No
  other market data was used. Nothing after 2024-12-31 was touched.
* TRAIN 2018-01-01 .. 2023-12-31 was used for every design and tuning decision.
  VALID 2024 was evaluated **once**, for the three frozen candidates. No change
  was made after that.
* Score: `research.evaluate_period(..., period=...)`. This is the mean log(1+r)
  of 30-day campaigns started daily. Each campaign starts flat with 10,000 USDT
  and ends flat. TRAIN has 2161 heavily overlapping campaigns, which is about 72
  independent months.
* Costs and risk are the defaults from `config.py`: 0.10% fee and 3 bps slippage
  per side, fills at the next open, and a 0.10 minimum rebalance. The circuit
  breaker goes flat for 48h when equity is 10% below its high-water mark. The
  campaign stop is at -15%. Stops re-arm. All primary numbers use these defaults.
* Checks on all three defaults (and the variants in the sensitivity table):
  * `check_causal(strategy, df, n_checks=30)` returns `[]`.
  * `signals()` on the full 64.5k-bar file takes 0.01-0.03 s.
  * Live-window invariance: I took 40 random 3000-bar windows (the live
    `lookback_bars`). In every one, the last-bar target equals the
    full-history target.

## Final candidates

| strategy | logic | default parameters (n tunable) |
|---|---|---|
| `breakout_donchian` | Enter when close > previous `entry_n`-bar high **and** close > SMA(`trend_n`). Exit when close < previous `exit_n`-bar low. Optional ATR trailing stop (off). | entry_n=144 (6d), exit_n=72 (3d), trend_n=480 (20d), atr_n=168, stop_atr=0 (5) |
| `breakout_keltner` | Enter when close > EMA(`ema_n`) + `entry_k`·ATR(`atr_n`). Exit when close < EMA − `exit_k`·ATR. | ema_n=600 (25d), atr_n=168 (7d), entry_k=3.0, exit_k=0.0 (4) |
| `breakout_dual` | Target is 0.5·Keltner leg + 0.5·Donchian leg. Both legs share EMA(`trend_n`): the Keltner leg uses it as its midline, and the Donchian leg uses it as its entry filter. Exposure takes the values 0, 0.5 or 1. | entry_n=144, exit_n=72, trend_n=600, band_k=3.0, atr_n=168, keltner_weight=0.5 (6) |

Each strategy is a vectorised latch. An entry event sets the state to 1, an exit
event sets it to 0, and the last state is forward-filled. All lookbacks are in 1h
bars. ATR is the 1h Wilder ATR. With k=3 the Keltner entry band sits about 2%
above the EMA.

## TRAIN (2018-2023), default risk overlay

| | buy_hold | donchian | keltner | **dual** |
|---|---|---|---|---|
| score (mean log 30d) | 0.0168 | 0.0361 | **0.0420** | 0.0410 |
| pct_positive | 47.2% | **47.2%** | 44.1% | 45.8% |
| pct flat (exactly 0) | 0% | 6.3% | 9.6% | 5.3% |
| mean | 3.36% | 4.40% | **5.25%** | 5.00% |
| median | -1.17% | 0.00% | 0.00% | 0.00% |
| p05 | -16.4% | -10.3% | -11.5% | **-8.2%** |
| P(loss > 10%) | 32.7% | 5.3% | 6.6% | **2.6%** |
| worst campaign max DD | 35.7% | 21.9% | 25.8% | **21.2%** |
| continuous CAGR | 22.9% | 57.3% | **74.2%** | 68.3% |
| continuous Sharpe | 0.64 | 1.40 | 1.58 | **1.58** |
| continuous max DD | 76.9% | 35.2% | 35.7% | **32.5%** |
| round trips (6y) | 131 | 113 | 104 | 109 (357 fills) |
| time in market | 88% | 35% | 42% | 47% (avg exposure 39%) |
| score 2018 | -0.0683 | **+0.0240** | -0.0108 | +0.0016 |
| score 2019 | 0.0576 | 0.0493 | **0.0781** | 0.0698 |
| score 2020 | **0.1198** | 0.0739 | 0.0945 | 0.0914 |
| score 2021 | -0.0108 | 0.0233 | 0.0323 | **0.0332** |
| score 2022 | -0.0499 | **+0.0037** | +0.0062 | +0.0003 |
| score 2023 | **0.0553** | 0.0430 | 0.0525 | 0.0503 |
| years with score > 0 | 3/6 | **6/6** | 5/6 | **6/6** |

The 2020 year is the only one where buy-and-hold wins clearly. That was a
persistent bull market, and a strategy that is flat about 55% of the time cannot
keep up with it. In return, the strategies avoid most of the 2018 and 2022 bear
markets.

pct_positive stays below 50% because of how the trades pay off. The strategies
win about 30-35% of their trades, but the winners are large (profit factor
2.1-3.0). Some campaigns never trade and return exactly 0, and those do not count
as positive. As a result, the median campaign is 0 and the mean is +4-5%.

## VALID (2024), evaluated once, default risk overlay

| | buy_hold | donchian | keltner | dual |
|---|---|---|---|---|
| score | 0.0391 | **0.0448** | 0.0378 | 0.0400 |
| pct_positive | 55.4% | **61.6%** | 53.9% | 53.9% |
| mean | **5.32%** | 5.24% | 4.73% | 4.82% |
| median | **2.65%** | 1.75% | 1.14% | 0.99% |
| p05 | -15.6% | **-8.7%** | -12.8% | -9.7% |
| P(loss > 10%) | 20.5% | **2.7%** | 8.9% | 4.5% |
| worst campaign max DD | 23.5% | **15.9%** | 23.5% | 19.5% |
| continuous CAGR | **71.3%** | 55.0% | 45.7% | 54.5% |
| continuous Sharpe | 1.35 | **1.50** | 1.21 | 1.46 |
| continuous max DD | 33.1% | **25.8%** | 30.5% | 28.6% |
| round trips | 14 | 22 | 23 | 20 (81 fills) |
| time in market | 92% | 42% | 54% | 60% |

The ranking on VALID differs from TRAIN: Donchian comes first and Keltner last.
The differences are small next to the noise in one year of overlapping
campaigns, which is about 12 independent months. Because of that, I base the
recommendation on all seven years, where the dual has the most consistent
profile. All three candidates give at least buy-and-hold's VALID score with
roughly half its 30-day tail risk, in a year that strongly favoured buy-and-hold.

## Parameter sensitivity (TRAIN, each parameter moved ±25%, others at default)

For `exit_k`, the default is 0, so it was moved ±0.5 ATR instead.

| strategy | parameter | −25% | default | +25% |
|---|---|---|---|---|
| donchian | entry_n 108 / 144 / 180 | 0.0322 | 0.0361 | 0.0342 |
| donchian | exit_n 54 / 72 / 90 | 0.0305 | 0.0361 | 0.0394 |
| donchian | trend_n 360 / 480 / 600 | 0.0285 | 0.0361 | 0.0336 |
| keltner | ema_n 450 / 600 / 750 | 0.0380 | 0.0420 | 0.0384 |
| keltner | atr_n 126 / 168 / 210 | 0.0415 | 0.0420 | 0.0419 |
| keltner | entry_k 2.25 / 3 / 3.75 | 0.0400 | 0.0420 | 0.0412 |
| keltner | exit_k −0.5 / 0 / +0.5 | 0.0412 | 0.0420 | 0.0409 |
| dual | entry_n 108 / 144 / 180 | 0.0404 | 0.0410 | 0.0391 |
| dual | exit_n 54 / 72 / 90 | 0.0356 | 0.0410 | 0.0411 |
| dual | trend_n 450 / 600 / 750 | 0.0374 | 0.0410 | 0.0381 |
| dual | band_k 2.25 / 3 / 3.75 | 0.0404 | 0.0410 | 0.0408 |
| dual | atr_n 126 / 168 / 210 | 0.0403 | 0.0410 | 0.0409 |
| dual | keltner_weight 0.375 / 0.5 / 0.625 | 0.0400 | 0.0410 | 0.0399 |

* **Dual:** the worst ±25% move costs 13% of the score (exit_n −25%), and most
  moves cost less than 3%. In the coarse 144-point grid behind it (entry 96-240,
  exit 48-96, EMA 360-960, band 2-4), the minimum was 0.029 and 58% of points
  were above 0.035.
* **Donchian:** this is the least smooth of the three. Its worst move costs 21%
  (trend_n −25%). Its full 5×5 entry×exit surface at trend_n=480 ranges from
  0.032 to 0.040. The default (144, 72) is a little off the score peak at
  (96-120, 84). I chose it because it has a much lower continuous max DD (35%
  vs 45-49%) and a positive 2022.
* **Keltner:** the plateau is wide. The full 5×3×3 grid over EMA 360-900, k
  2.25-3.75 and exit −0.5..+0.5 stays between 0.030 and 0.042. The EMA=600 row
  stays between 0.040 and 0.042.

## Risk-overlay sensitivity (TRAIN, `dataclasses.replace` on `cfg.risk`)

| overlay | donchian | keltner | dual |
|---|---|---|---|
| **default** (cb 10%/48h, campaign stop 15%) | 0.0361 (p05 -10.3%, worst DD 21.9%) | 0.0420 (-11.5%, 25.8%) | 0.0410 (-8.2%, 21.2%) |
| circuit_breaker_dd 0.15 | 0.0383 | 0.0332 | 0.0370 |
| circuit_breaker_dd 0.20 | 0.0373 | 0.0335 | 0.0363 |
| circuit_breaker_dd 0.99 (off) | 0.0382 (p05 -9.9%, worst DD 29.5%) | 0.0340 (-13.7%, 32.0%) | 0.0371 (-8.7%, 30.1%) |
| circuit_breaker_hours 24 | 0.0387 | 0.0366 | 0.0409 |
| circuit_breaker_hours 96 | 0.0360 | 0.0375 | 0.0377 |
| campaign_stop_loss 0.99 (off) | 0.0363 | 0.0433 | 0.0411 |
| breaker and campaign stop both off | 0.0384 | 0.0373 | 0.0375 |

* For **Keltner and dual the default circuit breaker helps clearly** (+0.008 and
  +0.004). The Keltner exit (close < 25-day EMA) can sit 5-10% below the price,
  so the breaker acts as the missing stop. Unlike an intrabar stop, it lets the
  position re-enter straight after the 48h pause if the latch is still on, with
  no re-arm.
* For **Donchian the breaker costs a little score** (about −0.002), because the
  3-day-low exit already limits losses. Even so, removing it makes the worst
  campaign drawdown worse (21.9% → 29.5%).
* The -15% campaign stop rarely binds and is roughly neutral for all three
  (−0.0001 to −0.0013).

Cost stress on TRAIN: doubling the fee to 0.2% per side lowers the scores to
0.032, 0.038 and 0.038 (donchian, keltner, dual). Doubling the fee and also
raising slippage to 10 bps gives 0.030, 0.035 and 0.034. Buy-and-hold drops to
0.013 and 0.010 under the same stresses. The edge does not depend on low fees.

## What was tried, and what failed

All results are TRAIN scores under the default overlay unless stated otherwise.

1. **Plain Donchian (no filter), entry 48-1200 bars × exit 24-720 bars.** The
   best region was entry 120-240 with exit 60-120, scoring 0.027-0.031. The
   best single point was 168/72 at 0.0312.
   * Short channels (48-96) traded 200-340 round trips and lost to fees, scoring
     0.004-0.021.
   * Long channels (720-1200) sat idle for many campaigns, scoring 0.012-0.028
     and up to 48% flat campaigns.
   * 2018 and 2022 were negative for most settings.
2. **Trend filter.** An SMA(480) entry filter raised 168/72 from 0.0312 to
   0.0338 and lifted 2022 from about 0 to +0.009.
   * Slower filters hurt: SMA 1200 gave 0.0291 and SMA 2400 gave 0.0262. They
     enter too late and cut participation.
   * Using the filter as an extra exit also hurt slightly (0.0301).
   * The filter type barely mattered. For 120/72: SMA 0.0357, EMA 0.0352,
     Donchian midline 0.0360. TSMOM(720) gave 0.0365, but 18% of its campaigns
     were flat.
   * Adding an SMA-slope condition cut time in market and fell to about 0.027.
3. **ATR trailing stops via `stop_dist`.**
   * Short-ATR stops (ATR 24) hurt badly on 168/72/480. Against 0.0338 without a
     stop, the scores were 0.0339 at 13×, 0.0310 at 9×, 0.0267 at 6× and 0.0133
     at 4×.
   * With ATR(168), an 8× stop matches the no-stop score (0.0356 vs 0.0361) and
     improves p05 (-8.0%) and continuous max DD (26%). However, it sits on a
     cliff: 6× gives 0.0239, and 6.5× gave 0.017-0.019 on the neighbouring
     120/72 and 168/72 channels.
   * On Keltner, a 10× stop dropped 0.038 to 0.026.
   * Why stops fail here: after a stop, the engine's re-arm rule blocks
     re-entry until the strategy target returns to 0. That means waiting for
     the channel or EMA exit plus a fresh breakout, so the strategy misses the
     rest of the trend. Stops are off by default; `breakout_donchian` keeps
     `stop_atr` as an opt-in.
4. **Volatility-targeted sizing.** Size was min(1, target_vol / realised vol),
   frozen at entry, with targets 0.5-0.9 and vol windows of 168 or 720. It
   scored lower in every case: 0.030-0.034 against 0.034-0.036 at full size. The
   only gain was a few points of max DD. A long-only BTC trend leg has positive
   drift, so scaling it down mostly lowers growth. Rejected.
5. **Multi-scale Donchian ensemble.** I averaged three latches with entry
   lengths N/s, N and N·s (s = 1.5-3). It scored 0.021-0.036 and never beat the
   best single scale. Rejected.
6. **Keltner (EMA ± k·ATR) breakout.** This was the strongest single family.
   * Plateau: 0.030-0.042, mostly above 0.035, for EMA 360-960, k 2-5 and
     exit −1..+1 ATR.
   * Short EMAs (120-240) with low k churned, scoring as low as -0.0007.
   * The ATR length (72-336) barely matters.
   * Adding an SMA(480) filter changed nothing, because the EMA already
     filters.
   * Its weak spot is 2018 (-0.011).
7. **Dual blend (Keltner + Donchian).** The two legs' false breakouts only partly
   overlap. Combining them kept about 98% of Keltner's score while improving p05
   (-11.5% → -8.2%), P(loss > 10%) (6.6% → 2.6%), worst campaign DD and
   continuous max DD. It also made 2018 and 2022 non-negative.
   * Linking the Donchian filter to the Keltner EMA saved a parameter (6 in
     total) at no cost.
   * Keltner weights of 0.375-0.625 are all within 3% of each other.

## Caveats

* The edge comes from a few large trend months. The median 30-day campaign
  returns 0, and the strategies are flat 50-65% of the time. A single 30-day
  forward run makes money only about 45-60% of the time. What these strategies
  mainly improve is the size of the losses, not how often a run is profitable.
* 2024 (VALID) was a bull year. A prolonged choppy range, like mid-2019 or 2021,
  is where breakout systems bleed through repeated small whipsaw losses.
* All numbers are on hourly OHLC with next-open fills. There is no exchange
  outage modelling beyond the gaps already in the data.

Throwaway research scripts live outside the repo, in the session scratchpad:
`/tmp/claude-0/-home-user-trading-app/51d8e21b-b4d0-5c80-97c0-ba14790b0d74/scratchpad/research/breakout/`
(`harness.py`, `strats.py` and the experiment scripts `e1.py`-`e16.py`, `sens.py`, `costs.py`, `final.py`).
