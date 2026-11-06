# Results: selected strategy `breakout_dual`

The selection was committed before the holdout was evaluated (`research/SELECTION.md`,
commit "Record strategy selection (breakout_dual) before evaluating the holdout").
The holdout below was evaluated once, with `scripts/holdout.py`. Raw numbers
are in `holdout.json`.

## 30-day campaigns

Each campaign starts flat with 10,000 USDT, trades for 30 days and ends flat,
exactly like the deployed run. One campaign starts every day. All figures are
after 0.10% fees and 3 bps slippage per side.

| | Research 2018-2024 (DEV) | **Holdout 2025-01-01 to 2026-11-05 (unseen)** |
|---|---:|---:|
| Campaigns | 2,527 | 644 |
| Mean 30-day return | +4.89% | **+0.63%** |
| Median 30-day return | 0.00% | **-0.54%** |
| Ended in profit | 46.7% | **38.8%** |
| Ended flat (never traded) | 4.6% | 5.7% |
| Ended at a loss | 48.8% | **55.4%** |
| Lost more than 10% | 2.8% | **0.2%** |
| 5th percentile (a bad month) | -8.4% | **-6.7%** |
| Worst campaign | -15.9% | **-12.0%** |
| 95th percentile (a good month) | +34.8% | +17.5% |
| Score, mean log(1+r) | 0.0401 | **0.0044** |
| Raw buy & hold: mean 30-day return | +4.61% | -0.21% |
| Raw buy & hold: score | n/a | -0.0095 |

## Continuous backtest (one account, no 30-day resets)

| | Research 2018-2024 | Holdout 2025-01 to 2026-11 |
|---|---:|---:|
| Strategy | CAGR 66%, Sharpe 1.56, max DD 32.5% | **+12.5% total**, max DD 22.4%, 133 fills |
| Buy & hold BTC | | -13.9% (93,576 to 80,543; peak 126,200 in Oct 2025) |
| Time in market | 49% | 48% |

## Other finalists on the holdout (context only, not used for selection)

| Strategy | Holdout score | Mean 30d | Continuous |
|---|---:|---:|---:|
| **breakout_dual** (selected) | +0.0044 | +0.63% | +12.5% |
| breakout_keltner | +0.0043 | +0.67% | +15.6% |
| trend_sma_band | -0.0005 | +0.17% | +2.7% |
| regime_volswitch (co-leader on DEV) | -0.0092 | -0.64% | -10.1% |
| buy_hold with the same risk overlay | -0.0192 | -1.28% | -42.0% |

## Most recent 30 days (live code path replay)

`tradebot replay` over 2026-10-05 to 2026-11-04 ran the actual forward tick
every 15 minutes: **-5.77%**, against -11.14% for buy & hold. It made 9 fills,
3 losing round trips and paid 48.51 USDT in fees.

## Interpretation

* **The edge survived out of sample but shrank sharply.** It fell from +4.9%
  to +0.6% mean per 30 days. 2018-2024 contained several huge trends. 2025-26
  was a topping market followed by a 36% decline, which is hostile to
  long-only trend following.
* **What it does reliably is limit losses.** It is in cash about half the
  time, and the risk overlay caps the downside. Holdout 30-day losses larger
  than 10% occurred in 0.2% of campaigns.
* **Any single 30-day run is close to a coin flip.** In the holdout, slightly
  more runs lost a little than gained. Profits come from occasionally catching
  a strong trend. "Make money over the next 30 days" is therefore a hope, not
  an expectation that can be promised.
* **Current state (2026-11-05):** BTC is 36% below its high and 5.8% down over
  30 days. The strategy is flat, and has been for 12 days. The paper run
  starts in USDT and only buys on a breakout above the 6-day high and the
  600-hour EMA (or EMA + 3 ATR). If no trend forms, the expected 30-day result
  is close to 0%.
