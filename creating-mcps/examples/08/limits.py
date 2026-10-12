"""
Unit 8: per-caller limits -- how much of a shared server any one caller may use.

Four limits, checked in this order on every tool call:

    rate         a token bucket of requests per caller: `rate` per second, bursts up to `burst`
    quota        a token bucket of RESULT tokens per caller per minute: what the calls returned,
                 charged after each call (a request count alone lets one caller pull
                 megabytes in a few calls)
    concurrency  at most `per_caller` calls in flight per caller, waiting up to `queue_wait`
                 seconds for a slot
    capacity     at most `total` calls in flight on the whole server

and one for what a call leaves behind after it returns:

    cursors      at most `cursors` open result cursors per caller. A paged search holds a point
                 in time on the cluster until its last page or until it expires (`cursor_hold`
                 seconds); the cluster allows a few hundred for everyone together, and an agent
                 that never reads page 2 leaves every one of them open. `cursors_total`
                 caps them for everyone: per-caller limits only protect the backend if they
                 can't add up past its own limit

Any of them can refuse with Busy: what ran out, and when to retry. Per-caller limits are what
make it fair: a runaway agent fills its own slots and its own buckets, not everyone's.

The gateway connects to the backends as one login (Unit 7), so the database's own per-persona
limits (connection limit, statement_timeout) no longer separate callers. These are what does.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field


@dataclass
class Settings:
    rate: float = 5.0                  # requests per second per caller, sustained
    burst: int = 10                    # requests a caller may make at once after a pause
    quota: int = 50_000                # result tokens per caller per minute
    per_caller: int = 2                # calls in flight per caller
    total: int = 16                    # calls in flight on the server
    queue_wait: float = 1.0            # seconds a call may wait for a slot
    cursors: int = 10                  # result cursors a caller may hold open
    cursors_total: int = 100           # ... and all callers together. The cluster allows 300
                                       # contexts, one per shard a cursor covers: a day spans
                                       # two daily indexes, so 100 cursors hold ~200
    cursor_hold: float = 120.0         # seconds an abandoned cursor stays open (the PIT keep-alive)

    @classmethod
    def from_env(cls) -> Settings:
        """MCP_RATE, MCP_BURST, MCP_QUOTA, MCP_PER_CALLER, MCP_TOTAL, MCP_QUEUE_WAIT,
        MCP_CURSORS, MCP_CURSORS_TOTAL, MCP_CURSOR_HOLD."""
        out = cls()
        for name, kind in (("rate", float), ("burst", int), ("quota", int),
                           ("per_caller", int), ("total", int), ("queue_wait", float),
                           ("cursors", int), ("cursors_total", int),
                           ("cursor_hold", float)):
            raw = os.environ.get(f"MCP_{name.upper()}")
            if raw:
                setattr(out, name, kind(raw))
        return out

    def as_dict(self) -> dict:
        return asdict(self)


class Busy(Exception):
    """A refusal the caller can act on: which limit, and how long to wait."""

    MESSAGES = {
        "rate": "you're calling faster than your rate limit ({rate:g}/s)",
        "quota": "you've used your result budget for this minute ({quota:,} tokens)",
        "concurrency": "you already have {per_caller} calls running",
        "capacity": "the server is at capacity",
        "cursors": "you have {cursors} result cursors open. Read to the last page of a search "
                   "you need, or count and aggregate instead of listing",
        "cursors_total": "the server has {cursors_total} result cursors open for everyone. "
                         "Count or aggregate instead of listing",
    }

    def __init__(self, kind: str, retry_after: float, settings: Settings):
        self.kind, self.retry_after = kind, max(1, math.ceil(retry_after))
        why = self.MESSAGES[kind].format(**settings.as_dict())
        super().__init__(f"busy: {why}. Retry the same call after {self.retry_after} s; "
                         "don't retry sooner, and use what you already have meanwhile.")


@dataclass
class TokenBucket:
    capacity: float
    rate: float                        # tokens added per second
    tokens: float = field(default=-1.0)
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)  # the lab fakes it
    updated: float = field(default=-1.0)

    def __post_init__(self):
        if self.tokens < 0:
            self.tokens = self.capacity
        if self.updated < 0:
            self.updated = self.clock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    def take(self, n: float = 1) -> float:
        """Take n tokens if there are enough; returns 0, or the seconds until there will be."""
        self._refill()
        if self.tokens >= n:
            self.tokens -= n
            return 0.0
        return (n - self.tokens) / self.rate

    def debit(self, n: float) -> None:
        """Charge n tokens after the fact; the balance may go negative (a debt to wait off)."""
        self._refill()
        self.tokens -= n

    def wait_for(self, n: float = 1) -> float:
        """Seconds until n tokens are there, without taking them (0: they are now)."""
        self._refill()
        return 0.0 if self.tokens >= n else (n - self.tokens) / self.rate

    def wait_until_positive(self) -> float:
        self._refill()
        return 0.0 if self.tokens > 0 else (1 - self.tokens) / self.rate


class Limiter:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.requests: dict[str, TokenBucket] = {}
        self.results: dict[str, TokenBucket] = {}
        self.slots: dict[str, asyncio.Semaphore] = {}
        self.cursors: dict[str, TokenBucket] = {}
        s = self.settings
        self.all_cursors = TokenBucket(s.cursors_total, s.cursors_total / s.cursor_hold)
        self.total = asyncio.Semaphore(self.settings.total)
        self.in_flight: dict[str, int] = {}

    def _for(self, caller: str):
        s = self.settings
        if caller not in self.requests:
            self.requests[caller] = TokenBucket(s.burst, s.rate)
            self.results[caller] = TokenBucket(s.quota, s.quota / 60)
            self.slots[caller] = asyncio.Semaphore(s.per_caller)
            self.cursors[caller] = TokenBucket(s.cursors, s.cursors / s.cursor_hold)
            self.in_flight[caller] = 0
        return self.requests[caller], self.results[caller], self.slots[caller]

    @contextlib.asynccontextmanager
    async def admit(self, caller: str):
        """Hold a slot for one call, or raise Busy. Yields the seconds spent waiting."""
        requests, results, mine = self._for(caller)
        wait = requests.take()
        if wait:
            raise Busy("rate", wait, self.settings)
        wait = results.wait_until_positive()
        if wait:
            raise Busy("quota", wait, self.settings)
        t0 = time.monotonic()
        if not await _acquire(mine, self.settings.queue_wait):
            raise Busy("concurrency", 1, self.settings)
        try:
            left = self.settings.queue_wait - (time.monotonic() - t0)
            if not await _acquire(self.total, max(0.0, left)):
                raise Busy("capacity", 1, self.settings)
            self.in_flight[caller] += 1
            try:
                yield time.monotonic() - t0
            finally:
                self.in_flight[caller] -= 1
                self.total.release()
        finally:
            mine.release()

    def charge(self, caller: str, result_tokens: int) -> None:
        self._for(caller)[1].debit(result_tokens)

    def may_open_cursor(self, caller: str) -> None:
        """Before a call that may leave a cursor open: raise Busy if this caller holds its
        limit. (Counted as a bucket that refills over `cursor_hold`: an upper bound, since a
        cursor read to its last page is freed sooner.)"""
        self._for(caller)
        wait = self.cursors[caller].wait_for(1)
        if wait:
            raise Busy("cursors", wait, self.settings)
        wait = self.all_cursors.wait_for(1)
        if wait:
            raise Busy("cursors_total", wait, self.settings)

    def opened_cursor(self, caller: str) -> None:
        self._for(caller)
        self.cursors[caller].debit(1)
        self.all_cursors.debit(1)


async def _acquire(sem: asyncio.Semaphore, timeout: float) -> bool:
    """Acquire within `timeout` seconds (0: only if free right now)."""
    if timeout <= 0:
        if sem.locked():
            return False
        await sem.acquire()
        return True
    try:
        await asyncio.wait_for(sem.acquire(), timeout=timeout)
    except TimeoutError:
        return False
    return True
