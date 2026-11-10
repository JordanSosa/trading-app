"""Optional push notifications. Channels are configured through environment
variables (for example GitHub Actions secrets) and are disabled when unset:

* ``COMPOUNDER_NTFY_TOPIC``        ntfy.sh topic (phone push, no account needed);
  ``COMPOUNDER_NTFY_SERVER`` overrides https://ntfy.sh
* ``COMPOUNDER_DISCORD_WEBHOOK``   Discord channel webhook URL
* ``COMPOUNDER_SLACK_WEBHOOK``     Slack incoming-webhook URL
* ``COMPOUNDER_TELEGRAM_BOT_TOKEN`` + ``COMPOUNDER_TELEGRAM_CHAT_ID``

A failing channel is logged and never interrupts trading.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

import requests

from .config import NotifyConfig

log = logging.getLogger(__name__)

ALERT_KINDS = ("alert", "risk", "target", "warning")


class Notifier:
    def __init__(self, cfg: NotifyConfig, env: dict[str, str] | None = None,
                 post: Callable[..., Any] | None = None, timeout_s: float = 10.0, tag: str = "paper"):
        self.cfg = cfg
        self.env = dict(os.environ if env is None else env)
        self._post = post or requests.post
        self.timeout_s = timeout_s
        self.tag = tag
        self.sent: list[str] = []

    @property
    def channels(self) -> list[str]:
        e = self.env
        ch = []
        if e.get("COMPOUNDER_NTFY_TOPIC"):
            ch.append("ntfy")
        if e.get("COMPOUNDER_DISCORD_WEBHOOK"):
            ch.append("discord")
        if e.get("COMPOUNDER_SLACK_WEBHOOK"):
            ch.append("slack")
        if e.get("COMPOUNDER_TELEGRAM_BOT_TOKEN") and e.get("COMPOUNDER_TELEGRAM_CHAT_ID"):
            ch.append("telegram")
        return ch

    def send(self, text: str, title: str = "compounder") -> None:
        self.sent.append(text)
        e = self.env
        for ch in self.channels:
            try:
                if ch == "discord":
                    r = self._post(e["COMPOUNDER_DISCORD_WEBHOOK"], timeout=self.timeout_s,
                                   json={"content": text[:1900], "allowed_mentions": {"parse": []}})
                elif ch == "slack":
                    r = self._post(e["COMPOUNDER_SLACK_WEBHOOK"], json={"text": text}, timeout=self.timeout_s)
                elif ch == "telegram":
                    url = f"https://api.telegram.org/bot{e['COMPOUNDER_TELEGRAM_BOT_TOKEN']}/sendMessage"
                    r = self._post(url, json={"chat_id": e["COMPOUNDER_TELEGRAM_CHAT_ID"], "text": text},
                                   timeout=self.timeout_s)
                else:
                    server = e.get("COMPOUNDER_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
                    r = self._post(f"{server}/{e['COMPOUNDER_NTFY_TOPIC']}", data=text.encode(),
                                   headers={"Title": title}, timeout=self.timeout_s)
                code = getattr(r, "status_code", 200)
                if code >= 400:
                    # Never log the URL: it contains the webhook secret.
                    log.warning("notification via %s rejected: HTTP %s", ch, code)
            except Exception as exc:  # noqa: BLE001
                log.warning("notification via %s failed: %s", ch, type(exc).__name__)

    # --------------------------------------------------------- high-level messages
    def send_trades(self, trades: list[dict[str, Any]], equity: float) -> None:
        if not self.cfg.on_trade or not trades:
            return
        lines = [f"{t['side'].upper()} {t['symbol']} {t['notional']:,.2f} USDT @ {t['price']:.6g} ({t['reason']})"
                 for t in trades[:12]]
        more = f"\n... and {len(trades) - 12} more" if len(trades) > 12 else ""
        self.send(f"[{self.tag}] {len(trades)} fill(s); equity {equity:,.2f} USDT\n" + "\n".join(lines) + more,
                  title="Fills")

    def send_event(self, e: dict[str, Any]) -> None:
        if not self.cfg.on_alert or e.get("kind") not in ALERT_KINDS:
            return
        self.send(f"[{self.tag}] {e['kind'].upper()}: {e['message']}", title=f"compounder {e['kind']}")

    def send_daily(self, date: str, day_return: float, equity: float, total_return: float, drawdown: float,
                   target: float | None, status: str) -> None:
        if not self.cfg.daily_summary:
            return
        hit = ""
        if target:
            hit = " (target reached)" if day_return >= target else f" (target {target:+.1%})"
        self.send(f"[{self.tag}] {date}: day {day_return:+.2%}{hit}; equity {equity:,.2f} USDT "
                  f"({total_return:+.2%} since start); drawdown {drawdown:.1%}; status {status}",
                  title="Daily summary")
