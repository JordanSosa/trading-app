"""Monitoring outputs: STATUS.md (renders on GitHub), equity.svg, dashboard.html, summary.json."""

from __future__ import annotations

import html
import json
import math
from typing import Any

import pandas as pd

from .config import Config
from .engine import EngineState
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


def build_summary(store: StateStore, doc: dict[str, Any]) -> dict[str, Any]:
    run, health = doc["run"], doc["health"]
    st = EngineState.from_dict(doc["engine"])
    if run.get("completed_at") and run.get("final_price"):
        live = run["final_price"]  # results are frozen at the campaign end
    else:
        live = health.get("last_live_price") or st.last_price or run["start_price"]
    equity = st.cash + st.qty * live
    initial = st.initial_equity
    now_ms = pd.Timestamp(health.get("last_tick_at") or doc["updated_at"]).value // 1_000_000
    elapsed = max(0.0, (min(now_ms, run["end_ms"]) - run["start_ms"]) / DAY_MS)
    trips = store.read_csv("trips.csv")
    trades = store.read_csv("trades.csv")
    if len(trades):
        trades = trades.drop_duplicates(subset=["ts_ms", "side", "qty", "price"], keep="last")
    if len(trips):
        trips = trips.drop_duplicates(subset=["entry_ms", "exit_ms"], keep="last")
    unreal = st.qty * live - st.qty * st.avg_cost if st.qty > 0 else 0.0
    bh = live / run["start_price"] - 1.0 if run.get("start_price") else None
    ret = equity / initial - 1.0
    return {
        "generated_at": doc["updated_at"],
        "run_id": run["id"],
        "mode": run.get("mode", "paper"),
        "status": st.status,
        "strategy": run.get("strategy_description", run["strategy"]),
        "start_time": run["start_time"],
        "end_time": run["end_time"],
        "completed_at": run.get("completed_at"),
        "days_elapsed": elapsed,
        "days_total": run["duration_days"],
        "progress": min(1.0, elapsed / run["duration_days"]) if run["duration_days"] else None,
        "initial_equity": initial,
        "equity": equity,
        "pnl": equity - initial,
        "return": ret,
        "buy_hold_return": bh,
        "excess_return": (ret - bh) if bh is not None else None,
        "price": live,
        "start_price": run.get("start_price"),
        "cash": st.cash,
        "qty": st.qty,
        "position_value": st.qty * live,
        "exposure": (st.qty * live / equity) if equity > 0 else 0.0,
        "avg_cost": st.avg_cost if st.qty > 0 else None,
        "unrealized_pnl": unreal,
        "realized_pnl": st.realized_pnl,
        "fees_paid": st.fees_paid,
        "max_drawdown": st.max_drawdown,
        "peak_equity": st.peak_equity,
        "drawdown_now": max(0.0, 1.0 - equity / max(st.peak_equity, equity)) if st.peak_equity else 0.0,
        "trades": int(len(trades)),
        "round_trips": int(len(trips)),
        "win_rate": float((trips["pnl"] > 0).mean()) if len(trips) else None,
        "stop_price": st.stop_price,
        "pending": st.pending,
        "halt_until": iso(st.halt_until_ms),
        "awaiting_rearm": st.rearm_required,
        "last_bar_closed": iso(st.last_close_ms + doc["run"]["interval_ms"]) if st.last_close_ms else None,
        "health": health,
    }


# --------------------------------------------------------------------- chart
def equity_svg(store: StateStore, doc: dict[str, Any], width: int = 900, height: int = 320) -> str:
    eq = store.read_csv("equity.csv")
    run = doc["run"]
    init = float(run["initial_cash"])
    pts_t = [int(run["start_ms"])]
    pts_e = [init]
    pts_b = [init]
    if len(eq):
        eq = eq.drop_duplicates(subset=["ts_ms"], keep="last").sort_values("ts_ms")
        pts_t += eq["ts_ms"].astype("int64").tolist()
        pts_e += eq["equity"].astype(float).tolist()
        pts_b += (init * eq["price"].astype(float) / float(run["start_price"])).tolist()
    pad_l, pad_r, pad_t, pad_b = 72, 20, 34, 40
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    t0 = pts_t[0]
    t1 = max(pts_t[-1], t0 + 3_600_000)
    lo = min(pts_e + pts_b)
    hi = max(pts_e + pts_b)
    span = max(hi - lo, init * 0.01)
    lo, hi = lo - span * 0.08, hi + span * 0.08

    def x(t: float) -> float:
        return pad_l + (t - t0) / (t1 - t0) * w

    def y(v: float) -> float:
        return pad_t + (1 - (v - lo) / (hi - lo)) * h

    def path(vals: list[float]) -> str:
        return " ".join(f"{'M' if i == 0 else 'L'}{x(t):.1f},{y(v):.1f}" for i, (t, v) in enumerate(zip(pts_t, vals)))

    grid = []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        yy = y(v)
        grid.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{pad_l + w}" y2="{yy:.1f}" stroke="#e3e5e8"/>'
                    f'<text x="{pad_l - 8}" y="{yy + 4:.1f}" text-anchor="end">{v:,.0f}</text>')
    n_xt = 5
    for k in range(n_xt + 1):
        t = t0 + (t1 - t0) * k / n_xt
        lbl = pd.Timestamp(int(t), unit="ms", tz="UTC").strftime("%b %d %H:%M")
        grid.append(f'<text x="{x(t):.1f}" y="{pad_t + h + 22}" text-anchor="middle">{lbl}</text>')
    base_y = y(init)
    final_e = pts_e[-1]
    color = "#1a7f37" if final_e >= init else "#cf222e"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="12" fill="#57606a">
<rect width="100%" height="100%" fill="#ffffff" rx="8"/>
<text x="{pad_l}" y="20" font-size="13" font-weight="600" fill="#24292f">Paper equity (USDT) vs buy &amp; hold since start</text>
<g transform="translate({width - 300},10)"><rect width="12" height="3" y="6" fill="{color}"/><text x="18" y="12" fill="#24292f">Strategy {final_e:,.0f}</text><rect x="140" width="12" height="3" y="6" fill="#8c959f"/><text x="158" y="12" fill="#24292f">Buy &amp; hold {pts_b[-1]:,.0f}</text></g>
{''.join(grid)}
<line x1="{pad_l}" y1="{base_y:.1f}" x2="{pad_l + w}" y2="{base_y:.1f}" stroke="#8c959f" stroke-dasharray="4 4"/>
<path d="{path(pts_b)}" fill="none" stroke="#8c959f" stroke-width="1.5"/>
<path d="{path(pts_e)}" fill="none" stroke="{color}" stroke-width="2.2"/>
</svg>
"""


# ------------------------------------------------------------------ markdown
def status_markdown(s: dict[str, Any], store: StateStore, n_trades: int = 15, n_events: int = 12) -> str:
    h = s["health"]
    state_word = {"running": "RUNNING", "halted": "HALTED (circuit breaker)", "stopped": "STOPPED (stop-loss)",
                  "ending": "ENDING", "completed": "COMPLETED"}.get(s["status"], s["status"].upper())
    lines = [
        f"# BTC/USDT paper trading - {state_word}",
        "",
        f"> **Paper money only. No real orders are placed.** Updated {s['generated_at']} - data source "
        f"`{h.get('last_provider')}` - run `{s['run_id']}`",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Equity | **{_f(s['equity'])} USDT** ({_pct(s['return'])}) |",
        f"| P&L | {_f(s['pnl'])} USDT (realised {_f(s['realized_pnl'])}, unrealised {_f(s['unrealized_pnl'])}) |",
        f"| Buy & hold since start | {_pct(s['buy_hold_return'])} (strategy vs B&H: {_pct(s['excess_return'])}) |",
        f"| BTC price | {_f(s['price'])} (start {_f(s['start_price'])}) |",
        f"| Position | {s['qty']:.5f} BTC = {_f(s['position_value'])} USDT ({_pct(s['exposure'], 1, False)} exposure) |",
        f"| Cash | {_f(s['cash'])} USDT |",
        f"| Max drawdown | {_pct(s['max_drawdown'], 2, False)} (current {_pct(s['drawdown_now'], 2, False)}) |",
        f"| Fees paid | {_f(s['fees_paid'])} USDT |",
        f"| Trades / round trips | {s['trades']} / {s['round_trips']} (win rate {_pct(s['win_rate'], 0, False)}) |",
        f"| Run window | {s['start_time']} -> {s['end_time']} (day {s['days_elapsed']:.1f} of {s['days_total']:g}) |",
        f"| Strategy | `{s['strategy']}` |",
    ]
    if s["stop_price"]:
        lines.append(f"| Protective stop | {_f(s['stop_price'])} |")
    if s["pending"]:
        lines.append(f"| Pending order | target exposure {_pct(s['pending'].get('target'), 0, False)} at next open "
                     f"({s['pending'].get('reason')}) |")
    if s["halt_until"]:
        lines.append(f"| Circuit breaker | flat until {s['halt_until']} |")
    if s["awaiting_rearm"]:
        lines.append("| Re-entry | waiting for signal to reset after a stop-out |")
    lines += ["", "![equity curve](equity.svg)", ""]

    trades = store.read_csv("trades.csv")
    lines += ["## Recent fills", ""]
    if len(trades):
        trades = trades.drop_duplicates(subset=["ts_ms", "side", "qty", "price"], keep="last").tail(n_trades)
        lines += ["| Time (UTC) | Side | Qty BTC | Fill price | Notional | Fee | Realised P&L | Reason |",
                  "|---|---|---:|---:|---:|---:|---:|---|"]
        for _, t in trades.iloc[::-1].iterrows():
            lines.append(f"| {t['time']} | {t['side']} | {t['qty']:.5f} | {_f(t['price'])} | {_f(t['notional'])} | "
                         f"{_f(t['fee'])} | {_f(t['realized_pnl'])} | {t['reason']} |")
    else:
        lines.append("_No fills yet._")
    lines += ["", "## Events", ""]
    events = store.read_events(limit=n_events)
    if events:
        for e in reversed(events):
            lines.append(f"- `{e.get('time')}` **{e.get('kind')}** - {e.get('message')}")
    else:
        lines.append("_No events yet._")
    lines += [
        "",
        "## Health",
        "",
        f"- Last tick: {h.get('last_tick_at')} (ticks run: {h.get('ticks')})",
        f"- Last successful tick: {h.get('last_success_at')}",
        f"- Last closed bar processed: {s['last_bar_closed']}",
        f"- Consecutive failures: {h.get('consecutive_failures', 0)}"
        + (f" - last error: {h.get('last_error')}" if h.get("last_error") else ""),
        "",
        "Ledgers: [trades.csv](trades.csv) - [trips.csv](trips.csv) - [equity.csv](equity.csv) - "
        "[events.jsonl](events.jsonl) - [state.json](state.json)",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------- html
def dashboard_html(s: dict[str, Any], svg: str, md_tables: list[tuple[str, str]]) -> str:
    def row(k: str, v: str) -> str:
        return f"<tr><th>{html.escape(k)}</th><td>{v}</td></tr>"

    ret_cls = "pos" if s["return"] >= 0 else "neg"
    rows = "".join([
        row("Equity", f"<b>{_f(s['equity'])} USDT</b> <span class='{ret_cls}'>{_pct(s['return'])}</span>"),
        row("Buy &amp; hold since start", _pct(s["buy_hold_return"])),
        row("BTC price", _f(s["price"])),
        row("Position", f"{s['qty']:.5f} BTC ({_pct(s['exposure'], 1, False)})"),
        row("Cash", f"{_f(s['cash'])} USDT"),
        row("Max drawdown", _pct(s["max_drawdown"], 2, False)),
        row("Fees paid", f"{_f(s['fees_paid'])} USDT"),
        row("Trades / round trips", f"{s['trades']} / {s['round_trips']}"),
        row("Run window", f"{html.escape(s['start_time'])} &rarr; {html.escape(s['end_time'])} "
            f"(day {s['days_elapsed']:.1f} of {s['days_total']:g})"),
        row("Strategy", f"<code>{html.escape(s['strategy'])}</code>"),
        row("Status", html.escape(s["status"])),
    ])
    sections = "".join(f"<h2>{html.escape(t)}</h2>{body}" for t, body in md_tables)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BTC Paper Trader</title>
<style>
:root {{ --bg:#f6f8fa; --card:#ffffff; --fg:#1f2328; --muted:#59636e; --line:#d1d9e0; --pos:#1a7f37; --neg:#cf222e; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0d1117; --card:#151b23; --fg:#e6edf3; --muted:#9198a1; --line:#3d444d; --pos:#3fb950; --neg:#f85149; }} }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif; }}
main {{ max-width:960px; margin:0 auto; padding:24px 16px 48px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin:16px 0; overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }}
th,td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); font-weight:500; white-space:nowrap; }}
.pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }}
.note {{ color:var(--muted); font-size:13px; }}
svg {{ max-width:100%; height:auto; }}
</style></head>
<body><main>
<h1>BTC/USDT paper trading</h1>
<p class="note">Paper money only - no real orders. Updated {html.escape(str(s['generated_at']))}.</p>
<div class="card"><table>{rows}</table></div>
<div class="card">{svg}</div>
<div class="card">{sections}</div>
</main></body></html>
"""


def _html_table(df: pd.DataFrame, cols: list[str]) -> str:
    if df.empty:
        return "<p class='note'>None yet.</p>"
    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(r[c]))}</td>" for c in cols) + "</tr>"
                   for _, r in df.iterrows())
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def write_reports(store: StateStore, doc: dict[str, Any], cfg: Config | None = None) -> dict[str, Any]:
    s = build_summary(store, doc)
    svg = equity_svg(store, doc)
    store.write_text("equity.svg", svg)
    store.write_text("STATUS.md", status_markdown(s, store))
    trades = store.read_csv("trades.csv")
    if len(trades):
        trades = trades.drop_duplicates(subset=["ts_ms", "side", "qty", "price"], keep="last").tail(30).iloc[::-1]
        trades = trades.assign(qty=trades["qty"].map(lambda v: f"{v:.5f}"),
                               price=trades["price"].map(_f), fee=trades["fee"].map(_f),
                               realized_pnl=trades["realized_pnl"].map(_f))
    events = pd.DataFrame(store.read_events(limit=30)[::-1])
    tables = [
        ("Recent fills", _html_table(trades, ["time", "side", "qty", "price", "fee", "realized_pnl", "reason"])
         if len(trades) else "<p class='note'>No fills yet.</p>"),
        ("Events", _html_table(events, ["time", "kind", "message"]) if len(events) else "<p class='note'>No events yet.</p>"),
    ]
    store.write_text("dashboard.html", dashboard_html(s, svg, tables))
    store.write_text("summary.json", json.dumps(_json_safe(s), indent=2, sort_keys=True) + "\n")
    return s


def _json_safe(o: Any) -> Any:
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o
