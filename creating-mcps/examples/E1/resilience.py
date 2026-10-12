"""
E1: calling a backend that can fail, from a tool that must not do anything twice.

    Backend(name, base_url)      one HTTP backend: a timeout on every request, retries with
                                 exponential backoff and jitter (honouring Retry-After), and a
                                 circuit breaker in front of it
    CircuitBreaker               closed -> open after `threshold` failures in a row; open
                                 refuses at once for `cooldown` seconds; then half-open lets one
                                 trial through: success closes it, failure opens it again
    idempotency_key(...)         the same logical action always gets the same key

Retries are only safe because every POST carries an Idempotency-Key: a request that timed out
may have been done, and the key makes doing it again a replay. Without a key, the retry loop
below would isolate a host twice, or open two change tickets.

What a caller sees when it gives up is an error that says what is known: "nothing was done"
when the request never reached the backend (the breaker was open, or every attempt was
refused before it ran), and "not known; check with action_status" when an attempt may have
reached it.
"""

from __future__ import annotations

import hashlib
import random
import threading
import time
from dataclasses import dataclass, field

import httpx

RETRYABLE = {502, 503, 504}


def idempotency_key(*parts: str) -> str:
    return "idem-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


class BackendError(Exception):
    """A backend call that didn't succeed. `maybe_done` says whether it might have."""

    def __init__(self, backend: str, message: str, maybe_done: bool, retry_after: float = 0):
        super().__init__(message)
        self.backend, self.maybe_done, self.retry_after = backend, maybe_done, retry_after


@dataclass
class CircuitBreaker:
    threshold: int = 3                     # failures in a row that open it
    cooldown: float = 5.0                  # seconds it stays open
    state: str = "closed"
    failures: int = 0
    opened_at: float = 0.0
    transitions: list = field(default_factory=list)   # (time, from, to), for the lab
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _trial: bool = False

    def _move(self, to: str) -> None:
        if to != self.state:
            self.transitions.append((round(time.time(), 3), self.state, to))
            self.state = to

    def before(self) -> float:
        """0 if a request may go now; else the seconds until it may."""
        with self._lock:
            if self.state == "open":
                wait = self.opened_at + self.cooldown - time.monotonic()
                if wait > 0:
                    return wait
                self._move("half_open")
                self._trial = False
            if self.state == "half_open":
                if self._trial:            # one trial at a time
                    return 0.5
                self._trial = True
            return 0.0

    def success(self) -> None:
        with self._lock:
            self.failures = 0
            self._trial = False
            self._move("closed")

    def failure(self) -> None:
        with self._lock:
            self.failures += 1
            self._trial = False
            if self.state == "half_open" or self.failures >= self.threshold:
                self.opened_at = time.monotonic()
                self._move("open")


@dataclass
class Backend:
    name: str
    base_url: str
    timeout: float = 2.0                   # per attempt
    attempts: int = 4
    base_delay: float = 0.2
    max_delay: float = 2.0
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    calls: list = field(default_factory=list)          # every attempt, for the lab
    rng: random.Random = field(default_factory=lambda: random.Random(1))

    def request(self, method: str, path: str, json: dict | None = None,
                key: str | None = None) -> dict:
        maybe_done = False
        last = "no attempt was made"
        for attempt in range(1, self.attempts + 1):
            wait = self.breaker.before()
            if wait:
                raise BackendError(self.name, f"{self.name} is failing; its circuit breaker is "
                                   f"open for another {wait:.1f} s. " + (
                                       "An earlier attempt may have reached it, so whether it "
                                       "was done is not known; check before trying again."
                                       if maybe_done else "Nothing was sent."),
                                   maybe_done=maybe_done, retry_after=wait)
            t0 = time.monotonic()
            try:
                r = httpx.request(method, self.base_url + path, json=json, timeout=self.timeout,
                                  headers={"Idempotency-Key": key} if key else None)
                self._log(method, path, attempt, r.status_code, t0,
                          r.headers.get("idempotent-replay"))
            except httpx.TimeoutException:
                # It may have arrived and been done: only the key makes retrying safe.
                maybe_done = maybe_done or method != "GET"
                self._log(method, path, attempt, "timeout", t0)
                self.breaker.failure()
                last = f"timed out after {self.timeout:g} s"
            except httpx.TransportError as e:
                self._log(method, path, attempt, type(e).__name__, t0)
                self.breaker.failure()
                last = f"couldn't connect ({type(e).__name__})"
            else:
                if r.status_code in RETRYABLE:
                    # A 503 from our stand-in may follow the work (a "lost reply").
                    maybe_done = maybe_done or method != "GET"
                    self.breaker.failure()
                    last = f"answered {r.status_code}"
                    retry_after = float(r.headers.get("retry-after") or 0)
                    self._sleep(attempt, retry_after)
                    continue
                self.breaker.success()
                if r.status_code >= 400:   # the backend's answer, not a failure of it
                    raise BackendError(self.name, f"{self.name} refused: {r.status_code} "
                                       f"{r.text[:200]}", maybe_done=False)
                return r.json()
            self._sleep(attempt)
        raise BackendError(self.name, f"{self.name} {last} on {self.attempts} attempts. " + (
            "An attempt may have reached it, so whether it was done is not known; check "
            "before trying again." if maybe_done else "Nothing was done."),
            maybe_done=maybe_done)

    def _sleep(self, attempt: int, at_least: float = 0) -> None:
        if attempt >= self.attempts:
            return
        delay = min(self.max_delay, self.base_delay * 2 ** (attempt - 1))
        time.sleep(max(at_least, delay * (0.5 + self.rng.random() / 2)))   # jitter

    def _log(self, method, path, attempt, outcome, t0, replay=None) -> None:
        self.calls.append({"backend": self.name, "method": method, "path": path,
                           "attempt": attempt, "outcome": outcome, "replay": bool(replay),
                           "ms": round((time.monotonic() - t0) * 1000, 1),
                           "breaker": self.breaker.state})
