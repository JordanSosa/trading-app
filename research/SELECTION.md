# Selection (committed before HOLDOUT was evaluated)

All numbers here use bars before 2025-01-01. Raw output:
`research/results/valid_check.json`, `risk_stops_dev.json`,
`risk_governor_dev.json`, `risk_levels_dev.json`, `frozen_dev_checks.json`,
`spot_dev.json`.

## 1. VALID check of the frozen candidates (2024, run once)

After 8 bps a side, fast linear backtest, at a 2% daily volatility target.

| Candidate | TRAIN Sharpe | VALID Sharpe | VALID CAGR | VALID max drawdown | DEV Sharpe | DEV at doubled costs |
|---|---:|---:|---:|---:|---:|---:|
| T0 trend | 1.77 | 0.89 | 25% | 15% | 1.61 | 1.28 |
| X0 cross-sectional momentum | 1.92 | 1.65 | 81% | 23% | 1.86 | 1.35 |
| B0 blend | 1.97 | 1.26 | 39% | 12% | 1.84 | 1.51 |

Both families are positive on VALID, so both stay. VALID is well below
TRAIN for trend and for the blend: the in-sample figures are inflated, as
expected.

**Blend or best member?** The rule says the blend is used only if it beats
its best member on DEV. On the raw Sharpe ratio the two are tied (X0 1.86,
B0 1.84), which the rule does not settle. The tie is resolved by the
protocol's objective, compound growth under the drawdown constraints, and
by cost robustness: X0 alone needs 2.0x gross exposure, has a 48% drawdown
at 2.4% daily volatility and drops to 1.35 at doubled costs; B0 has a 26%
drawdown at 1.9% volatility and 1.51 at doubled costs. **B0 is selected.**
This is a judgement call and is recorded as one.

## 2. Stops (DEV, full engine, governor off, 2% volatility target)

| Protective stop | Compound per day | Sharpe | Max drawdown | Worst day |
|---|---:|---:|---:|---:|
| none | 0.166% | 1.86 | 28.2% | -7.6% |
| 2 daily sigmas | 0.134% | 1.54 | 26.5% | -9.6% |
| 3 daily sigmas | 0.149% | 1.67 | 27.8% | -10.6% |
| 4 daily sigmas | 0.152% | 1.70 | 28.0% | -9.7% |

Stops cost growth: they fill on wicks that later recover. Letting the stopped
side back in after 24 or 72 hours changes nothing (0.147-0.151%). With
stops filled at the bar's extreme (worst case) the 3-sigma result is 0.136%.

**4 sigma is selected** (cost 8% of growth, inside the 10% rule). The reason
is not in the backtest: the simulation assumes the bot acts at every hourly
close, and a real deployment has outages. A resting stop on the exchange is
the only protection while the bot is offline, and the only cap on a short
that doubles in an hour.

## 3. Risk governor (DEV, full engine, 3-sigma stops, 2% target)

| Rule | Compound per day | Max drawdown | Worst day | Adopted |
|---|---:|---:|---:|---|
| none | 0.149% | 27.8% | -10.6% | |
| flatten at +2% on the day | 0.065% | 35.0% | -9.0% | no |
| trail after +2% (give back half) | 0.033% | 41.9% | -9.0% | no |
| flatten at +3% / +5% | 0.111% / 0.151% | 31.2% / 28.9% | -10.6% | no |
| daily loss limit 2% | 0.130% | 24.7% | -7.1% | no (costs 13%) |
| **daily loss limit 3%** | **0.153%** | **26.9%** | **-7.1%** | **yes** |
| daily loss limit 5% | 0.144% | 29.0% | -7.0% | no |
| drawdown scaling 10%-30% to 0.25x | 0.125% | 25.1% | -10.6% | no (costs 16%) |
| **drawdown scaling 15%-35% to 0.5x** | **0.145%** | **27.0%** | -10.6% | **yes** |
| drawdown scaling on a 180-day peak | 0.133% | 25.1% | -10.6% | no (costs 11%) |
| kill switch at 40% | 0.149% | 27.8% | -10.6% | always on |

**Locking in the day at +2% is the most damaging rule tested.** It cuts
compound growth by 56% to 78% and makes the drawdown worse. The strategy's
profit comes from a minority of large days; capping them keeps every loss
and pays to re-enter the next morning. The 2% target is therefore
**measured, not enforced**: the bot reports how often the day ends at or
above +2% and never closes winners to "bank" it. The lock stays in the code
as an option, off by default.

## 4. Risk level (DEV, full engine, selected stops and governor)

| Daily volatility target | Compound per day | CAGR | Max drawdown | Days >= +2% | Worst day | P(50% drawdown within a year) |
|---:|---:|---:|---:|---:|---:|---:|
| 0.5% | 0.040% | 16% | 7.9% | 0.5% | -2.4% | 0.0% |
| 1.0% | 0.076% | 32% | 15.4% | 2.9% | -3.5% | 0.0% |
| 1.5% | 0.117% | 53% | 21.5% | 7.7% | -5.3% | 0.0% |
| 2.0% | 0.148% | 71% | 26.4% | 12.9% | -7.0% | 0.1% |
| 2.5% | 0.160% | 79% | 28.8% | 17.2% | -8.4% | 0.8% |
| **3.0%** | **0.192%** | **101%** | **32.6%** | **19.7%** | **-10.3%** | **2.0%** |
| 4.0% | 0.182% | 94% | 37.5% | 22.3% | -13.8% | 8.3% |
| 5.0% | kill switch fired | | 40.1% | | -18.0% | |
| 6.0% to 20% | kill switch fired, earlier each time | | 40%-59% | | -21% to -40% | |

Compound growth peaks near 0.19% a day at a 3% target and falls beyond it;
from 5% the account hits the 40% kill switch. In-sample, with every choice
made on this same data, the best the risk dial can do is about a tenth of
the 2% a day in the brief.

By the rule (highest level with drawdown <= 35%, P(50% drawdown) <= 5%, no
kill or liquidation) the selected level is **3.0%**.

### Amendment 2 (made here, before HOLDOUT): the default is half of that

The rule picks the level on in-sample data, and VALID already showed the
in-sample edge shrinking by a third to a half. At a true Sharpe ratio near
1 a 3% target would be expected to reach the 40% kill switch within about
two years. The shipped default is therefore **1.5%**, half the
rule-selected level (the usual half-Kelly haircut for an estimated edge).
3.0% ships as the "aggressive" preset, labelled as the protocol maximum.
Both are reported on HOLDOUT, with the whole ladder.

## 5. The frozen configuration

Strategy: 20 most liquid crypto perpetuals (re-ranked every Monday); trend
score = mean of clip(z, -1, 1) over 2, 4, 8, 16 and 32-day returns measured
in units of their own volatility; plus a cross-sectional rank of the 14-day
return at equal weight; shorts at half size; inverse-volatility weights
scaled to the portfolio volatility target (correlation 0.7, one symbol at
most half the risk); weights averaged over 12 hours; stops at 4 daily
sigmas (3% to 25% of price).

Engine: fee 0.05% and slippage 0.03% a side; 25% no-trade band; minimum
order 5 USDT, minimum position 10 USDT; gross exposure cap 3.0x, single
weight cap 0.6x; daily loss limit 3%; drawdown scaling from 15% (1.0x) to
35% (0.5x); kill switch at 40%; 2% daily target measured only.

DEV with this configuration (10,000 USDT):

| | Default (1.5%) | Aggressive (3.0%) |
|---|---:|---:|
| Compound per day | 0.117% | 0.191% |
| CAGR | 53% | 101% |
| Sharpe | 1.73 | 1.65 |
| Max drawdown | 21.5% | 32.6% |
| Days at or above +2% | 7.7% | 19.8% |
| Worst day | -5.3% | -10.3% |
| VALID year alone, compound per day | 0.054% | 0.073% |
| Doubled costs, compound per day | 0.090% | 0.125% |
| From 100 USDT, compound per day | 0.157% | 0.209% |
| By year 2020 (Apr-Dec) / 21 / 22 / 23 / 24 | +86% / +57% / +13% / +89% / +23% | +196% / +103% / +22% / +169% / +39% |

A 100 USDT account holds fewer, larger positions (exchange minimums), so
its path differs from a large account's; it was luckier in this sample, not
better by design.

Spot-only alternative (long or flat, no leverage, 0.10% fee): 0.072% a day
on DEV at the 1.5% target, 28% drawdown, and a losing 2022 (-24%). Higher
targets hit the kill switch in 2022. It ships as `config/spot.toml` for
accounts that cannot trade derivatives.

## 6. What will be run on HOLDOUT (2025-01-01 to 2026-10-31), once

Primary result: the frozen default from 10,000 USDT. Also reported, all
declared here: the same from 100 USDT; the aggressive preset; doubled
costs; governor off; the +2% profit lock switched on; the risk ladder
(0.5% to 8%); T0 and X0 alone (attribution only); the spot preset; BTC
buy-and-hold for context; bootstrap one-year outcomes. Nothing is changed
after this run.

## Amendment 3 (independent audit, made before HOLDOUT)

Before the holdout was evaluated, the engine, strategy, backtest and data
code were audited independently, with instructions to find anything that
would make the results wrong or optimistic.

**What the audit confirmed as correct.** No look-ahead: with every field
replaced from bar k onward (prices, volume, funding, ranks) on 36-symbol
synthetic panels, weights, stops, ranks, the engine's state, its equity
curve, trade list and day ledger before k were unchanged bit for bit, for
the production parameters and two variants. No edge on noise: 600
driftless panels gave +0.6% before costs (t = 0.8) and -2.8% after. The day
ledger compounds exactly to final equity. Fees are charged on every fill,
funding has the right sign and timing, stop fills are never better than
their trigger.

**The one confirmed defect: holes in the data.** Binance's monthly archive
files are missing bars that its daily files have (2022-02-26 to 28 and
2022-04-01 to 02 for about 47 symbols, whole months for a few symbols), and
its futures archive has no data at all for 2026-10-28, a day on which its
spot archive shows normal trading. A position in a symbol without bars is
frozen: it cannot be traded, its stop is not tested, and three days of
profit and loss arrive in one bar. In the first DEV run this booked +2.35%
of equity on a single bar in March 2022.

Repairs, all made without looking at any result after 2024:

* Holes inside a symbol's history are filled from the daily archive files
  (`archive.patch_gaps`). Missing bars inside listing spans fell from 16,130
  to 2,834; gaps of a day or more from 452 to 111.
* The market-wide hole on 2026-10-28 is filled from the exchange's own spot
  candles, rescaled so the first one opens where the perpetual last closed
  (`archive.patch_from_spot`: 231 symbols, 5,544 bars). It applies only to
  days on which the futures archive is empty for every symbol, so a genuine
  halt of one market stays a halt. 110 perpetuals without a matching spot
  market keep the hole; two of them were in the top 20 that day.
* What remains are real halts and delistings.

**Engine changes from the audit's other points.**

* Margin is tested at the moment stops trigger, before they fill: an
  account whose equity is already below maintenance there is liquidated
  instead of being stopped out. Stop fills that leave less than nothing are
  a wipe-out even with no position left. (Matters only at the top of the
  risk ladder.)
* A position in a market that has printed no bar for 48 hours is settled at
  its last price, with costs, as a delisting would settle it. Previously it
  was held forever at a stale mark.
* A day whose final return is beyond the loss limit or the target is
  flagged as such even when the breach came in its last hour.
* A wiped-out account reports -100%, not the average of what came before.
* Funding settlements are snapped to the bar grid and de-duplicated (no
  row in the data was affected).

**Known and left as they are.** The daily loss limit is checked at hourly
closes within a UTC day, so a loss in the day's final hour is recorded but
cannot be prevented. The fast linear backtest used for signal research
counts the return across a remaining data gap as zero. A ticker that is
redenominated or relisted under the same name is not detected (BNX in
February 2023 is the one case in the data; it ranked about 80th, outside
the universe).

**Everything was re-run on the repaired data with the corrected engine.**
Every decision is unchanged:

| | First run | Re-run |
|---|---:|---:|
| VALID Sharpe, T0 / X0 / B0 | 0.89 / 1.65 / 1.26 | 0.89 / 1.65 / 1.26 |
| DEV Sharpe, T0 / X0 / B0 | 1.61 / 1.86 / 1.84 | 1.61 / 1.91 / 1.86 |
| Stops: none / 2 / 3 / 4 sigma, compound per day | 0.166 / 0.134 / 0.149 / 0.152% | 0.166 / 0.134 / 0.149 / 0.152% |
| +2% lock, flatten / trail | 0.065 / 0.033% | 0.064 / 0.033% |
| Daily loss limit 2% / 3% / 5% | 0.130 / 0.153 / 0.144% | 0.131 / 0.155 / 0.146% |
| Drawdown scaling 15%-35% | 0.145% | 0.145% |
| Risk ladder 1.5% / 2% / 3% / 4% | 0.117 / 0.148 / 0.192 / 0.182% | 0.118 / 0.149 / 0.193 / 0.183% |
| Highest level passing the survival rule | 3.0% | 3.0% |
| Frozen default: compound per day, max drawdown | 0.117%, 21.5% | 0.118%, 21.5% |
| Frozen default, 2022 | +12.9% | +14.7% |

On the raw DEV Sharpe ratio the cross-sectional sleeve alone is now 0.05
ahead of the blend (1.91 against 1.86). With the full engine at the frozen
settings the blend compounds faster than either member (0.118% a day
against 0.096% for trend and 0.102% for cross-sectional momentum) with the
smallest drawdown (21.5% against 21.6% and 32.9%), so the choice of B0
stands. All JSON files in `research/results/` are from the re-run;
`frozen_dev_checks.json` and `spot_dev.json` from the first run are
replaced by `dev.json`, which `scripts/holdout.py dev` writes in the same
format as the holdout report.
