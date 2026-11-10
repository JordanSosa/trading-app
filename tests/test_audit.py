"""The self-audit: what a run's own results are allowed to say."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from compounder import audit as au
from compounder.config import config_from_dict, load_config
from compounder.report import status_markdown
from compounder.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
FROZEN = load_config(ROOT / "config" / "paper.toml")          # the shipped default
RESEARCH = load_config(ROOT / "config" / "research.toml")     # the setting the research record was produced at


def run(mean: float, days: int, seed: int = 11, vol: float = 0.015) -> list[float]:
    """Daily returns with exactly this average, so a test does not hang on the luck of a sample."""
    r = np.random.default_rng(seed).normal(0.0, vol, days)
    return [float(x) for x in r - r.mean() + mean]


def synthetic_reference(days: int = 400, mean: float = 0.0006, cost: float = 0.0003) -> dict:
    return {"basis": "synthetic", "key": au.reference_key(FROZEN), "daily_returns": run(mean, days, seed=5),
            "trading_cost_per_day": cost, "fee_per_day": 0.0002}


def test_shipped_references_belong_to_the_shipped_configurations():
    # If this fails a configuration changed: re-run scripts/make_reference.py (and the research behind it).
    assert len(au.load_references()) == 2
    default, research = au.load_reference(FROZEN), au.load_reference(RESEARCH)
    assert default is not None and research is not None and default is not research
    assert default["key"] == au.reference_key(FROZEN) == au.reference_key(load_config(None))
    assert au.load_reference(load_config(ROOT / "config" / "live.example.toml")) is default     # same strategy, live
    assert (default["target_vol"], research["target_vol"]) == (0.010, 0.015)
    assert default["total_return"] == pytest.approx(0.2127, abs=5e-4) and research["total_return"] == pytest.approx(0.2636, abs=5e-4)
    assert default["max_drawdown"] < research["max_drawdown"] and default["daily_vol"] < research["daily_vol"]
    for ref in (default, research):
        r = np.asarray(ref["daily_returns"])
        assert len(r) == ref["days"] == 669
        assert np.prod(1.0 + r) - 1.0 == pytest.approx(ref["total_return"], abs=2e-4)
        assert float(r.mean()) == pytest.approx(ref["mean_daily"], abs=1e-6)
        assert ref["trading_cost_per_day"] == pytest.approx(ref["turnover_per_day"] * 0.0008, rel=1e-4)
        assert ref["fee_per_day"] == pytest.approx(ref["turnover_per_day"] * FROZEN.costs.fee_rate, rel=1e-4)
    # Presets without an out-of-sample record of their own have no reference.
    for name in ("aggressive.toml", "spot.toml"):
        assert au.load_reference(load_config(ROOT / "config" / name)) is None, name


def test_reference_key_follows_what_shapes_returns_and_nothing_else():
    key = au.reference_key(FROZEN)
    bigger = config_from_dict({"run": {"initial_equity": 25_000.0, "mode": "live", "state_dir": "elsewhere"}})
    assert au.reference_key(bigger) == key
    for section, change in (("strategy", {"target_vol": 0.03}), ("risk", {"daily_loss_limit": 0.05}),
                            ("costs", {"slippage_bps": 6.0}), ("universe", {"min_history_days": 60})):
        assert au.reference_key(config_from_dict({section: change})) != key, section


def test_ranges_widen_with_time_and_the_edge_separates_slowly():
    ref = synthetic_reference()
    short, long = au.outcome_ranges(ref, 30), au.outcome_ranges(ref, 365)
    assert au.outcome_ranges(ref, 30) == short                                    # deterministic
    for r in (short, long):
        assert r["edge"]["p05"] < r["edge"]["median"] < r["edge"]["p95"]
        assert r["edge"]["median"] > r["no_edge"]["median"] and r["edge"]["prob_loss"] < r["no_edge"]["prob_loss"]
    assert long["edge"]["p95"] - long["edge"]["p05"] > short["edge"]["p95"] - short["edge"]["p05"]
    assert 0.03 < short["chance_the_edge_shows"] < long["chance_the_edge_shows"] < 1.0
    # A long run is resampled in batches; the result has the same shape.
    far = au.outcome_ranges(ref, 3000, paths=1500)
    assert 0.0 < far["edge"]["prob_loss"] < 0.2 < far["no_edge"]["prob_loss"] and far["chance_the_edge_shows"] > 0.5


def test_shipped_ranges_match_the_research_record():
    r = au.outcome_ranges(au.load_reference(RESEARCH), 365)
    # research/RESULTS.md, section 9: -35% to +111% with the edge, -51% to +58% without, 18% chance it shows.
    assert r["edge"]["p05"] == pytest.approx(-0.35, abs=0.03) and r["edge"]["p95"] == pytest.approx(1.11, abs=0.08)
    assert r["no_edge"]["p05"] == pytest.approx(-0.51, abs=0.03) and r["no_edge"]["p95"] == pytest.approx(0.58, abs=0.06)
    assert r["chance_the_edge_shows"] == pytest.approx(0.18, abs=0.03)
    # The shipped default is the same strategy at two-thirds of the risk: a narrower range, the same odds.
    d = au.outcome_ranges(au.load_reference(FROZEN), 365)
    assert -0.30 < d["edge"]["p05"] < -0.20 and 0.60 < d["edge"]["p95"] < 0.80 and 0.08 < d["edge"]["median"] < 0.14
    assert d["edge"]["p95"] - d["edge"]["p05"] < r["edge"]["p95"] - r["edge"]["p05"]
    assert d["chance_the_edge_shows"] == pytest.approx(r["chance_the_edge_shows"], abs=0.05)


def test_verdicts():
    ref = synthetic_reference()
    assert au.audit([], FROZEN, reference=ref)["verdict"] == "no_days"
    early = au.audit(run(0.0005, 12), FROZEN, reference=ref)
    assert early["verdict"] == "too_early" and early["ranges"] is not None and early["sharpe"] is not None
    assert au.audit([0.01, -0.02, float("nan")], FROZEN, reference=ref)["days"] == 2
    normal = au.audit(run(0.0005, 120), FROZEN, reference=ref)
    assert normal["verdict"] == "inside" and normal["ranges"]["edge"]["p05"] < normal["result"] < normal["ranges"]["edge"]["p95"]
    assert normal["sharpe_error"] == pytest.approx(np.sqrt(365 / 120), rel=0.02)
    assert not normal["beats_no_edge"] and not early["beats_no_edge"]
    # Between the no-edge ceiling and the top of the normal range: still normal, and the first real evidence.
    r = normal["ranges"]
    assert r["no_edge"]["p95"] < r["edge"]["p95"]
    strong = au.audit(run(float(np.log1p((r["no_edge"]["p95"] + r["edge"]["p95"]) / 2) / 120) + 0.5 * 0.015 ** 2, 120),
                      FROZEN, reference=ref)
    assert strong["verdict"] == "inside" and strong["beats_no_edge"]
    assert "above what a bot with no edge would make" in "\n".join(au.audit_markdown(strong))
    assert au.audit(run(-0.006, 120), FROZEN, reference=ref)["verdict"] == "below"
    assert au.audit(run(0.008, 120), FROZEN, reference=ref)["verdict"] == "above"
    # A changed strategy has no out-of-sample record: no ranges, no verdict on the result.
    other = au.audit(run(0.0005, 120), config_from_dict({"strategy": {"target_vol": 0.03}}), reference=ref)
    assert other["verdict"] == "no_reference" and other["ranges"] is None and other["result"] is not None
    assert au.audit([0.01] * 40, FROZEN, reference={"key": "something else", "daily_returns": [0.0] * 50})["verdict"] \
        == "no_reference"


def test_fees_are_compared_with_the_research_run():
    ref = synthetic_reference()
    days = run(0.0005, 60, seed=3)
    fine = au.audit(days, FROZEN, fees_paid=60 * 1000 * 0.00025, mean_equity=1000.0, reference=ref)
    assert fine["fee_per_day"] == pytest.approx(0.00025) and fine["reference_fee_per_day"] == 0.0002
    assert not fine["fees_high"]
    high = au.audit(days, FROZEN, fees_paid=60 * 1000 * 0.0005, mean_equity=1000.0, reference=ref)
    assert high["fees_high"] and "more than twice the research rate" in "\n".join(au.audit_markdown(high))
    # Too early to judge the fee rate: opening the first positions inflates it.
    assert not au.audit(days[:10], FROZEN, fees_paid=10 * 1000 * 0.0005, mean_equity=1000.0, reference=ref)["fees_high"]


def test_markdown_states_the_reading():
    ref = synthetic_reference()
    text = "\n".join(au.audit_markdown(au.audit(run(0.0005, 90, seed=2), FROZEN, reference=ref)))
    assert "## Self-audit" in text and "| Completed days | 90 |" in text
    assert "Normal for 90 days if the research holds" in text and "if the bot has no edge" in text
    assert "inside the normal range" in text and "times in 100" in text and "never changes the strategy" in text
    assert "Sharpe ratio so far" in text
    assert "too early" in "\n".join(au.audit_markdown(au.audit([0.01] * 5, FROZEN, reference=ref)))
    assert "nothing yet" in "\n".join(au.audit_markdown(au.audit([], FROZEN, reference=ref)))
    none = "\n".join(au.audit_markdown(au.audit([0.01, -0.01] * 30, config_from_dict({"risk": {"kill_dd": 0.3}}),
                                                reference=ref)))
    assert "no research reference" in none and "Normal for" not in none
    for verdict in ("below", "above"):
        a = au.audit(run(-0.006 if verdict == "below" else 0.008, 120), FROZEN, reference=ref)
        assert f"{verdict} the normal range" in "\n".join(au.audit_markdown(a))


def test_a_missing_or_broken_reference_is_not_an_error(tmp_path):
    key = au.reference_key(FROZEN)
    assert au.load_references(tmp_path / "absent.json") == () and au.load_reference(FROZEN, tmp_path / "absent.json") is None
    (tmp_path / "bad.json").write_text("{not json")
    assert au.load_references(tmp_path / "bad.json") == ()
    (tmp_path / "old.json").write_text(json.dumps({"key": key, "daily_returns": [0.01] * 50}))     # not a list of them
    assert au.load_references(tmp_path / "old.json") == ()
    (tmp_path / "short.json").write_text(json.dumps({"references": [{"key": key, "daily_returns": [0.01] * 5}]}))
    assert au.load_reference(FROZEN, tmp_path / "short.json") is None
    (tmp_path / "good.json").write_text(json.dumps({"references": [{"key": "other", "daily_returns": [0.01] * 50},
                                                                   {"key": key, "daily_returns": [0.02] * 50}]}))
    assert au.load_reference(FROZEN, tmp_path / "good.json")["daily_returns"][0] == 0.02
    assert au.load_reference(RESEARCH, tmp_path / "good.json") is None
    # Without a reference the audit still reports the result; it just has no range to hold it against.
    a = au.audit([0.01, -0.01] * 20, config_from_dict({"strategy": {"target_vol": 0.02}}))
    assert a["verdict"] == "no_reference" and a["ranges"] is None


def test_status_page_carries_the_audit(tmp_path):
    from compounder.forward import new_run_document
    from compounder.report import build_summary

    store = StateStore(tmp_path / "state")
    t0 = 1_760_054_400_000                                                        # 2025-10-10 00:00 UTC
    doc = new_run_document(FROZEN, now_ms=t0)
    equity, rows = 1000.0, []
    for i, r in enumerate(np.random.default_rng(9).normal(0.0004, 0.015, 45)):
        rows.append((t0 + (i + 1) * 86_400_000, equity, equity * (1.0 + r), ""))
        equity *= 1.0 + r
    store.append_daily(rows)
    doc["engine"]["last_equity"] = equity
    doc["engine"]["fees_paid"] = 9.0
    s = build_summary(store, doc, FROZEN)
    assert s["audit"]["days"] == 45 and s["audit"]["verdict"] in ("inside", "below", "above")
    assert s["audit"]["fee_per_day"] == pytest.approx(9.0 / 45 / ((1000.0 + sum(r[2] for r in rows)) / 46))
    json.dumps(s, default=str)                                                    # summary.json stays serialisable
    md = status_markdown(s, store)
    assert "## Self-audit" in md and md.index("## Self-audit") < md.index("## Open positions")
    assert "shipped default out of sample" in md and "typically" in md and not s["audit"]["small_account"]
    # The same days under the research setting are judged against the research setting's own record.
    wide = build_summary(store, doc, RESEARCH)["audit"]
    assert "1.5% volatility target" in wide["basis"]
    assert wide["ranges"]["edge"]["p95"] > s["audit"]["ranges"]["edge"]["p95"]


def test_status_page_measures_against_the_goal(tmp_path):
    from compounder.forward import new_run_document
    from compounder.report import build_summary

    store = StateStore(tmp_path / "state")
    t0 = 1_760_054_400_000
    doc = new_run_document(FROZEN, now_ms=t0)
    equity, rows = 1000.0, []
    for i in range(73):                                                           # a fifth of a year, +0.05% a day
        rows.append((t0 + (i + 1) * 86_400_000, equity, equity * 1.0005, ""))
        equity *= 1.0005
    store.append_daily(rows)
    doc["engine"].update(last_equity=equity, peak_equity=equity, max_drawdown=0.08)
    doc["health"]["last_success_at"] = "2025-12-22T00:00:00Z"                       # 73 days after the start
    s = build_summary(store, doc, FROZEN)
    assert s["days_elapsed"] == pytest.approx(73.0)
    assert s["goal"]["annual_return"] == 0.12 and s["goal"]["pace"] == pytest.approx(1.12 ** 0.2 - 1.0)
    assert s["goal"]["ahead_of_pace"] and not s["goal"]["drawdown_exceeded"] and s["daily_target"] is None
    md = status_markdown(s, store)
    assert "| Goal pace for that time (+12% a year) | +2.29% |" in md
    assert "(plan for up to 25%)" in md and "Daily target" not in md and "at or above target" not in md
    assert "deeper than" not in md
    # A drawdown beyond the plan is said in words; a run behind the pace is just a number.
    doc["engine"].update(last_equity=1000.0, max_drawdown=0.27)
    s = build_summary(store, doc, FROZEN)
    assert s["goal"]["drawdown_exceeded"] and not s["goal"]["ahead_of_pace"]
    assert "deeper than the 25% this setting was planned for" in status_markdown(s, store) and "stops the run at 40%" in status_markdown(s, store)
    # The original yardstick is still there for whoever sets it; a goal of zero removes the rows.
    old = config_from_dict({"risk": {"daily_target": 0.02}, "goal": {"annual_return": 0.0, "drawdown": 0.0}})
    md = status_markdown(build_summary(store, doc, old), store)
    assert "| Daily target | +2.0% |" in md and "Days at or above target" in md
    assert "Goal pace" not in md and "plan for" not in md and "deeper than" not in md
    # An account too small for the researched book is told so.
    doc["engine"]["initial_equity"] = 300.0
    small = build_summary(store, doc, FROZEN)
    assert small["audit"]["small_account"] and "smaller than the research describes" in status_markdown(small, store)
