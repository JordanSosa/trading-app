"""Optional push notifications. All channels are configured via environment
variables (e.g. GitHub Actions secrets) and are silently disabled when unset:

* ``TRADEBOT_DISCORD_WEBHOOK``   - Discord channel webhook URL
* ``TRADEBOT_SLACK_WEBHOOK``     - Slack incoming-webhook URL
* ``TRADEBOT_TELEGRAM_BOT_TOKEN`` + ``TRADEBOT_TELEGRAM_CHAT_ID``
* ``TRADEBOT_NTFY_TOPIC``        - ntfy.sh topic (phone push, no account needed);
  ``TRADEBOT_NTFY_SERVER`` overrides https://ntfy.sh

A failing channel is logged and never interrupts trading.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

import pandas as pd
import requests

from .config import NotifyConfig
from .engine import EngineState
from .state import StateStore, iso

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg: NotifyConfig, env: dict[str, str] | None = None,
                 post: Callable[..., Any] | None = None, timeout_s: float = 10.0):
        self.cfg = cfg
        self.env = dict(os.environ if env is None else env)
        self._post = post or requests.post
        self.timeout_s = timeout_s
        self.sent: list[str] = []

    @property
    def channels(self) -> list[str]:
        e = self.env
        ch = []
        if e.get("TRADEBOT_DISCORD_WEBHOOK"):
            ch.append("discord")
        if e.get("TRADEBOT_SLACK_WEBHOOK"):
            ch.append("slack")
        if e.get("TRADEBOT_TELEGRAM_BOT_TOKEN") and e.get("TRADEBOT_TELEGRAM_CHAT_ID"):
            ch.append("telegram")
        if e.get("TRADEBOT_NTFY_TOPIC"):
            ch.append("ntfy")
        return ch

    def send(self, text: str, title: str = "BTC paper trader") -> None:
        self.sent.append(text)
        e = self.env
        for ch in self.channels:
            try:
                if ch == "discord":
                    self._post(e["TRADEBOT_DISCORD_WEBHOOK"], json={"content": text[:1900]}, timeout=self.timeout_s)
                elif ch == "slack":
                    self._post(e["TRADEBOT_SLACK_WEBHOOK"], json={"text": text}, timeout=self.timeout_s)
                elif ch == "telegram":
                    url = f"https://api.telegram.org/bot{e['TRADEBOT_TELEGRAM_BOT_TOKEN']}/sendMessage"
                    self._post(url, json={"chat_id": e["TRADEBOT_TELEGRAM_CHAT_ID"], "text": text},
                               timeout=self.timeout_s)
                elif ch == "ntfy":
                    server = e.get("TRADEBOT_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
                    self._post(f"{server}/{e['TRADEBOT_NTFY_TOPIC']}", data=text.encode(),
                               headers={"Title": title}, timeout=self.timeout_s)
            except Exception as exc:  # noqa: BLE001
                log.warning("notification via %s failed: %s", ch, type(exc).__name__)

    # high-level messages ----------------------------------------------------
    def send_trade(self, t: dict[str, Any], doc: dict[str, Any]) -> None:
        if not self.cfg.on_trade:
            return
        self.send(f"[paper] {t['side'].upper()} {t['qty']:.5f} BTC @ {t['price']:,.2f} "
                  f"({t['reason']}); equity {t['equity_after']:,.2f} USDT", title="Paper trade")

    def send_event(self, e: dict[str, Any], doc: dict[str, Any]) -> None:
        if not self.cfg.on_alert or e.get("kind") not in ("alert", "stop", "end", "warning", "info"):
            return
        if e.get("kind") == "info" and "started" not in e.get("message", ""):
            return
        self.send(f"[paper] {e['kind'].upper()}: {e['message']}", title=f"Paper {e['kind']}")

    def maybe_daily_summary(self, doc: dict[str, Any], store: StateStore, now_ms: int) -> None:
        if not self.cfg.daily_summary:
            return
        today = pd.Timestamp(now_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
        last = doc.get("notify", {}).get("last_daily_summary")
        if last == today:
            return
        st = EngineState.from_dict(doc["engine"])
        live = doc["health"].get("last_live_price") or st.last_price or 0.0
        eq = st.cash + st.qty * live
        bh = live / doc["run"]["start_price"] - 1 if doc["run"].get("start_price") else 0.0
        self.send(f"[paper] Daily summary {today}: equity {eq:,.2f} USDT ({eq / st.initial_equity - 1:+.2%}), "
                  f"B&H {bh:+.2%}, exposure {st.qty * live / eq if eq else 0:.0%}, trades {st.trade_count}, "
                  f"max DD {st.max_drawdown:.2%}, status {st.status}", title="Daily summary")
        doc.setdefault("notify", {})["last_daily_summary"] = today
        doc["notify"]["last_daily_summary_at"] = iso(now_ms)
        # Persist the marker so we only send once per day.
        from .forward import _clean
        state_doc = store.load() or doc
        state_doc.setdefault("notify", {}).update(doc["notify"])
        store.save(_clean(state_doc))
