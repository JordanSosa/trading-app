# Long/short decision (committed before the 2025-26 comparison was run)

Protocol: `research/LONG_SHORT_PROTOCOL.md` (adoption rule committed before any
long/short result existed). Evaluator: `scripts/ls_evaluate.py`. Data:
2018-01-01 to 2024-12-31 only. Re-verified after the funding-gap and metrics
fixes in commit b0179b3; all numbers were unchanged.

## Results against the rule (hybrid mode: longs on spot, shorts on perpetuals)

| Candidate | Robust (bar 0.04415) | Paired t vs deployed | Years + | p05 (limit -9.36%) | Audit overfit risk | Passes |
|---|---:|---:|---|---:|---|---|
| **lss_breakdown_vol** | **0.0511** | 2.80 | 7/7 | -9.13% | medium (upper) | **yes** |
| lss_breakdown | 0.0506 | 3.07 | 7/7 | -9.29% | medium (upper) | yes |
| lst_dual_overlay (max_short 0.5) | 0.0484 | 3.22 | 7/7 | -9.04% | medium | yes |
| lst_dual_bear | 0.0506 | 2.79 | 7/7 | -8.70% | **high** | no (rule 5) |
| lsb_bear_chandelier | 0.0467 | 1.73 | 7/7 | -10.21% | medium | no (rules 2, 4) |
| lsb_mirror (naive symmetric shorts) | 0.0392 | 0.16 | 7/7 | -11.61% | - | no |

All-perp mode (longs on perps too) is worse for every candidate. Perp longs
paid 2-22% of equity a year in funding (deployed strategy on perps: 0.0365 vs
0.0401 on spot, t -3.7), while bear-regime shorts received only 0.02-0.25% a
year. The perp-mode passes were rated high overfit risk by the auditors.

## Decision

By the pre-declared rule (highest robust score among passing candidates),
**`lss_breakdown_vol` in hybrid mode with `max_short = 1.0`** replaces the
long-only `breakout_dual`.

How it trades:
* **Longs** are exactly the previously deployed `breakout_dual`: spot, 0/50/100%.
  Longs always have priority.
* **Shorts** run only when the long side is flat and BTC is in a bear regime:
  close below its 125-day average but not more than 35% below it. Entry is a
  fresh 10-day low; the cover is a 2-day high, or the 35% "oversold floor",
  where short squeezes start. Size is volatility-scaled when the short opens
  (0.25-1.0 of equity, median about 0.5) and held until exit.
* **Time short:** about 11% of 2018-2024.

## What the evidence does and does not show (from the audits)

* **In-sample gain:** +0.012 per 30-day campaign (log), robust to block
  length, start phase and 21 of 22 parameter variants (fixed-size sibling).
  It is not a single bear market: t stays at about 2.1 with 2018 and 2022
  both removed.
* **Concentration:** five crash trades (Feb 2018, Nov 2018, Mar 2020,
  May 2021, Jan 2022) make up about 84% of the short contribution. Without
  the 7% of campaigns that overlap them, the gain is +0.002 (t ~1).
* **No out-of-sample support:** 2024 (VALID) shows +0.0008 (t 0.24).
  2025-26 is not a clean test for shorts (its decline is already known).
* **Bull-market cost:** in bull years shorts whipsaw for -2.5% to -6% of
  equity a year. A losing short can also trip the shared -15% campaign stop
  and cause the long leg to miss a rally (seen once, April 2020).
* **Selection breadth:** about 800 + 550 + 630 configurations were searched
  across the three families. Rule 4 is a knife edge for the fixed-size
  sibling. The volatility-sized pick concentrates more in Nov 2018 (+42% of
  equity at near 1.0 size).

Treat the short sleeve as **crash insurance with positive expected value**,
not as a way to profit on both sides every month. Reverting to long-only is
one config change: `[run] instrument = "spot"`, `[strategy] name =
"breakout_dual"`, `[risk] max_short = 0`.
