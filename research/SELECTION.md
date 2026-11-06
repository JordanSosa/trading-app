# Strategy selection (decided before the holdout was evaluated)

Selection used only 2018-2024 data (TRAIN + VALID = DEV). The 2025-01-01 to
present holdout had not been evaluated by anyone when this file was committed.

## Candidates and audits

Four research agents designed strategy families on TRAIN (2018-2023) and
validated each candidate once on VALID (2024). An independent auditor per
family then re-read the code for look-ahead bias, re-ran causality and window
checks, reproduced every number, and perturbed every parameter.

| Family | Audit verdict | Overfit risk | Notes |
|---|---|---|---|
| breakout | pass with concerns | medium | Donchian warm-up too short (fixed: latch and EMA warm-ups enlarged; all pass the warmup+1 window check on real data) |
| trend | pass with concerns | medium | regime filter adds nothing; edge concentrated in a few trends |
| regime | pass with concerns | medium | edge sits on a narrow circuit-breaker setting; regime layer not supported on VALID |
| meanrev | pass with concerns | **high** | parameter cliffs; TRAIN does not predict VALID -> **excluded** |

No look-ahead was found anywhere, and every reported number reproduced.

## Robustness-weighted ranking (`scripts/robust_select.py`)

DEV campaign score (mean log(1+r) of 30-day campaigns started daily), averaged
over the default parameters, each numeric parameter at -30/-15/+15/+30%, and the
circuit breaker disabled.

| Strategy | Robust mean | Worst variant | Default | No circuit breaker |
|---|---:|---:|---:|---:|
| regime_volswitch | 0.03919 | 0.0328 | 0.0419 | 0.0353 |
| **breakout_dual** | **0.03915** | **0.0351** | 0.0401 | **0.0381** |
| breakout_keltner | 0.03910 | 0.0345 | 0.0407 | 0.0359 |
| regime_confirm | 0.03839 | 0.0330 | 0.0407 | 0.0350 |
| trend_sma_band | 0.03640 | 0.0328 | 0.0379 | 0.0365 |
| trend_sma_band_regime | 0.03571 | 0.0325 | 0.0369 | 0.0347 |
| breakout_donchian | 0.03559 | 0.0295 | 0.0365 | 0.0394 |
| trend_strength | 0.03252 | 0.0282 | 0.0324 | 0.0338 |
| buy_hold (same risk overlay) | 0.0199 | | | |

Blends of trend and breakout were also tested on DEV (0.0355-0.0368). None
beat the best single strategy: the signals are highly correlated, and partial
exposures add rebalancing cost.

## Decision: `breakout_dual` (default parameters)

`regime_volswitch` and `breakout_dual` differ by 0.00004 in robust mean, about
1/300 of the score's standard error (~0.012), so the primary metric is a tie.
The tie was broken on the criteria that matter for an unattended 30-day run:

* Higher worst-case variant (0.0351 vs 0.0328), so less parameter fragility.
* Much less dependence on the circuit-breaker setting (0.0381 vs 0.0353 with
  the breaker off). The regime auditor flagged this coupling as a knife edge.
* Non-negative score in all 6 TRAIN years, including the 2018 and 2022 bear
  markets (regime_volswitch: 4 of 6).
* Best loss tail of the finalists (TRAIN p05 -8.2%; buy-and-hold -16.4%).

Risk overlay: defaults are kept (10%/48h circuit breaker, -15% campaign stop).
The overlay is roughly neutral for breakout_dual on DEV (0.0401 vs 0.0381),
and it bounds the worst case of a single unattended 30-day run.

## Honest expectations from 2018-2024

* The edge over buy-and-hold is modest: about 1.5-2.5 standard errors
  depending on the test, before any correction for the breadth of the search.
* A single 30-day run is roughly a coin flip to finish positive. The strategy
  is flat (in USDT) 50-65% of the time, so many campaigns end at exactly 0.
  The positive expectation comes from catching a few large trend months while
  keeping losses small.
