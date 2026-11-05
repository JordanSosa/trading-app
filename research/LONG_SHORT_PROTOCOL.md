# Long/short research protocol (written before any long/short result existed)

Question: can short selling (via BTC/USDT perpetual futures) or a long/short
design beat the deployed long-only `breakout_dual`?

## Data and cost model

* Prices: Binance spot BTCUSDT 1h candles (perp-spot basis ignored; it is
  typically a few bps).
* Funding: Binance USD-M BTCUSDT 8-hourly funding from 2020-01 (archive).
  Before 2020 Binance perps did not exist; funding is taken as zero.
* Perp fees: 0.05% per side (Binance USD-M taker, regular tier), plus 3 bps
  slippage. Spot baseline: 0.10% + 3 bps.
* Same engine, risk overlay (10%/48h circuit breaker, -15% campaign stop),
  no leverage (|exposure| <= 1).
* Research data stops at 2024-12-31. The 2025-26 holdout is **not** a clean
  test for short strategies: it is already known to be a 36% decline, so any
  short design can look good on it through hindsight. It is reported, but
  cannot change the decision.

## Adoption rule

A long/short candidate replaces the deployed long-only `breakout_dual` (spot)
only if **all** of the following hold on DEV (2018-01-01 to 2024-12-31),
measured by `scripts/ls_evaluate.py`:

1. Robust score (mean DEV campaign score over default params, each numeric
   param at -30/-15/+15/+30%, and the circuit breaker disabled) is at least
   **0.005 higher** than `breakout_dual` spot's robust score (0.03915).
2. Paired 30-day-campaign improvement over `breakout_dual` spot has a
   moving-block-bootstrap **t >= 2.0**.
3. Positive campaign score in **at least 5 of the 7** DEV years.
4. DEV 5th-percentile 30-day return no more than 1 percentage point worse
   than `breakout_dual` spot (-8.36%).
5. Independent audit: no look-ahead (verdict not `fail`), overfit risk not
   `high`.

If several candidates pass, the highest robust score wins. If none pass, the
deployment stays long-only, and the long/short code remains available as a
configurable option.

## Amendment 1 (added before any long/short result was available)

The engine gained a `hybrid` instrument mode: long exposure is held on spot
(0.10% fees, no funding) and short exposure on perpetuals (0.05% fees,
funding received or paid). This is how a long/short bot would actually be run,
with a spot account for longs and a futures account for shorts, so it avoids
the funding drag that all-perp longs pay. Every final candidate is evaluated
in both modes (`scripts/ls_evaluate.py --mode perp|hybrid`), and the adoption
rule may be met in either mode. Reported numbers always state the mode.
