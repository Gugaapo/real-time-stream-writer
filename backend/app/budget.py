"""Monthly DeepSeek spend. Prices are the published Flash rates, in USD per 1M tokens.

Peak is Monday–Friday 01:00–04:00 and 06:00–10:00 UTC. Legacy vision model ids
are billed as Flash. A call is reserved at the peak cache-miss ceiling before
it is sent, then replaced with the usage the API reports.
"""
from __future__ import annotations

import math
import threading
from datetime import datetime, timezone

# USD per 1M tokens. Peak is double the off-peak Flash price.
_RATES = {
    True: {"hit": 0.006, "miss": 0.30, "out": 1.20},
    False: {"hit": 0.003, "miss": 0.15, "out": 0.60},
}

# Held before the HTTP call so parallel chapters cannot slip past the cap.
# 4096 uncached input tokens covers the still plus the prompt; 384 covers max output.
_RESERVE_INPUT = 4096
_RESERVE_OUTPUT = 384


def is_peak(when: datetime) -> bool:
    moment = when.astimezone(timezone.utc)
    if moment.weekday() >= 5:
        return False
    hour = moment.hour
    return (1 <= hour < 4) or (6 <= hour < 10)


def _rates(when: datetime) -> dict[str, float]:
    return _RATES[is_peak(when)]


def reserve_micros() -> int:
    """Worst-case hold for one call, priced at peak cache-miss."""
    rates = _RATES[True]
    usd = (_RESERVE_INPUT * rates["miss"] + _RESERVE_OUTPUT * rates["out"]) / 1_000_000
    return int(math.ceil(usd * 1_000_000))


def cost_micros(usage: dict | None, when: datetime) -> int | None:
    """Micro-dollars (1e-6 USD) for one response. None when the API sent no usage."""
    if not usage:
        return None
    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    details = usage.get("prompt_tokens_details") or {}
    if hit is None and details.get("cached_tokens") is not None:
        cached = int(details["cached_tokens"])
        prompt = int(usage.get("prompt_tokens") or 0)
        hit, miss = cached, max(0, prompt - cached)
    if hit is None or miss is None:
        prompt = usage.get("prompt_tokens")
        if prompt is None:
            return None
        hit, miss = 0, int(prompt)
    out = int(usage.get("completion_tokens") or 0)
    rates = _rates(when)
    usd = (int(hit) * rates["hit"] + int(miss) * rates["miss"] + out * rates["out"]) / 1_000_000
    return int(round(usd * 1_000_000))


class Budget:
    def __init__(self, store, cap_usd: float = 10.0, now=None) -> None:
        self.store = store
        self.cap_micros = int(round(float(cap_usd) * 1_000_000))
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self.reserve = reserve_micros()

    def month_key(self, when: datetime | None = None) -> str:
        moment = when or self._now()
        moment = moment.astimezone(timezone.utc)
        return f"{moment.year:04d}-{moment.month:02d}"

    def spent_micros(self, when: datetime | None = None) -> int:
        return self.store.spend_micros(self.month_key(when))

    def can_afford(self) -> bool:
        with self._lock:
            return self.spent_micros() + self.reserve <= self.cap_micros

    def try_reserve(self) -> str | None:
        """Hold the ceiling price. Returns the UTC month, or None when the cap is reached."""
        with self._lock:
            month = self.month_key()
            if self.store.spend_micros(month) + self.reserve > self.cap_micros:
                return None
            self.store.add_spend_micros(month, self.reserve)
            return month

    def commit(self, month: str, usage: dict | None, when: datetime | None = None) -> None:
        """Replace the hold with the billed amount. An unpriced call keeps the hold."""
        actual = cost_micros(usage, when or self._now())
        if actual is None:
            return
        with self._lock:
            self.store.add_spend_micros(month, actual - self.reserve)

    def release(self, month: str) -> None:
        """Drop the hold when the call was not billed."""
        with self._lock:
            self.store.add_spend_micros(month, -self.reserve)
