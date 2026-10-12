"""
Unit 9: Unit 8's per-caller limits, kept in Valkey so every replica enforces the same ones.

Unit 8's Limiter lives in one process's memory. Run three replicas behind a load balancer and
each has its own buckets and slots: every caller gets three times the rate, three times the
concurrency, and the server-wide capacity triples with nobody deciding it should. This class
has the same interface (`admit`, `charge`, `may_open_cursor`, `opened_cursor`) and keeps the
state in Valkey instead:

    rate, quota, cursors   token buckets, one hash per caller (tokens, last refill)
    concurrency, capacity  sorted sets of leases, one per call in flight, each with an
                           expiry so a replica that dies mid-call can't hold a slot forever

Every check-and-update is one Lua script, which Valkey runs atomically: two replicas can't
both take the last token. Every script reads the time from Valkey (`TIME`), not from the
replica, so replicas whose clocks disagree still agree on the buckets.

The price: one round trip to Valkey per check, about a tenth of a millisecond on a laptop.
And a new failure: if Valkey is down, `admit` lets the call through (fail open) and logs it.
Refusing every call because the limiter is down turns a limiter outage into a full outage;
the backends' own limits (pools, timeouts) still stand. Choose the other way if a backend
can't take an unlimited burst.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "08")]
from limits import Busy, Settings  # noqa: E402  (Unit 8)

_NOW = "local t = redis.call('TIME'); local now = tonumber(t[1]) + tonumber(t[2]) / 1e6\n"
_BUCKET = """
local function refill(key, cap, rate, now)
  local v = redis.call('HMGET', key, 't', 'ts')
  local tokens = tonumber(v[1]) or cap
  local ts = tonumber(v[2]) or now
  return math.min(cap, tokens + math.max(0, now - ts) * rate)
end
local function store(key, tokens, now, ttl)
  redis.call('HSET', key, 't', tostring(tokens), 'ts', tostring(now))
  redis.call('EXPIRE', key, ttl)
end
"""

# KEYS: request bucket, result bucket. ARGV: burst, rate, quota, quota_rate, ttl
TAKE = _NOW + _BUCKET + """
local req = refill(KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), now)
if req < 1 then return {'rate', tostring((1 - req) / tonumber(ARGV[2]))} end
local res = refill(KEYS[2], tonumber(ARGV[3]), tonumber(ARGV[4]), now)
if res <= 0 then return {'quota', tostring((1 - res) / tonumber(ARGV[4]))} end
store(KEYS[1], req - 1, now, tonumber(ARGV[5]))
return {'ok', '0'}
"""

# KEYS: bucket. ARGV: capacity, rate, amount, ttl. The balance may go negative: a debt.
DEBIT = _NOW + _BUCKET + """
local tokens = refill(KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), now)
store(KEYS[1], tokens - tonumber(ARGV[3]), now, tonumber(ARGV[4]))
return tostring(tokens - tonumber(ARGV[3]))
"""

# KEYS: caller's leases, everyone's leases. ARGV: per_caller, total, lease id, lease seconds
LEASE = _NOW + """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then return 'concurrency' end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[2]) then return 'capacity' end
local expires = now + tonumber(ARGV[4])
redis.call('ZADD', KEYS[1], expires, ARGV[3])
redis.call('ZADD', KEYS[2], expires, ARGV[3])
redis.call('EXPIRE', KEYS[1], math.ceil(tonumber(ARGV[4])) + 1)
redis.call('EXPIRE', KEYS[2], math.ceil(tonumber(ARGV[4])) + 1)
return 'ok'
"""

# KEYS: caller's cursor bucket, everyone's. ARGV: cursors, rate, cursors_total, total rate
CURSORS = _NOW + _BUCKET + """
local mine = refill(KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), now)
if mine < 1 then return {'cursors', tostring((1 - mine) / tonumber(ARGV[2]))} end
local all = refill(KEYS[2], tonumber(ARGV[3]), tonumber(ARGV[4]), now)
if all < 1 then return {'cursors_total', tostring((1 - all) / tonumber(ARGV[4]))} end
return {'ok', '0'}
"""


class SharedLimiter:
    """Unit 8's Limiter, with its state in Valkey. `prefix` separates servers (and learners
    sharing one Valkey); `lease_seconds` must outlast the longest call."""

    def __init__(self, settings: Settings, client, prefix: str, lease_seconds: float = 60.0):
        self.settings, self.client, self.prefix = settings, client, prefix
        self.lease_seconds = lease_seconds
        self.scripts = {name: client.register_script(src) for name, src in
                        (("take", TAKE), ("debit", DEBIT), ("lease", LEASE),
                         ("cursors", CURSORS))}
        self.failed_open = 0               # calls let through because Valkey didn't answer

    def _key(self, *parts: str) -> str:
        return ":".join((self.prefix, *parts))

    def _ttl(self) -> int:                 # long enough for any bucket to refill completely
        s = self.settings
        return int(max(s.burst / s.rate, 60, s.cursor_hold)) + 5

    async def _run(self, name: str, keys: list[str], args: list):
        return await self.scripts[name](keys=keys, args=args)

    @contextlib.asynccontextmanager
    async def admit(self, caller: str):
        """Hold a slot for one call, or raise Busy. Yields the seconds spent waiting."""
        s = self.settings
        try:
            kind, wait = await self._run("take", [self._key("req", caller),
                                                  self._key("res", caller)],
                                         [s.burst, s.rate, s.quota, s.quota / 60, self._ttl()])
        except Exception as e:  # noqa: BLE001 -- the limiter is down: let the call through
            self._fail_open(e)
            yield 0.0
            return
        if kind != "ok":
            raise Busy(kind, float(wait), s)
        lease, t0, pause = uuid.uuid4().hex, time.monotonic(), 0.01
        keys = [self._key("slots", caller), self._key("slots")]
        while True:                        # wait up to queue_wait for a slot, as in Unit 8
            try:
                got = await self._run("lease", keys,
                                      [s.per_caller, s.total, lease, self.lease_seconds])
            except Exception as e:  # noqa: BLE001
                self._fail_open(e)
                yield time.monotonic() - t0
                return
            if got == "ok":
                break
            if time.monotonic() - t0 + pause > s.queue_wait:
                raise Busy(got, 1, s)
            await asyncio.sleep(pause)
            pause = min(pause * 2, 0.1)
        try:
            yield time.monotonic() - t0
        finally:
            try:
                await self.client.zrem(keys[0], lease)
                await self.client.zrem(keys[1], lease)
            except Exception:  # noqa: BLE001 -- the lease expires on its own
                pass

    def _fail_open(self, e: Exception) -> None:
        self.failed_open += 1
        print(json.dumps({"operation": "limiter.fail_open", "error": type(e).__name__}),
              file=sys.stderr, flush=True)

    async def charge(self, caller: str, result_tokens: int) -> None:
        s = self.settings
        with contextlib.suppress(Exception):
            await self._run("debit", [self._key("res", caller)],
                            [s.quota, s.quota / 60, result_tokens, self._ttl()])

    async def may_open_cursor(self, caller: str) -> None:
        s = self.settings
        try:
            kind, wait = await self._run(
                "cursors", [self._key("cur", caller), self._key("cur")],
                [s.cursors, s.cursors / s.cursor_hold, s.cursors_total,
                 s.cursors_total / s.cursor_hold])
        except Exception as e:  # noqa: BLE001
            self._fail_open(e)
            return
        if kind != "ok":
            raise Busy(kind, float(wait), s)

    async def opened_cursor(self, caller: str) -> None:
        s = self.settings
        with contextlib.suppress(Exception):
            await self._run("debit", [self._key("cur", caller)],
                            [s.cursors, s.cursors / s.cursor_hold, 1, self._ttl()])
            await self._run("debit", [self._key("cur")],
                            [s.cursors_total, s.cursors_total / s.cursor_hold, 1, self._ttl()])
