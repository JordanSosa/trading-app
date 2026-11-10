"""Self-audit: what a run's own results do and do not show yet.

A paper or live run is judged against the research record instead of against
hope. A reference is the strategy's out-of-sample daily returns at one
configuration (written by ``scripts/make_reference.py`` for the shipped default
and for the research setting). Resampling those days gives the range of
results that is normal for a run of any length if the research holds, and the
range for a bot that predicts nothing and still pays to trade.
The two ranges overlap for a long time; the audit says by how much, so a good
or a bad month is not read as proof of anything.

The audit only reads. It never changes the strategy: in a walk-forward
simulation (research/RESULTS.md, section 9) no bot that re-tuned itself on six
months of its own results or less beat leaving the strategy alone, and the
faster it adapted the worse it did.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .config import SMALL_ACCOUNT, Config
from .metrics import block_bootstrap

REFERENCE_FILE = Path(__file__).with_name("reference.json")
MIN_DAYS = 30            # below this many completed days nothing is read into the result
PATHS = 4000             # resampled runs per range
BLOCK = 10               # days per resampled block (keeps losing streaks and volatility clusters together)
FEE_ALARM = 2.0          # fees per day above this multiple of the research run's are flagged


def reference_key(cfg: Config) -> str:
    """Hash of everything that shapes the strategy's returns, whatever the account size or mode."""
    material = {"strategy": cfg.strategy.to_dict(), "risk": dataclasses.asdict(cfg.risk),
                "costs": dataclasses.asdict(cfg.costs), "universe": dataclasses.asdict(cfg.universe),
                "timeframe": cfg.run.timeframe}
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()[:16]


@functools.lru_cache(maxsize=4)
def load_references(path: str | Path = REFERENCE_FILE) -> tuple[dict[str, Any], ...]:
    """The references shipped with the package: one per configuration that has an out-of-sample record."""
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return ()
    refs = doc.get("references") if isinstance(doc, dict) else None
    return tuple(r for r in refs or () if isinstance(r, dict) and len(r.get("daily_returns") or []) >= 3 * BLOCK)


def load_reference(cfg: Config, path: str | Path = REFERENCE_FILE) -> dict[str, Any] | None:
    """The reference for this configuration, or None if it has no out-of-sample record."""
    key = reference_key(cfg)
    return next((r for r in load_references(path) if r.get("key") == key), None)


def _final_returns(series: np.ndarray, days: int, paths: int) -> np.ndarray:
    """Total return of ``paths`` resampled runs of ``days`` days (in batches, to bound memory on long runs)."""
    out = []
    batch = max(1, min(paths, 2_000_000 // max(days, 1)))
    seed = 7
    while sum(len(x) for x in out) < paths:
        n = min(batch, paths - sum(len(x) for x in out))
        sample = block_bootstrap(series, days, n_paths=n, block=BLOCK, seed=seed)
        out.append(np.expm1(np.log1p(np.maximum(sample, -0.999999)).sum(axis=1)))
        seed += 1
    return np.concatenate(out)


def outcome_ranges(ref: dict[str, Any], days: int, paths: int = PATHS) -> dict[str, Any]:
    """5th to 95th percentile of a ``days``-day run: with the edge measured in research, and with none.

    "No edge" is the same days with their mean removed and the trading costs
    left in. ``chance_the_edge_shows`` is how often the run with the edge ends
    above what 95% of no-edge runs reach: the chance a run this long proves it.
    """
    r = np.asarray(ref["daily_returns"], dtype="float64")
    null = r - r.mean() - float(ref.get("trading_cost_per_day", 0.0))
    out: dict[str, Any] = {}
    finals = {}
    for key, series in (("edge", r), ("no_edge", null)):
        final = _final_returns(series, days, paths)
        q = np.quantile(final, [0.05, 0.5, 0.95])
        finals[key] = final
        out[key] = {"p05": float(q[0]), "median": float(q[1]), "p95": float(q[2]), "prob_loss": float((final < 0).mean())}
    out["chance_the_edge_shows"] = float((finals["edge"] > out["no_edge"]["p95"]).mean())
    return out


def audit(daily_returns: list[float], cfg: Config, fees_paid: float = 0.0, mean_equity: float | None = None,
          reference: dict[str, Any] | None = None, initial_equity: float | None = None) -> dict[str, Any]:
    """Judge a run's completed days against the research record.

    ``reference`` defaults to the shipped one for this configuration, if there is one.
    """
    rets = np.asarray([x for x in daily_returns if math.isfinite(x)], dtype="float64")
    n = int(rets.size)
    out: dict[str, Any] = {"days": n, "min_days": MIN_DAYS, "result": None, "sharpe": None, "sharpe_error": None,
                           "ranges": None, "fee_per_day": None, "reference_fee_per_day": None, "fees_high": False,
                           "beats_no_edge": False, "basis": None,
                           "small_account": bool(initial_equity is not None and initial_equity < SMALL_ACCOUNT)}
    if n == 0:
        out["verdict"] = "no_days"
        return out
    out["result"] = float(np.expm1(np.log1p(np.maximum(rets, -0.999999)).sum()))
    sd = float(rets.std(ddof=1)) if n > 1 else 0.0
    if n >= 10 and sd > 0:
        daily_sharpe = float(rets.mean()) / sd
        out["sharpe"] = daily_sharpe * math.sqrt(365.0)
        out["sharpe_error"] = math.sqrt((1.0 + 0.5 * daily_sharpe ** 2) / n) * math.sqrt(365.0)
    ref = reference if reference is not None else load_reference(cfg)
    if ref is None or ref.get("key") != reference_key(cfg):
        out["verdict"] = "no_reference"
        return out
    out["basis"] = ref.get("basis")
    out["ranges"] = outcome_ranges(ref, n)
    if mean_equity and mean_equity > 0 and fees_paid >= 0:
        out["fee_per_day"] = float(fees_paid) / n / float(mean_equity)
        out["reference_fee_per_day"] = float(ref.get("fee_per_day") or 0.0) or None
        if out["reference_fee_per_day"] and n >= MIN_DAYS:
            out["fees_high"] = out["fee_per_day"] > FEE_ALARM * out["reference_fee_per_day"]
    edge = out["ranges"]["edge"]
    # Above what 95 in 100 no-edge runs of this length reach: the one result that counts as evidence of an edge.
    out["beats_no_edge"] = n >= MIN_DAYS and out["result"] > out["ranges"]["no_edge"]["p95"]
    if n < MIN_DAYS:
        out["verdict"] = "too_early"
    elif out["result"] < edge["p05"]:
        out["verdict"] = "below"
    elif out["result"] > edge["p95"]:
        out["verdict"] = "above"
    else:
        out["verdict"] = "inside"
    return out


def _pct(x: float, nd: int = 1) -> str:
    return f"{x * 100:+.{nd}f}%"


READINGS = {
    "no_days": "**Reading: nothing yet.** No trading day has completed.",
    "too_early": "**Reading: too early.** Fewer than {min_days} completed days: nothing can be read into the result yet.",
    "inside": "**Reading: inside the normal range.** A run this long tells a working strategy from one with no edge "
              "about {chance} times in 100, so this result is not yet evidence either way.",
    "inside_strong": "**Reading: inside the normal range, and above what a bot with no edge would make.** The result "
                     "beats 95 in 100 no-edge runs of this length, which is evidence that the edge is real; a run "
                     "this long shows that about {chance} times in 100 when it is.",
    "below": "**Reading: below the normal range.** Worse than 19 in 20 runs of this length resampled from the research "
             "record. Check the data feed, the fills and the fees first; if they are sound, the edge is smaller than "
             "measured or gone.",
    "above": "**Reading: above the normal range.** Better than 19 in 20 runs of this length resampled from the research "
             "record. Expect it to fall back: a lucky stretch is far more likely than a larger edge.",
    "no_reference": "**Reading: no research reference for this configuration.** The normal ranges exist for the frozen "
                    "default only; a changed strategy, risk or cost setting has no out-of-sample record to be judged "
                    "against.",
}


def audit_markdown(a: dict[str, Any]) -> list[str]:
    """The status page's self-audit section."""
    lines = ["## Self-audit", ""]
    n = a["days"]
    if a["verdict"] == "no_days":
        return lines + [READINGS["no_days"], ""]
    lines += ["| | |", "|---|---:|", f"| Completed days | {n} |", f"| Result over those days | {_pct(a['result'])} |"]
    r = a.get("ranges")
    if r:
        lines += [
            f"| Normal for {n} days if the research holds | {_pct(r['edge']['p05'])} to {_pct(r['edge']['p95'])}, "
            f"typically {_pct(r['edge']['median'])} |",
            f"| Normal for {n} days if the bot has no edge | {_pct(r['no_edge']['p05'])} to {_pct(r['no_edge']['p95'])}, "
            f"typically {_pct(r['no_edge']['median'])} |",
        ]
    if a.get("sharpe") is not None:
        lines.append(f"| Sharpe ratio so far | {a['sharpe']:+.1f}, give or take {a['sharpe_error']:.1f} |")
    if a.get("fee_per_day") is not None and a.get("reference_fee_per_day"):
        lines.append(f"| Fees per day, share of equity | {a['fee_per_day'] * 100:.3f}% (research run: "
                     f"{a['reference_fee_per_day'] * 100:.3f}%) |")
    chance = round(100 * r["chance_the_edge_shows"]) if r else 0
    reading = "inside_strong" if a["verdict"] == "inside" and a.get("beats_no_edge") else a["verdict"]
    lines += ["", READINGS[reading].format(min_days=a["min_days"], chance=chance)]
    if a.get("fees_high"):
        lines.append("**Fees are running at more than twice the research rate**: the bot is trading more, or paying "
                     "more per trade, than the record assumes.")
    if a.get("small_account"):
        lines.append(f"**This account is smaller than the research describes.** Under about {SMALL_ACCOUNT:,.0f} USDT the "
                     "exchange's minimum order size, not the risk setting, decides the positions, and such accounts "
                     "did poorly in research at every setting.")
    if r:
        lines += ["", f"Ranges are the 5th to 95th percentile of resampled runs of the {a['basis']}. \"No edge\" is the "
                      "same days with the average gain removed and the trading costs left in. The audit only reports; "
                      "it never changes the strategy."]
    return lines + [""]
