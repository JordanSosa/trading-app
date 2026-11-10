"""Monitoring outputs: STATUS.md (renders on GitHub), equity.svg, dashboard.html, summary.json."""

from __future__ import annotations

import html
import json
import math
from typing import Any

from .audit import audit, audit_markdown
from .config import Config
from .state import StateStore, iso

DAY_MS = 86_400_000


def _f(x: Any, nd: int = 2) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "n/a"
    return f"{x:,.{nd}f}"


def _pct(x: Any, nd: int = 2, sign: bool = True) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "n/a"
    return f"{x * 100:+.{nd}f}%" if sign else f"{x * 100:.{nd}f}%"


def _floats(rows: list[dict[str, str]], key: str) -> list[float]:
    out = []
    for r in rows:
        try:
            out.append(float(r[key]))
        except (KeyError, ValueError, TypeError):
            out.append(math.nan)
    return out


def build_summary(store: StateStore, doc: dict[str, Any], cfg: Config) -> dict[str, Any]:
    run, eng, health = doc["run"], doc["engine"], doc["health"]
    initial = float(eng["initial_equity"])
    equity = float(doc.get("live_equity") or eng.get("last_equity") or initial)
    peak = max(float(eng.get("peak_equity") or initial), equity)
    daily = store.read_csv("daily.csv")
    seen: dict[str, dict[str, str]] = {}
    for r in daily:
        seen[r["day_end_ms"]] = r
    daily = [seen[k] for k in sorted(seen, key=int)]
    rets = [x for x in _floats(daily, "return") if math.isfinite(x)]
    target = cfg.risk.daily_target or None
    n = len(rets)
    compound = math.expm1(sum(math.log1p(max(r, -0.999999)) for r in rets) / n) if n else None
    started = int(run["started_ms"])
    now_ms = started
    for key in ("last_tick_at", "last_success_at"):
        if health.get(key):
            from datetime import datetime
            now_ms = max(now_ms, int(datetime.fromisoformat(health[key].replace("Z", "+00:00")).timestamp() * 1000))
    day_start = float(eng.get("day_start_equity") or initial)
    positions = doc.get("positions") or {}
    gross = sum(abs(p.get("notional") or 0.0) for p in positions.values())
    net = sum((p.get("notional") or 0.0) for p in positions.values())
    status = "liquidated" if eng.get("liquidated") else "killed" if eng.get("killed") else \
        "day-stopped" if eng.get("day_stopped") else "day-locked" if eng.get("day_locked") else "running"
    ends = [x for x in _floats(daily, "end_equity") if math.isfinite(x) and x > 0]
    mean_equity = (initial + sum(ends)) / (1 + len(ends))
    days_elapsed = max(0.0, (now_ms - started) / DAY_MS)
    worst = max(float(eng.get("max_drawdown") or 0.0), 1.0 - equity / peak if peak else 0.0)
    goal = cfg.goal
    # The goal is a yardstick: what the yearly goal comes to over the time the run has had.
    pace = (1.0 + goal.annual_return) ** (days_elapsed / 365.0) - 1.0 if goal.annual_return > 0 else None
    return {
        "audit": audit(rets, cfg, fees_paid=float(eng.get("fees_paid") or 0.0), mean_equity=mean_equity,
                       initial_equity=initial),
        "goal": {"annual_return": goal.annual_return or None, "drawdown": goal.drawdown or None, "pace": pace,
                 "ahead_of_pace": (equity / initial - 1.0 >= pace) if pace is not None else None,
                 "drawdown_exceeded": bool(goal.drawdown and worst > goal.drawdown),
                 "kill_drawdown": cfg.risk.kill_dd},
        "generated_at": doc["updated_at"], "run_id": run["id"], "mode": run.get("mode", "paper"), "status": status,
        "start_time": run["start_time"], "days_elapsed": days_elapsed,
        "initial_equity": initial, "equity": equity, "pnl": equity - initial, "return": equity / initial - 1.0,
        "today_return": equity / day_start - 1.0 if day_start else None, "daily_target": target,
        "target_hit_today": bool(eng.get("day_target_hit")),
        "days_completed": n,
        "days_at_target": int(sum(1 for r in rets if target and r >= target)),
        "pct_days_at_target": (sum(1 for r in rets if target and r >= target) / n) if n and target else None,
        "days_up": int(sum(1 for r in rets if r > 0)), "days_down": int(sum(1 for r in rets if r < 0)),
        "mean_daily_return": (sum(rets) / n) if n else None, "compound_daily_return": compound,
        "best_day": max(rets) if rets else None, "worst_day": min(rets) if rets else None,
        "peak_equity": peak, "drawdown_now": 1.0 - equity / peak if peak else 0.0,
        "max_drawdown": float(eng.get("max_drawdown") or 0.0), "risk_multiplier": float(eng.get("risk_mult") or 1.0),
        "gross_exposure": gross / equity if equity > 0 else 0.0, "net_exposure": net / equity if equity > 0 else 0.0,
        "positions": positions, "fees_paid": float(eng.get("fees_paid") or 0.0),
        "funding_paid": float(eng.get("funding_paid") or 0.0), "trades": int(eng.get("trade_count") or 0),
        "stops": int(eng.get("stop_count") or 0),
        "last_bar_closed": iso(int(eng["last_close_ms"]) + cfg.run.interval_ms) if eng.get("last_close_ms") else None,
        "universe": _current_universe(doc, cfg), "health": health,
    }


def _current_universe(doc: dict[str, Any], cfg: Config) -> list[str]:
    hist = (doc.get("universe") or {}).get("history") or []
    if not hist:
        return []
    ranks = hist[-1]["ranks"]
    return [s for s, r in sorted(ranks.items(), key=lambda kv: kv[1]) if 0 < r <= cfg.universe.size]


# ------------------------------------------------------------------------ chart
def equity_svg(store: StateStore, doc: dict[str, Any], width: int = 900, height: int = 320) -> str:
    rows = store.read_csv("equity.csv")
    init = float(doc["engine"]["initial_equity"])
    seen: dict[int, float] = {}
    for r in rows:
        try:
            seen[int(r["ts_ms"])] = float(r["equity"])
        except (KeyError, ValueError):
            continue
    pts = [(int(doc["run"]["started_ms"]), init)] + sorted(seen.items())
    if len(pts) > 1500:                                   # keep the file small on long runs
        stride = math.ceil(len(pts) / 1500)
        pts = pts[::stride] + [pts[-1]]
    pad_l, pad_r, pad_t, pad_b = 72, 20, 34, 40
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    t0, t1 = pts[0][0], max(pts[-1][0], pts[0][0] + 3_600_000)
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    span = max(hi - lo, init * 0.01)
    lo, hi = lo - span * 0.08, hi + span * 0.08

    def x(t: float) -> float:
        return pad_l + (t - t0) / (t1 - t0) * w

    def y(v: float) -> float:
        return pad_t + (1 - (v - lo) / (hi - lo)) * h

    path = " ".join(f"{'M' if i == 0 else 'L'}{x(t):.1f},{y(v):.1f}" for i, (t, v) in enumerate(pts))
    grid = []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        grid.append(f'<line x1="{pad_l}" y1="{y(v):.1f}" x2="{pad_l + w}" y2="{y(v):.1f}" stroke="#e3e5e8"/>'
                    f'<text x="{pad_l - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v:,.0f}</text>')
    for k in range(6):
        t = t0 + (t1 - t0) * k / 5
        grid.append(f'<text x="{x(t):.1f}" y="{height - 14}" text-anchor="middle">{iso(t)[5:10]}</text>')
    base = (f'<line x1="{pad_l}" y1="{y(init):.1f}" x2="{pad_l + w}" y2="{y(init):.1f}" stroke="#9aa0a6" '
            f'stroke-dasharray="4 4"/>') if lo < init < hi else ""
    last = pts[-1][1]
    colour = "#1a7f37" if last >= init else "#cf222e"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
            f'height="{height}" font-family="system-ui,sans-serif" font-size="11" fill="#57606a">'
            f'<rect width="{width}" height="{height}" fill="#ffffff"/>'
            f'<text x="{pad_l}" y="20" font-size="13" fill="#24292f">Equity (USDT): {last:,.2f} '
            f'({last / init - 1:+.2%} since start)</text>{"".join(grid)}{base}'
            f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="1.8"/></svg>\n')


# ---------------------------------------------------------------------- markdown
def status_markdown(s: dict[str, Any], store: StateStore) -> str:
    target = s["daily_target"]
    goal = s.get("goal") or {}
    lines = [
        f"# compounder: {s['mode']} run `{s['run_id']}`", "",
        f"**Status: {s['status']}** | updated {s['generated_at']} | last bar closed {s['last_bar_closed'] or 'n/a'}",
        "", "![equity curve](equity.svg)", "",
        "| | |", "|---|---:|",
        f"| Equity | {_f(s['equity'])} USDT |",
        f"| Since start ({_f(s['days_elapsed'], 1)} days) | {_pct(s['return'])} ({_f(s['pnl'])} USDT) |",
    ]
    if goal.get("annual_return"):
        lines.append(f"| Goal pace for that time ({_pct(goal['annual_return'], 0)} a year) | {_pct(goal['pace'])} |")
    lines.append(f"| Today so far | {_pct(s['today_return'])}{' (target reached)' if s['target_hit_today'] else ''} |")
    if target:
        lines += [
            f"| Daily target | {_pct(target, 1)} |",
            f"| Days at or above target | {s['days_at_target']} of {s['days_completed']}"
            f"{' (' + _pct(s['pct_days_at_target'], 1, False) + ')' if s['pct_days_at_target'] is not None else ''} |"]
    plan = f" (plan for up to {_pct(goal['drawdown'], 0, False)})" if goal.get("drawdown") else ""
    lines += [
        f"| Compound daily return (measured) | {_pct(s['compound_daily_return'], 3)} |",
        f"| Mean daily return | {_pct(s['mean_daily_return'], 3)} |",
        f"| Days up / down | {s['days_up']} / {s['days_down']} |",
        f"| Best / worst day | {_pct(s['best_day'])} / {_pct(s['worst_day'])} |",
        f"| Drawdown now / worst | {_pct(s['drawdown_now'], 1, False)} / {_pct(s['max_drawdown'], 1, False)}{plan} |",
        f"| Risk multiplier | {_f(s['risk_multiplier'])} |",
        f"| Exposure gross / net | {_f(s['gross_exposure'])}x / {_f(s['net_exposure'])}x |",
        f"| Fills / stops | {s['trades']} / {s['stops']} |",
        f"| Fees / funding paid | {_f(s['fees_paid'])} / {_f(s['funding_paid'])} USDT |", "",
    ]
    if goal.get("drawdown_exceeded"):
        lines += [f"**The drawdown has gone deeper than the {_pct(goal['drawdown'], 0, False)} this setting was planned "
                  f"for.** The governor has been cutting risk on the way down and stops the run at "
                  f"{_pct(goal['kill_drawdown'], 0, False)}.", ""]
    if s.get("audit"):
        lines += audit_markdown(s["audit"])
    if s["positions"]:
        lines += ["## Open positions", "", "| Symbol | Side | Notional (USDT) | Entry | Price | Stop |",
                  "|---|---|---:|---:|---:|---:|"]
        for sym, p in sorted(s["positions"].items(), key=lambda kv: -abs(kv[1].get("notional") or 0.0)):
            side = "long" if p["qty"] > 0 else "short"
            lines.append(f"| {sym} | {side} | {_f(p.get('notional'))} | {_g(p.get('entry'))} | {_g(p.get('price'))} "
                         f"| {_g(p.get('stop'))} |")
        lines.append("")
    else:
        lines += ["## Open positions", "", "None (flat).", ""]
    daily = store.read_csv("daily.csv")[-14:]
    if daily:
        lines += ["## Last days", "", "| Date | Return | End equity | |", "|---|---:|---:|---|"]
        for r in reversed(daily):
            flag = "target" if r.get("target_hit") == "1" else ("loss limit" if r.get("loss_limit_hit") == "1" else "")
            lines.append(f"| {r['date']} | {_pct(float(r['return']))} | {_f(float(r['end_equity']))} | {flag} |")
        lines.append("")
    events = store.read_events(limit=12)
    if events:
        lines += ["## Recent events", ""]
        lines += [f"* `{e.get('time')}` **{e.get('kind')}**: {e.get('message')}" for e in reversed(events)]
        lines.append("")
    if s["universe"]:
        lines += ["## Universe", "", ", ".join(s["universe"]), ""]
    h = s["health"]
    lines += ["## Health", "",
              f"Ticks {h.get('ticks')}, last success {h.get('last_success_at')}, data via {h.get('last_provider')}, "
              f"consecutive failures {h.get('consecutive_failures')}"
              + (f", last error: {h.get('last_error')}" if h.get("last_error") else "") + ".", ""]
    return "\n".join(lines)


def _g(x: Any) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "n/a"
    return f"{x:.6g}"


def dashboard_html(s: dict[str, Any], svg: str, md: str) -> str:
    body = html.escape(md.replace("![equity curve](equity.svg)", ""))
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta http-equiv='refresh' content='300'><title>compounder status</title>"
            "<style>body{font-family:system-ui,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;"
            "color:#24292f;background:#fff}pre{white-space:pre-wrap;font-size:13px;line-height:1.5}"
            "svg{max-width:100%;height:auto}</style></head><body>"
            f"<h1>compounder: {html.escape(s['mode'])} run</h1>{svg}<pre>{body}</pre></body></html>\n")


def write_reports(store: StateStore, doc: dict[str, Any], cfg: Config) -> dict[str, Any]:
    s = build_summary(store, doc, cfg)
    svg = equity_svg(store, doc)
    md = status_markdown(s, store)
    store.write_text("summary.json", json.dumps(s, indent=1, sort_keys=True, default=str) + "\n")
    store.write_text("equity.svg", svg)
    store.write_text("STATUS.md", md)
    store.write_text("dashboard.html", dashboard_html(s, svg, md))
    return s
