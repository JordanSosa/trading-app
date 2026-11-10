"""Small HTTPS client with retries, exponential backoff, size caps and deadlines."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

import requests

USER_AGENT = "compounder/1.0 (trading research bot)"


class HttpError(RuntimeError):
    """Raised when a request cannot be completed."""


class HttpClient:
    max_json_bytes = 32 * 1024 * 1024
    max_blob_bytes = 64 * 1024 * 1024

    def __init__(self, timeout_s: float = 15.0, retries: int = 3, backoff_s: float = 1.0,
                 session: requests.Session | None = None, sleep: Callable[[float], None] = time.sleep):
        self.timeout_s = timeout_s
        self.retries = max(1, retries)
        self.backoff_s = backoff_s
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self._sleep = sleep

    def _read(self, resp: requests.Response, limit: int, deadline: float) -> bytes:
        buf = bytearray()
        for chunk in resp.iter_content(65536):
            buf.extend(chunk)
            if len(buf) > limit:
                raise HttpError(f"response larger than {limit} bytes")
            if time.monotonic() > deadline:
                raise HttpError("response took too long")
        return bytes(buf)

    def request_json(self, method: str, url: str, params: dict[str, Any] | None = None,
                     body: Any = None, headers: dict[str, str] | None = None) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            fatal = False
            try:
                deadline = time.monotonic() + 2 * self.timeout_s
                with self.session.request(method, url, params=params, json=body, headers=headers,
                                          timeout=self.timeout_s, stream=True) as resp:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise HttpError(f"HTTP {resp.status_code} from {url}")
                    if resp.status_code >= 400:
                        fatal = True  # 4xx other than 429 will not fix itself
                        raise HttpError(f"HTTP {resp.status_code} from {url}")
                    raw = self._read(resp, self.max_json_bytes, deadline)
                return json.loads(raw)
            except (requests.RequestException, ValueError, HttpError) as exc:
                last_exc = exc
                if fatal or attempt == self.retries - 1:
                    break
                self._sleep(self.backoff_s * (2 ** attempt))
        raise HttpError(f"request failed: {url}: {last_exc}") from last_exc

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self.request_json("GET", url, params=params)

    def post_json(self, url: str, body: Any) -> Any:
        return self.request_json("POST", url, body=body)

    def get_bytes(self, url: str, params: dict[str, Any] | None = None) -> bytes | None:
        """GET raw bytes; returns None on 404 (used for bulk archives)."""
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                timeout = max(self.timeout_s, 60.0)
                deadline = time.monotonic() + timeout * 4
                with self.session.get(url, params=params, timeout=timeout, stream=True) as resp:
                    if resp.status_code == 404:
                        return None
                    if resp.status_code >= 400:
                        raise HttpError(f"HTTP {resp.status_code} from {url}")
                    return self._read(resp, self.max_blob_bytes, deadline)
            except (requests.RequestException, HttpError) as exc:
                last_exc = exc
                if attempt < self.retries - 1:
                    self._sleep(self.backoff_s * (2 ** attempt))
        raise HttpError(f"download failed: {url}: {last_exc}") from last_exc
