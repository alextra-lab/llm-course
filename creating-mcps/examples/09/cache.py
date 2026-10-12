"""
Unit 9: a result cache for the gateway's read-only tools, and the two ways to get it wrong.

Where it sits decides whether it's safe. It runs INSIDE the policy layer (Unit 7's
`gateway.RESULTS` hook): a call is authenticated, granted, evaluated against the rules and
logged first, and only then may the cache answer instead of the backend. And its key starts
with the persona the call acts as, because the answer depends on it: row-level security,
field-level security and masking all differ by persona.

    key   = persona | tool | the arguments, canonical JSON | the "now" step (if relative)
    value = the tool's result, as JSON, for `ttl` seconds

Relative windows ("now-7d") mean something different every second, so a key that contains
them is only reusable while "now" stays put. With `MCP_ROUND_NOW=60` (Unit 4's windows.py)
"now" moves once a minute, and the step number goes in the key.

Not cached: calls that return or follow a cursor (they hold state on the cluster), and any
tool not in CACHEABLE. Two stores: `local` (this process only, so each replica warms its own
copy) and `shared` (Valkey, so one replica's answer serves them all).

    UnsafeCache, below, is what NOT to build: a cache in front of the policy, keyed without
    the persona. The lab runs it to show what leaks.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "04"), str(HERE.parent)]
import windows  # noqa: E402  (Unit 4)
from common_mcp import log_note, persona  # noqa: E402

# Read-only, deterministic, small: worth keeping. get_contact is personal data behind an
# approval; search_events holds a cursor; neither is cached.
CACHEABLE = {"get_asset", "find_assets", "count_assets", "service_dependencies",
             "hosts_with_vulnerability", "list_incidents", "get_incident",
             "event_histogram", "top_values"}


def _relative(arguments: dict) -> bool:
    """Does the answer depend on when it's asked? (a window relative to now, or open-ended)"""
    if "start" in arguments and "end" not in arguments:
        return True
    return any(isinstance(v, str) and v.strip().startswith("now") for v in arguments.values())


def cache_key(who: str, tool: str, arguments: dict) -> str:
    step = ""
    if _relative(arguments):
        now = windows.current_time().timestamp()
        step = str(int(now // windows.ROUND_NOW)) if windows.ROUND_NOW else str(now)
    raw = json.dumps([who, tool, arguments, step], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class _Local:
    """A small LRU with expiry, in this process."""

    def __init__(self, size: int = 2048):
        self.size, self.items = size, OrderedDict()

    async def get(self, key: str) -> str | None:
        item = self.items.get(key)
        if item is None or item[0] < time.monotonic():
            self.items.pop(key, None)
            return None
        self.items.move_to_end(key)
        return item[1]

    async def set(self, key: str, value: str, ttl: float) -> None:
        self.items[key] = (time.monotonic() + ttl, value)
        self.items.move_to_end(key)
        while len(self.items) > self.size:
            self.items.popitem(last=False)


class _Shared:
    """The same, in Valkey: every replica reads and writes one copy."""

    def __init__(self, client, prefix: str):
        self.client, self.prefix = client, prefix

    async def get(self, key: str) -> str | None:
        try:
            return await self.client.get(f"{self.prefix}:{key}")
        except Exception:  # noqa: BLE001 -- a cache that's down is a miss, never an error
            return None

    async def set(self, key: str, value: str, ttl: float) -> None:
        try:
            await self.client.set(f"{self.prefix}:{key}", value, ex=max(1, int(ttl)))
        except Exception:  # noqa: BLE001
            pass


class ResultCache:
    """`gateway.RESULTS`: get_or_run(tool, arguments, run) for every call the policy allowed."""

    def __init__(self, store, ttl: float = 60.0):
        self.store, self.ttl = store, ttl
        self.hits = self.misses = 0

    @classmethod
    def local(cls, ttl: float = 60.0) -> ResultCache:
        return cls(_Local(), ttl)

    @classmethod
    def shared(cls, client, prefix: str, ttl: float = 60.0) -> ResultCache:
        return cls(_Shared(client, prefix), ttl)

    async def get_or_run(self, tool: str, arguments: dict, run):
        if tool not in CACHEABLE or arguments.get("cursor"):
            return await asyncio.to_thread(run)
        key = cache_key(persona(), tool, arguments)
        cached = await self.store.get(key)
        if cached is not None:
            self.hits += 1
            log_note(cache="hit")
            return json.loads(cached)
        self.misses += 1
        log_note(cache="miss")
        result = await asyncio.to_thread(run)
        await self.store.set(key, json.dumps(result, default=str), self.ttl)
        return result


class UnsafeCache:
    """DON'T. MCP middleware in front of everything, keyed on the tool and its arguments only.
    The first caller's answer is everyone's: priya gets marc's rows, and nobody's call reaches
    the policy (no verdict logged, no ticket asked) once someone else has made it."""

    def __init__(self, ttl: float = 60.0):
        self.store, self.ttl = _Local(), ttl

    async def middleware(self, ctx, call_next):
        if ctx.method != "tools/call":
            return await call_next(ctx)
        params = ctx.params or {}
        key = cache_key("", params.get("name", ""), params.get("arguments") or {})
        cached = await self.store.get(key)
        if cached is not None:
            log_note(cache="hit-unsafe")
            return json.loads(cached)
        result = await call_next(ctx)
        if isinstance(result, dict) and result.get("resultType", "complete") == "complete" \
                and not result.get("isError"):
            await self.store.set(key, json.dumps(result, default=str), self.ttl)
        return result


def from_env(state_client=None, prefix: str = "mcp") -> ResultCache | UnsafeCache | None:
    """MCP_CACHE = off | local | shared | unsafe, MCP_CACHE_TTL seconds."""
    mode = os.environ.get("MCP_CACHE", "off")
    ttl = float(os.environ.get("MCP_CACHE_TTL", 60))
    if mode == "local":
        return ResultCache.local(ttl)
    if mode == "shared":
        return ResultCache.shared(state_client, prefix, ttl)
    if mode == "unsafe":
        return UnsafeCache(ttl)
    return None
