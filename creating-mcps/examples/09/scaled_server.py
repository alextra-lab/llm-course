"""
Unit 9: Unit 8's envelope server, ready to run as several replicas.

Streamable HTTP in the 2026-07-28 spec is stateless: no session, every request carries what
it needs (the token, the arguments, a sealed cursor). So any replica can answer any request,
and scaling out is starting more processes behind something that spreads the connections.
Here that's uvicorn's own workers: N processes sharing one port, the kernel handing each new
connection to one of them. A load balancer in front of N containers works the same way.

What has to change when there's more than one:

    pools          each replica opens its own. Replicas x pool must fit the gateway login's
                   connection limit, so the pool is a share of MCP_PG_BUDGET, not a constant
    limits         per-caller limits move to Valkey (shared_limits.py), or each replica
                   enforces its own and every caller gets N times the rate
    cache          optional, inside the policy (cache.py): local per replica, or shared
    host facts     the policy's "is this host visible to the caller?" lookups are cached per
                   persona for a minute (Unit 7 did one CMDB query per log question)
    health         /healthz (the process is up) and /readyz (its pools and Valkey answer), for
                   whatever starts and balances the replicas

    python examples/09/scaled_server.py --kind siem --port 8728 --workers 3

Settings are environment variables, as in Unit 8, plus:

    MCP_WORKERS=1           replicas (uvicorn workers) on this port
    MCP_PG_BUDGET=20        Postgres connections all replicas of this server may hold together
    MCP_SHARED_LIMITS=1     limits in Valkey (0: each replica keeps its own, as in Unit 8)
    MCP_CACHE=off           off | local | shared | unsafe (the wrong way, for the lab)
    MCP_CACHE_TTL=60
    MCP_ROUND_NOW=60        round "now" down to the minute (Unit 4's windows.py)
    MCP_PUBLIC_URL          the URL callers use (tokens' audience), when it isn't host:port
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
sys.path[:0] = [str(HERE), str(EXAMPLES / "08"), str(EXAMPLES / "07"), str(EXAMPLES)]


def _flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).lower() not in ("0", "false", "no", "off")


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = sys.modules[name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pool_share(budget: int, workers: int) -> int:
    """Each replica's Postgres pool: an equal share of the budget, at least 2."""
    return max(2, budget // max(1, workers))


def check_budget(workers: int) -> None:
    """Fail at start, with a reason, if the replicas' pools can't fit the gateway login's
    connection limit. Without this, the first replicas fill their pools, the rest can't, and
    the process manager restarts them forever while the survivors report ready."""
    import psycopg

    from common_mcp import GATEWAY, _pg_params

    budget = int(os.environ.get("MCP_PG_BUDGET", 20))
    need = pool_share(budget, workers) * workers
    with psycopg.connect(**_pg_params(GATEWAY)) as conn:
        limit = conn.execute("SELECT rolconnlimit FROM pg_roles WHERE rolname = current_user"
                             ).fetchone()[0]
    if limit >= 0 and need > limit:
        raise SystemExit(f"{workers} replicas x {pool_share(budget, workers)} connections = "
                         f"{need}, but {GATEWAY} may open {limit} in all (and the other "
                         "server shares them). Lower MCP_PG_BUDGET.")


def ttl_cached(fn, ttl: float = 60.0, size: int = 4096):
    """Cache a one-argument fact per persona for `ttl` seconds."""
    from common_mcp import persona

    seen: dict = {}

    @functools.wraps(fn)
    def cached(arg):
        key, now = (persona(), arg), time.monotonic()
        hit = seen.get(key)
        if hit and hit[0] > now:
            return hit[1]
        if len(seen) >= size:
            seen.clear()
        value = fn(arg)
        seen[key] = (now + ttl, value)
        return value
    return cached


def transport_security(public: str | None):
    """Bound to 127.0.0.1, the SDK refuses requests whose Host header isn't local (DNS
    rebinding protection). Bound to 0.0.0.0 it checks nothing, so name the public host."""
    if not public:
        return None
    from urllib.parse import urlsplit

    from mcp.server.transport_security import TransportSecuritySettings

    url = urlsplit(public)
    return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                     allowed_hosts=[url.netloc],
                                     allowed_origins=[f"{url.scheme}://{url.netloc}"])


class Health:
    """ASGI: /healthz and /readyz answered here, everything else passed to the MCP app."""

    def __init__(self, app, ready):
        self.app, self.ready = app, ready

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if path not in ("/healthz", "/readyz"):
            return await self.app(scope, receive, send)
        ok, detail = (True, {}) if path == "/healthz" else await self.ready()
        body = json.dumps({"ok": ok, "pid": os.getpid(), **detail}).encode()
        await send({"type": "http.response.start", "status": 200 if ok else 503,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def create_app():
    """Build one replica. uvicorn calls this in each worker process, so every replica opens its
    own pools and Valkey connection after the fork, never before."""
    import common_mcp
    import shared_limits
    from common_mcp import load_env
    from limits import Settings

    load_env()
    kind = os.environ["MCP_KIND"]
    host, port = os.environ.get("MCP_HOST", "127.0.0.1"), int(os.environ["MCP_PORT"])
    workers = int(os.environ.get("MCP_WORKERS", 1))
    envelope = _load("unit8_envelope", EXAMPLES / "08" / "envelope_server.py")
    gateway = envelope.gateway
    cache_mod = _load("unit9_cache", HERE / "cache.py")
    gateway.hold_only_my_secret()

    pg_size = pool_share(int(os.environ.get("MCP_PG_BUDGET", 20)), workers)
    if _flag("MCP_POOL"):
        common_mcp.use_pools(pg_size=pg_size, pg_wait=float(os.environ.get("MCP_PG_WAIT", 2)),
                             os_size=int(os.environ.get("MCP_OS_POOL", 10)),
                             search_budget=os.environ.get("MCP_SEARCH_BUDGET", "10s") or None)
    project = os.environ.get("COMPOSE_PROJECT_NAME", "creating-mcps")
    state = common_mcp.state_client(asynchronous=True)
    if envelope.LIMITER is not None and _flag("MCP_SHARED_LIMITS"):
        envelope.LIMITER = shared_limits.SharedLimiter(Settings.from_env(), state,
                                                       f"{project}:{kind}:limits")
    gateway.host_visible = ttl_cached(gateway.host_visible)
    cache = cache_mod.from_env(state, f"{project}:{kind}:cache")
    if isinstance(cache, cache_mod.ResultCache):
        gateway.RESULTS = cache

    # The audience tokens are minted for is the URL callers use, which behind a load balancer
    # or in a container is not the address this process binds to.
    public = os.environ.get("MCP_PUBLIC_URL")
    resource = public or f"http://{host}:{port}/mcp"
    mcp = gateway.build(kind, resource)
    mcp.middleware.append(envelope.envelope)
    if isinstance(cache, cache_mod.UnsafeCache):
        mcp.middleware.append(cache.middleware)      # after identity, in front of the policy
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host=host,
                                  transport_security=transport_security(public))
    caps = envelope.tool_caps()
    if caps:
        app = envelope.ToolCaps(app, caps)

    async def ready():
        detail = {"kind": kind, "pg_pool": common_mcp.pool_stats().get("pool_size")}
        try:
            await state.ping()
            detail["valkey"] = "ok"
        except Exception as e:  # noqa: BLE001
            return False, {**detail, "valkey": type(e).__name__}
        return True, detail

    print(json.dumps({"operation": "replica.start", "kind": kind, "pid": os.getpid(),
                      "workers": workers, "pg_pool": pg_size if _flag("MCP_POOL") else None,
                      "limits": type(envelope.LIMITER).__name__ if envelope.LIMITER else None,
                      "cache": os.environ.get("MCP_CACHE", "off"),
                      "round_now": os.environ.get("MCP_ROUND_NOW", "0")}),
          file=sys.stderr, flush=True)
    return Health(app, ready)


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Unit 9: the envelope server, N replicas.")
    parser.add_argument("--kind", choices=("cmdb", "siem"), required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8727)
    parser.add_argument("--workers", type=int, default=int(os.environ.get("MCP_WORKERS", 1)))
    args = parser.parse_args()
    os.environ.update(MCP_KIND=args.kind, MCP_HOST=args.host, MCP_PORT=str(args.port),
                      MCP_WORKERS=str(args.workers))
    os.environ.setdefault("MCP_ROUND_NOW", "60")
    if _flag("MCP_POOL"):
        from common_mcp import load_env

        load_env()
        check_budget(args.workers)
    uvicorn.run("scaled_server:create_app", factory=True, app_dir=str(HERE), host=args.host,
                port=args.port, workers=args.workers, log_level="warning",
                timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
