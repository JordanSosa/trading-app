# Results

The configuration was frozen and committed (`SELECTION.md`, including the
audit in Amendment 3) before the holdout was evaluated. The holdout was run
once, with `scripts/holdout.py`; raw numbers are in `results/holdout.json`
and, in the same format for the development period, `results/dev.json`.
`scripts/make_tables.py` prints every table below from those two files.

All figures are after a 0.05% fee and 0.03% slippage per side and actual
funding, with the full engine (stops, liquidation model, risk governor).
"Compound per day" is the constant daily return that compounds to the same
result: the number to hold against a 2% a day target.

## 1. Headline

| Default configuration (1.5% daily volatility target) | Development 2020-04 to 2024-12 (in-sample) | **Holdout 2025-01 to 2026-10 (out-of-sample)** |
|---|---:|---:|
| **From 10,000 USDT** | | |
| Compound return per day | +0.118% | **+0.050%** |
| Total / annualised | +676% / +54% | **+39.8% / +20.1%** |
| Sharpe ratio | 1.74 | **0.75** |
| Maximum drawdown | 21.5% | **23.2%** |
| Days at or above +2% | 7.8% | **7.6%** |
| Days up / down | 52% / 48% | 46% / 54% |
| Best / worst day | +8.9% / -5.3% | +12.4% / -5.3% |
| **From 1,000 USDT** | | |
| Compound per day, max drawdown | +0.120%, 20.9% | +0.035%, 25.4% |
| Total | | +26.4% |
| **From 100 USDT** | | |
| Compound per day, max drawdown | +0.157%, 24.3% | +0.009%, 39.8% |
| Total | +1,415% | +5.9% |
| **BTC buy and hold, for scale** | +0.155% a day, 76.7% drawdown | -0.028% a day (-17.1%), 53.0% drawdown |

Against the brief:

* **The target is 2% a day. Out of sample the strategy made 0.05% a day**:
  one fortieth of the target, about +20% a year, in a period when Bitcoin
  lost 17%.
* **A day at or above +2% happens about one day in thirteen**, in both
  periods. It is what a good day looks like at this risk level, not the
  normal day.
* **The edge shrank by more than half out of sample** (0.118% to 0.050% a
  day; Sharpe 1.74 to 0.75), as in-sample results always do. 2025 was a
  losing year (-5.9%); the first ten months of 2026 made +48.7%.
* **It did not blow up**: worst day -5.3%, maximum drawdown 23.2%, no
  liquidation, no kill switch, the same order of drawdown as in
  development.
* **A 100 USDT account is a different animal.** Exchange minimum order
  sizes let it hold three to six positions instead of about twenty. It beat
  the large account in development and badly lagged it in the holdout
  (+5.9% against +39.8%, with a 39.8% drawdown that stopped just short of
  the 40% kill switch). 1,000 USDT tracks the tested portfolio much more
  closely. See section 5.

## 2. Turning the risk up does not reach the target

Same strategy, same engine, only the volatility target changes. 10,000 USDT.

| Daily volatility target | DEV compound/day | DEV max drawdown | HOLDOUT compound/day | HOLDOUT total | HOLDOUT max drawdown | HOLDOUT days >= +2% |
|---:|---:|---:|---:|---:|---:|---:|
| 0.5% | +0.040% | 7.9% | +0.022% | +15.9% | 8.1% | 0.9% |
| 1.0% | +0.078% | 15.3% | +0.040% | +31.0% | 15.1% | 3.7% |
| **1.5% (default)** | +0.118% | 21.5% | **+0.050%** | **+39.8%** | 23.2% | 7.6% |
| 2.0% | +0.149% | 26.4% | +0.030% | +22.3% | 28.8% | 9.9% |
| 3.0% (aggressive) | +0.193% | 32.6% | +0.002% | +1.3% | 38.7% | 13.9% |
| 4.0% | +0.183% | 37.5% | -0.069% (kill switch) | -37.2% | 40.1% | 3.6% |
| 6.0% | +0.106% (kill switch) | 40.3% | -0.048% (kill switch) | -27.7% | 40.4% | 3.3% |
| 8.0% | +0.015% (kill switch) | 40.1% | -0.051% (kill switch) | -29.1% | 40.4% | 2.4% |

* In development, compound growth peaked near **0.19% a day** at a 3%
  target and fell beyond it. That is the most the risk dial could do with
  every choice made on that same data: a tenth of the target.
* Out of sample the peak was at the default, **0.05% a day**. Twice the
  risk made less money (+1% to +11% in total, depending on the exposure
  caps) with a 38.7% drawdown; from a 4% target the kill switch fired and
  the account ended 28% to 37% down.
* More risk buys more +2% days (13.9% of days at a 3% target) and a lower
  final balance. The frequency of good days is not the same thing as
  growth.

This is why the shipped default is half the level the in-sample rule
selected (Amendment 2 in `SELECTION.md`), and why the aggressive preset is
labelled as it is: in the holdout it returned +10.6% from 10,000 USDT with a
38.7% drawdown, and from 100 USDT it hit the kill switch in March 2025.

## 3. What each rule did

10,000 USDT, default risk.

| Variant | DEV compound/day | DEV max drawdown | HOLDOUT compound/day | HOLDOUT total | HOLDOUT max drawdown |
|---|---:|---:|---:|---:|---:|
| **Default** | +0.118% | 21.5% | +0.050% | +39.8% | 23.2% |
| Doubled fees and slippage | +0.091% | 23.4% | +0.012% | +8.7% | 27.5% |
| Stops fill at the bar's extreme (worst case) | +0.110% | 22.3% | +0.039% | +29.7% | 22.9% |
| Risk governor off (no loss limit, scaling or kill switch) | +0.115% | 22.3% | +0.051% | +40.4% | 22.9% |
| **+2% profit lock: flatten at the target** | +0.090% | 21.4% | **-0.008%** | **-5.5%** | 27.3% |
| +2% profit lock: trail after the target | +0.060% | 22.0% | +0.053% | +42.7% | 20.4% |
| Trend only (T0) | +0.096% | 21.6% | +0.042% | +32.6% | 19.7% |
| Cross-sectional momentum only (X0) | +0.102% | 32.9% | +0.115% | +115.9% | 34.3% |
| Spot preset (long or flat, no leverage, 0.10% fee) | +0.071% | 27.9% | +0.012% | +8.5% | 22.4% |

* **Costs are the margin of survival.** At doubled costs the out-of-sample
  result falls from +39.8% to +8.7%. The model assumes taker orders at 5 bps
  plus 3 bps of slippage on the 20 most liquid contracts; an account that
  pays more than that has little edge left.
* **Banking +2% a day turned a gain into a loss** out of sample (-5.5%
  against +39.8%) and cost 24% of growth in development (57% at a 2% risk
  target). Trailing the target instead was harmless out of sample but
  halved growth in development. Neither is on by default.
* **The governor cost nothing and changed little at this risk level**: the
  daily loss limit acted on 13 days, the drawdown scaling barely engaged,
  the kill switch never fired. Its job is at higher risk and on worse
  paths, where the ladder above shows it ending runs at -28% to -37%
  instead of letting them continue.
* **The cross-sectional sleeve carried the holdout.** Alone it made +116%
  (with a 34% drawdown and twice the gross exposure); trend alone made
  +33%. This comparison was not used for anything, and choosing the sleeve
  that won the holdout would be exactly the mistake a holdout exists to
  prevent. It does mean the blend's result leaned on one family.
* **Without shorting there is not much left**: the spot preset made +8.5%
  in 22 months.

## 4. The holdout month by month (default, 10,000 USDT)

| 2025 | Return | 2026 | Return |
|---|---:|---|---:|
| January | +1.4% | January | +17.8% |
| February | +2.7% | February | -4.4% |
| March | -13.1% | March | +1.4% |
| April | +2.4% | April | -3.4% |
| May | +4.0% | May | +7.0% |
| June | -10.5% | June | +12.3% |
| July | +8.3% | July | -12.9% |
| August | -2.2% | August | +31.7% |
| September | +17.2% | September | +9.2% |
| October | -7.7% | October | -10.5% |
| November | +5.0% | | |
| December | -9.4% | | |
| **2025** | **-5.9%** | **2026 to October** | **+48.7%** |

Nine of 22 months lost money; four lost more than 10%. The longest
stretch below a previous peak was 213 days. The run made 40,409 fills
(0.37x of equity traded per day), was stopped out 126 times, paid 1,348
USDT in fees and received 922 USDT of funding on a 10,000 USDT start, and
held 0.71x gross exposure on average (1.75x at most).

### One year ahead, by resampling

Block bootstrap (10-day blocks, 4,000 paths) of the daily returns:

| Resampled from | Median year | 5th to 95th percentile | P(losing year) | P(drawdown >= 20% / 35% / 50%) |
|---|---:|---:|---:|---:|
| Default, DEV days | x1.52 | x1.00 to x2.43 | 5% | 18% / 0.4% / 0.0% |
| **Default, HOLDOUT days** | **x1.20** | **x0.69 to x2.23** | **30%** | **72% / 17% / 1.4%** |
| Aggressive, DEV days | x2.00 | x0.91 to x4.65 | 8% | 83% / 20% / 1.9% |
| Aggressive, HOLDOUT days | x1.05 | x0.44 to x2.89 | 47% | 99% / 70% / 29% |

If the out-of-sample edge persists, a year at the default setting is most
likely to end between -31% and +123%, with about a one-in-three chance of a
loss and a 20% drawdown more likely than not. If the edge has gone, the
distribution centres on the cost drag, a few percent a year below zero.

## 5. Account size

| Starting equity | DEV compound/day | DEV max drawdown | HOLDOUT compound/day | HOLDOUT total | HOLDOUT max drawdown |
|---:|---:|---:|---:|---:|---:|
| 10,000 USDT | +0.118% | 21.5% | +0.050% | +39.8% | 23.2% |
| 1,000 USDT | +0.120% | 20.9% | +0.035% | +26.4% | 25.4% |
| 100 USDT | +0.157% | 24.3% | +0.009% | +5.9% | 39.8% |
| 100 USDT, aggressive | +0.200% | 33.6% | -0.069% | -37.1% (kill switch, March 2025) | 40.1% |

The model lets a position be no smaller than 10 USDT (twice a 5 USDT
minimum order). At a typical 0.6x gross exposure a 100 USDT account can
then hold about six positions, and fewer once a drawdown has shrunk both
its equity and its risk multiplier. In the holdout it lost a quarter of its
equity in its first three months on a handful of positions, traded at
reduced size for most of the following year and missed most of the
September 2025 and January 2026 gains that the large account made.

A small account's result is the large account's plus a lot of noise, in
either direction: it was the best performer in development and the worst in
the holdout. **100 USDT is the minimum that functions, not the size the
numbers above describe.** From about 1,000 USDT the book is close to the
tested one.

### Post-hoc diagnostic (run after the holdout was seen; not evidence)

Would a lower minimum position fix it? `results/posthoc_small_accounts.json`:

| Start, minimum position | DEV compound/day | HOLDOUT compound/day | HOLDOUT max drawdown |
|---|---:|---:|---:|
| 100 USDT, 10 USDT (default) | +0.157% | +0.009% | 39.8% |
| 100 USDT, 5 USDT | +0.142% | -0.001% | 36.1% |
| 100 USDT, 2 USDT | +0.123% | +0.015% | 29.7% |
| 300 USDT, 10 USDT (default) | +0.131% | +0.037% | 28.8% |
| 300 USDT, 5 USDT | +0.122% | +0.037% | 27.1% |
| 300 USDT, 2 USDT | +0.121% | +0.064% | 24.1% |

Smaller minimums pull the development result toward the large account's
and reduce the drawdown, but the 100 USDT holdout result stays poor in all
three. Nothing was changed because of this table.

## 6. What these numbers do and do not show

* **One out-of-sample regime.** 22 months, of which the first twelve lost
  money. The holdout supports "a positive edge of roughly a third to a half
  of the in-sample size"; it does not pin the number down. A Sharpe ratio of
  0.75 over 1.8 years is about one standard error above zero.
* **About 250 configurations were tried on TRAIN** before the candidates
  were frozen, and the governor and risk level were chosen on DEV. The
  in-sample column is inflated by that search. The holdout column is not,
  with one caveat: it was run once, after an audit, and its data needed one
  repair (below).
* **Simulated fills.** Orders fill at the next hourly open with 3 bps of
  slippage; stops fill beyond their trigger by a quarter of the remaining
  move. Inside an hour the path is unknown. The worst-case stop assumption
  costs about a quarter of the out-of-sample result.
* **Two venues.** The history is Binance's; the paper and live paths trade
  OKX. Prices are near-identical, funding rates and fill quality are not.
* **Data repairs.** Holes in Binance's monthly archive were filled from its
  daily archive. One day, 2026-10-28, is missing from the futures archive
  altogether and was filled from the exchange's spot candles for the 231
  symbols that have a spot market (four days before the end of the
  holdout). See Amendment 3.
* **Edges decay.** Trend and cross-sectional momentum in liquid crypto are
  widely known. The fall from 0.118% to 0.050% a day is partly selection
  bias and partly, plausibly, a real decline.
* **Not modelled:** exchange failure or withdrawal freezes, API outages
  longer than the stops can cover, liquidity at sizes far above the tested
  10,000 USDT, taxes.

## 7. Against simply holding Bitcoin

The frozen default from 1,000 USDT beside 1,000 USDT of BTC bought on the
first day of each period and held, without costs
(`scripts/vs_bitcoin.py`, `results/vs_bitcoin.json`).

| | Bot, default, from 1,000 USDT | BTC bought and held |
|---|---:|---:|
| 2020 (April to December) | +89% | +352% |
| 2021 | +52% | +60% |
| 2022 | +21% | -64% |
| 2023 | +87% | +156% |
| 2024 | +23% | +121% |
| **In-sample: 1,000 USDT became** | **8,000** | **14,601** |
| Maximum drawdown / worst day | 20.9% / -5.6% | 76.7% / -15.4% |
| 2025 | -12% | -6% |
| 2026 (January to October) | +43% | -12% |
| **Out-of-sample: 1,000 USDT became** | **1,264** | **829** |
| Maximum drawdown / worst day | 25.4% / -5.3% | 53.0% / -14.0% |
| Sharpe ratio, in-sample / out-of-sample | 1.74 / 0.57 | 1.22 / -0.02 |

* **When Bitcoin rises, holding it wins, by a wide margin.** It beat the bot
  in every rising year, and that is with the bot's in-sample column tuned
  on those same years.
* **When Bitcoin falls or drifts, the bot wins**: 2022, and the 22 months
  of the holdout taken together. Not every such year: in 2025 the bot lost
  more than Bitcoin did.
* **They are different bets.** The bot's daily returns had a correlation
  of +0.18 with Bitcoin's in-sample and 0.00 out of sample. Holding needs
  Bitcoin to go up; the bot needs trend and momentum to keep paying more
  than they cost. The bot's drawdowns were about a third the size.
* **In money, at this size:** out of sample the bot made 264 USDT on 1,000
  in 22 months, about 0.35 USDT a day, in 20,550 fills that cost 128 USDT
  in fees. A 2% a day target would be 20 USDT on the first day.

### Does anything here beat Bitcoin both when it rises and when it falls?

No. The table sets the bot beside four alternatives, from 1,000 USDT. The
last four columns are arithmetic added after the holdout had been seen;
nothing was selected on them, and the in-sample rows are tuned for every
column that contains the bot.

| | BTC held | Bot | Bot at twice the risk | Same trend rules on BTC alone | Half BTC, half bot | BTC held, bot on top |
|---|---:|---:|---:|---:|---:|---:|
| 2020 (April to December) | +352% | +89% | +186% | +108% | +199% | +694% |
| 2021 | +60% | +52% | +104% | 0% | +69% | +133% |
| 2022 | -64% | +21% | +28% | -4% | -29% | -55% |
| 2023 | +156% | +87% | +154% | +102% | +124% | +360% |
| 2024 | +121% | +23% | +39% | +36% | +71% | +164% |
| **In-sample: 1,000 became** | 14,601 | 8,000 | 26,504 | 5,541 | 13,651 | 101,203 |
| 2025 | -6% | -12% | -34% | -25% | -6% | -17% |
| 2026 (January to October) | -12% | +43% | 0% (kill switch) | +37% | +16% | +26% |
| **Out-of-sample: 1,000 became** | 829 | 1,264 | 658 | 1,035 | 1,091 | 1,052 |
| Maximum drawdown, in / out | 77% / 53% | 21% / 25% | 32% / 40% | 27% / 29% | 41% / 29% | 69% / 51% |

"Same trend rules on BTC alone" is long when Bitcoin trends up and short
when it trends down, at a 3% daily volatility target. "BTC held, bot on
top" is one unit of Bitcoin plus the bot on the same equity: about 1.7x
exposure, on margin. Drawdowns in the last two columns are on daily closes.

* **Bitcoin's gains arrive on a handful of days.** Without its best 17 days
  of 2020-2024 (1% of them) it made +124% instead of +1,360%; without its
  best 87 days it lost 98%. On those 17 days it averaged +11.7% and the bot
  +0.6%. A strategy that is ever flat or short, which it must be to do
  anything useful in a fall, is partly out on those days. Beating a rally
  means already holding the thing.
* **In falling months the bot is flat, not profitable.** In months when
  Bitcoin fell it averaged -0.3% in-sample and -1.1% out of sample, against
  -11.6% and -9.7%, and made money in fewer than half of them. In rising
  months it made +7.0% and +4.3% against +18.9% and +9.3%. It wins the bad
  months by not losing them.
* **More risk does not close the gap.** At twice the risk the bot beat
  Bitcoin over the tuned period as a whole but in only one of its four
  rising years (2021), then hit the kill switch out of sample and ended 34%
  down, behind Bitcoin in a falling market.
* **Timing Bitcoin directly is worse than either.** The same rules on
  Bitcoin alone lagged in every rising year and lost in 2022 and 2025.
* **The only column ahead in every rising year is the one that owns
  Bitcoin.** Holding it and running the bot on top beats Bitcoin by
  whatever the bot makes, whichever way the market goes, and loses with it
  when it falls (-55% in 2022, behind Bitcoin in 2025) with a
  Bitcoin-sized drawdown and borrowed exposure. It is a portfolio choice,
  not a strategy, and it is not built.
* **There is no rising year in the holdout.** How any of this behaves in a
  rally it has not been tuned on is untested.

## 8. Would the classic technical indicators do better?

The bot is already purely quantitative: every decision is a formula on
price, volume and volatility. The textbook indicators are other formulas on
the same prices, so each was put in the bot's place with its textbook
parameters and no tuning: same universe, sizing, 1.5% volatility target,
costs, engine and governor, from 10,000 USDT, on completed daily and 4-hour
bars (`scripts/research_indicators.py`, `results/indicators.json`). This
was run after the holdout had been seen; every rule is reported and none
is selected.

Compound return per day. † = the 40% kill switch fired.

| Rule (textbook settings) | Bets on | Daily bars, in-sample | Daily bars, out-of-sample | 4-hour bars, in-sample | 4-hour bars, out-of-sample |
|---|---|---:|---:|---:|---:|
| **The bot** | continuation | **+0.118%** | **+0.050%** | | |
| SMA 50/200 crossover | continuation | -0.005%† | 0.000% | +0.059% | +0.042% |
| MACD (12, 26, 9) | continuation | +0.081% | -0.075%† | -0.020%† | -0.035% |
| Donchian 20/10 breakout | continuation | +0.057% | -0.019% | +0.033% | -0.031% |
| Supertrend (10, 3) | continuation | +0.022% | +0.053% | +0.018%† | -0.039% |
| Ichimoku cloud (9, 26, 52) | continuation | +0.017% | +0.047% | +0.059% | +0.067% |
| RSI(14) above / below 50 | continuation | +0.023% | +0.028% | +0.047% | -0.069%† |
| Bollinger (20, 2) breakout | continuation | +0.039% | +0.031% | +0.032% | +0.003% |
| Vote of the first five | continuation | +0.055% | -0.009% | +0.062% | +0.025% |
| RSI(14) 30 / 70 | reversal | -0.020%† | -0.062% | -0.029%† | -0.058% |
| Bollinger (20, 2) fade | reversal | -0.022%† | -0.048% | -0.030%† | -0.058% |
| Stochastic (14, 3) 20 / 80 | reversal | -0.030%† | -0.059%† | -0.026%† | -0.049% |

* **None of the 22 beat the bot in-sample; two beat it out of sample**
  (Ichimoku on 4-hour bars, +56% in total, and Supertrend on daily bars,
  +42%, against the bot's +39.8%). The bot ranks third of 23 out of sample.
* **Which two could not have been known in advance.** Among the 16
  continuation rules the rank correlation between in-sample and
  out-of-sample results is -0.01. The best in-sample rule, daily MACD, was
  the worst out of sample and hit the kill switch. The same indicator often
  flipped with the bar size: Supertrend made +42% on daily bars and lost
  23% on 4-hour bars.
* **Every "oversold / overbought" rule lost in both periods**, and the kill
  switch fired in seven of those twelve runs. Buying what has fallen and
  selling what has risen is the opposite bet to the one that has paid in
  this market; the indicator used to place it makes no difference.
* **Continuation rules were a coin flip individually**: eight of 16 made
  money in both periods, seven lost out of sample, and the median
  out-of-sample result was +1%.

An indicator is not information the market lacks. Each one sorts into one of
two bets, that moves continue or that they reverse, and the result depends
on which bet it is, how often it trades and what that costs. What separates
the bot from the single rules in this table is construction rather than a
better formula: it averages five horizons instead of committing to one,
sizes in proportion to the signal instead of all-in and all-out, and adds a
cross-sectional sleeve.

## 9. Bottom line

A daily target of 2% compounding is 1,377x a year. On the evidence here a
robust, risk-governed strategy on liquid crypto perpetuals compounds at
about **0.05% a day** out of sample (about 0.12% in-sample, about 0.19% at
the most the risk dial could do in-sample), with drawdowns of 20-25% at
that setting. Raising the risk to chase the target lowered the out-of-sample
result and, from a 4% volatility target, ended the run at the kill switch.
Closing out each day at +2% turned the out-of-sample gain into a loss.

The app therefore measures every day against the 2% target and reports it,
sizes for a 1.5% daily swing, and never trades to hit the number.
