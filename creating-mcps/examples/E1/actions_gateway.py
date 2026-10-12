"""
E1: the SOC gateway, now able to act. Unit 12's gateway proposed; this one carries out the step,
through two backend APIs (an EDR and a ticketing system; backends.py stands in for both).

    isolate_host(incident_id, host, proposal_id, emergency)   cut a host off the network
    release_host(incident_id, host)                           the undo
    action_status(host)                                       what the EDR says, read-only

plus every Unit 12 tool and prompt, unchanged. What stands between a model and the EDR, in order:

  1  grants        the policy's (Unit 7): soc-l1 isn't granted the action tools, so it doesn't
                   see them. soc-l3 is.
  2  prechecks     before anyone is asked: the proposal id must be the one Unit 12's
                   propose_containment gave for this incident, action and host (so a step is
                   proposed before it's done); the incident must be active; the caller must be
                   able to see the host; and a production host is changed only in the change
                   window, or as an emergency change for a high-severity incident. A host that's
                   already isolated is answered from the EDR's state: nothing to approve.
  3  approval      the policy's "writes-need-approval" rule: these tools aren't read-only, so
                   the user confirms with a ticket (Unit 7's elicitation). Declining runs nothing.
  4  the call      through resilience.Backend: timeouts, retries with backoff, a circuit
                   breaker, and an Idempotency-Key on every POST, so a retry never acts twice.
  5  the wait      isolating takes the EDR a while. A client that declared the Tasks extension
                   gets a task at once and polls it (tasks_ext.py); one that didn't simply waits.

Every action opens a change record in the ticketing system and comments on it when the EDR has
finished, so the change, the incident, the approval ticket and the EDR action name each other.

    python examples/E1/backends.py --port 8750 &
    python examples/E1/actions_gateway.py --port 8760

Settings: MCP_BACKENDS (http://127.0.0.1:8750), MCP_CHANGE_WINDOW ("22:00-05:00", UTC),
MCP_CLOCK (pin "now", for drills), MCP_TASK_AFTER (1.0 s), MCP_EDR_WAIT (60 s),
MCP_BREAKER_COOLDOWN (10 s).
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import functools
import hashlib
import importlib.util
import inspect
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE.parent
sys.path[:0] = [str(HERE), str(EXAMPLES / "12"), str(EXAMPLES / "07"), str(EXAMPLES / "04"),
                str(EXAMPLES)]

from resilience import Backend, BackendError, CircuitBreaker, idempotency_key  # noqa: E402
from tasks_ext import Tasks  # noqa: E402

BACKENDS = os.environ.get("MCP_BACKENDS", "http://127.0.0.1:8750")
WINDOW = os.environ.get("MCP_CHANGE_WINDOW", "22:00-05:00")
EDR_WAIT = float(os.environ.get("MCP_EDR_WAIT", 60))
COOLDOWN = float(os.environ.get("MCP_BREAKER_COOLDOWN", 10))

# One client per backend, shared by every request: the breaker has to see every failure.
EDR = Backend("EDR", BACKENDS, breaker=CircuitBreaker(threshold=3, cooldown=COOLDOWN))
TICKETS = Backend("ticketing", BACKENDS, breaker=CircuitBreaker(threshold=3, cooldown=COOLDOWN))

APPROVED: contextvars.ContextVar[str | None] = contextvars.ContextVar("approved", default=None)

INSTRUCTIONS_ADD = (
    " isolate_host carries out an isolate_host proposal once the user approves it with a ticket; "
    "release_host undoes it; action_status says what the EDR reports. Propose first.")

ACTION = {"read_only_hint": False, "destructive_hint": True, "idempotent_hint": True,
          "open_world_hint": True}
STATUS = {"read_only_hint": True, "destructive_hint": False, "idempotent_hint": True,
          "open_world_hint": True}


class Done(TypedDict):
    status: str
    host: str
    incident_id: str
    action_id: str | None
    change_id: str | None
    approved_with: str | None
    emergency: bool
    took_s: float
    attempts: dict
    note: str


class HostState(TypedDict):
    host: str
    isolated: bool
    by_action: str | None
    last_action: str | None
    pending: list[str]


# =========================================================================================
# The change window
# =========================================================================================

def now() -> datetime:
    pinned = os.environ.get("MCP_CLOCK")
    if pinned:
        return datetime.fromisoformat(pinned.replace("Z", "+00:00"))
    return datetime.now(UTC)


def in_window(at: datetime, window: str = WINDOW) -> bool:
    """`window` is "HH:MM-HH:MM" in UTC; it may run past midnight ("22:00-05:00")."""
    start, end = (int(h) * 60 + int(m) for h, m in (part.split(":") for part in window.split("-")))
    minute = at.hour * 60 + at.minute
    return start <= minute < end if start < end else (minute >= start or minute < end)


def proposal_id(incident_id: str, action: str, target: str) -> str:
    """The id Unit 12's propose_containment gives this step (same inputs, same hash)."""
    digest = hashlib.sha256(f"{incident_id}|{action}|{target}".encode()).hexdigest()
    return f"PRP-{digest[:8]}"


# =========================================================================================
# The tools
# =========================================================================================

def make_actions(cmdb: dict, siem: dict, ToolError, log_note):
    """The three tools, and the prechecks that run before the user is asked anything."""

    def refuse(e: BackendError):
        log_note(backend=e.backend, maybe_done=e.maybe_done)
        raise ToolError(str(e)) from None

    def host_state(host: str) -> HostState:
        try:
            return EDR.request("GET", f"/edr/hosts/{host}")
        except BackendError as e:
            refuse(e)

    def precheck_isolate(incident_id: str, host: str, proposal_id_: str, emergency: bool):
        incident = siem["get_incident"](incident_id)
        if incident["status"] not in ("open", "in_progress"):
            raise ToolError(f"{incident['id']} is {incident['status']}: hosts are isolated for "
                            "open or in-progress incidents only.")
        want = proposal_id(incident["id"], "isolate_host", host)
        if proposal_id_ != want:
            raise ToolError(f"{proposal_id_!r} isn't the proposal for isolating {host} in "
                            f"{incident['id']}. Propose it first with propose_containment and "
                            "pass the proposal_id it returns.")
        asset = cmdb["get_asset"](host)              # ToolError if the caller can't see it
        if asset["environment"] == "prod" and not in_window(now()):
            if not emergency:
                raise ToolError(
                    f"{host} is a production host and it's {now():%H:%M} UTC, outside the "
                    f"change window ({WINDOW} UTC). Wait for the window, or, if this can't "
                    "wait, call again with emergency=true: it's recorded as an emergency "
                    "change. Nothing was done and nobody was asked.")
            if incident["severity"] != "high":
                raise ToolError(f"an emergency change needs a high-severity incident; "
                                f"{incident['id']} is {incident['severity']}.")
        state = host_state(host)
        if state["isolated"]:                        # already done: say so, ask nobody
            return _done("already isolated: nothing to do", host, incident["id"],
                         state["by_action"], None, None, emergency, 0.0, {}, "")
        if state["pending"]:
            raise ToolError(f"the EDR is still working on {', '.join(state['pending'])} for "
                            f"{host}. Check action_status before acting again.")
        return None

    def precheck_release(incident_id: str, host: str):
        incident = siem["get_incident"](incident_id)
        cmdb["get_asset"](host)
        state = host_state(host)
        if not state["isolated"] and not state["pending"]:
            return _done("not isolated: nothing to do", host, incident["id"], None, None,
                         None, False, 0.0, {}, "")
        if state["pending"]:
            raise ToolError(f"the EDR is still working on {', '.join(state['pending'])} for "
                            f"{host}. Check action_status before acting again.")
        return None

    def carry_out(kind: str, incident_id: str, host: str, reason: str, emergency: bool) -> Done:
        """Open the change, ask the EDR, wait for it, record the outcome. Each POST's key
        names the intent against the host's last finished action: a retry of this call is the
        same request, but isolating again after a release is a new one."""
        t0 = time.monotonic()
        ticket, who = APPROVED.get(), _who()
        state = host_state(host)
        base = (incident_id, kind, host, ticket or "-", state["last_action"] or "none")
        before = {b.name: len(b.calls) for b in (EDR, TICKETS)}
        try:
            change = TICKETS.request("POST", "/tickets", key=idempotency_key(*base, "change"),
                                     json={"title": f"{kind} {host} for {incident_id}"
                                                    + (" (emergency)" if emergency else ""),
                                           "incident_id": incident_id, "approved_with": ticket,
                                           "requested_by": who, "emergency": emergency,
                                           "reason": reason})
        except BackendError as e:
            refuse(e)
        try:
            started = EDR.request("POST", f"/edr/hosts/{host}/{kind}",
                                  key=idempotency_key(*base, "edr"),
                                  json={"ticket": ticket, "change": change["ticket_id"],
                                        "requested_by": who})
        except BackendError as e:
            log_note(change=change["ticket_id"])
            if e.maybe_done:
                raise ToolError(f"{e} The change record is {change['ticket_id']}. Call "
                                f"action_status for {host} before anything else; calling "
                                f"{kind}_host again is safe (it's the same request).") from None
            refuse(e)
        action = started
        deadline = time.monotonic() + EDR_WAIT
        while action["status"] == "pending":         # the slow part: why it becomes a task
            if time.monotonic() > deadline:
                raise ToolError(f"the EDR accepted {action['action_id']} but hasn't finished "
                                f"after {EDR_WAIT:g} s. It may still finish: check "
                                "action_status before acting again.")
            time.sleep(0.5)
            try:
                action = EDR.request("GET", f"/edr/actions/{started['action_id']}")
            except BackendError:
                pass                                 # keep waiting; the action has its id
        note = ""
        try:
            TICKETS.request("POST", f"/tickets/{change['ticket_id']}/comments",
                            key=idempotency_key(*base, "done"),
                            json={"requested_by": who, "text": f"{action['action_id']} "
                                  f"{kind} {host}: {action['status']}"})
        except BackendError as e:                    # the EDR acted; only the record lags
            note = (f"{host} was {kind}d, but the change record wasn't updated ({e}). "
                    f"Add the outcome to {change['ticket_id']} by hand.")
        posts = {b.name: [c for c in b.calls[before[b.name]:] if c["method"] == "POST"]
                 for b in (EDR, TICKETS)}
        attempts = {name: {"posts": len(cs), "retries": len(cs) - len({c["path"] for c in cs}),
                           "replays": sum(c["replay"] for c in cs)}
                    for name, cs in posts.items()}
        log_note(action_id=action["action_id"], change=change["ticket_id"], attempts=attempts)
        return _done(f"{kind}d" if kind == "release" else "isolated", host, incident_id,
                     action["action_id"], change["ticket_id"], ticket, emergency,
                     round(time.monotonic() - t0, 2), attempts, note)

    def isolate_host(incident_id: str, host: str, proposal_id: str,
                     emergency: bool = False) -> Done:
        """Isolate a host from the network in the EDR, carrying out an isolate_host proposal
        from propose_containment (pass its proposal_id). The user approves it with a ticket.
        A production host is isolated in the change window (22:00-05:00 UTC) or, for a
        high-severity incident that can't wait, with emergency=true. Waits until the EDR has
        finished; release_host undoes it."""
        incident_id = incident_id.strip().upper()
        return carry_out("isolate", incident_id, host.strip(),
                         f"proposal {proposal_id}", emergency)

    def release_host(incident_id: str, host: str) -> Done:
        """Release an isolated host back onto the network: the undo of isolate_host. The user
        approves it with a ticket. Allowed at any time."""
        return carry_out("release", incident_id.strip().upper(), host.strip(), "release",
                         False)

    def action_status(host: str) -> HostState:
        """What the EDR reports for a host: isolated or not, by which action, and any action
        still running. Read-only; use it whenever an action's outcome is unclear."""
        return host_state(host.strip())

    prechecks = {"isolate_host": lambda incident_id, host, proposal_id, emergency=False:
                 precheck_isolate(incident_id.strip().upper(), host.strip(), proposal_id,
                                  emergency),
                 "release_host": lambda incident_id, host:
                 precheck_release(incident_id.strip().upper(), host.strip())}
    return {"isolate_host": isolate_host, "release_host": release_host,
            "action_status": action_status}, prechecks


def _done(status, host, incident_id, action_id, change_id, approved, emergency, took,
          attempts, note) -> Done:
    return Done(status=status, host=host, incident_id=incident_id, action_id=action_id,
                change_id=change_id, approved_with=approved, emergency=emergency, took_s=took,
                attempts=attempts, note=note)


def _who() -> str | None:
    gateway = sys.modules.get("unit7_gateway")
    caller = gateway.current_caller() if gateway else None
    return caller.sub if caller else None


def with_prechecks(guarded, precheck):
    """Run `precheck` (as the caller, off the event loop) before the policy's approval round.
    If it returns a result, that's the answer and nobody is asked."""
    sig = inspect.signature(guarded)

    @functools.wraps(guarded)
    async def checked(*args, ctx, **kwargs):
        early = await asyncio.to_thread(precheck, *args, **kwargs)
        if early is not None:
            return early
        return await guarded(*args, ctx=ctx, **kwargs)

    checked.__signature__ = sig
    checked.__annotations__ = guarded.__annotations__
    return checked


# =========================================================================================
# The server
# =========================================================================================

def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = sys.modules[name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def remember_approval(gateway) -> None:
    """The policy layer checks the ticket; keep it, so the action can cite it."""
    original = gateway._ticket
    if getattr(original, "remembers", False):
        return

    def ticket(ctx):
        answer = original(ctx)
        APPROVED.set(answer)
        return answer

    ticket.remembers = True
    gateway._ticket = ticket


def build(gateway, resource: str | None, tasks: Tasks | None = None):
    """Unit 12's SOC gateway plus the action tools, with the Tasks extension."""
    from mcp.server.auth.settings import AuthSettings
    from mcp.server.caching import CacheHint
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    from common_mcp import joinable_log, log_note

    soc = _load("unit12_soc", EXAMPLES / "12" / "soc_gateway.py")
    remember_approval(gateway)
    cmdb, _ = gateway.tools_for("cmdb")
    siem, _ = gateway.tools_for("siem")
    options = {}
    if resource:
        options = dict(token_verifier=gateway.identity.JwtVerifier(audience=resource),
                       auth=AuthSettings(issuer_url=gateway.idp.ISSUER,
                                         resource_server_url=resource,
                                         required_scopes=["mcp:tools"],
                                         validate_token_resource=True))
    tasks = tasks or Tasks(after_s=float(os.environ.get("MCP_TASK_AFTER", 1.0)),
                           tools={"isolate_host", "release_host"})
    mcp = MCPServer("soc-actions", version="1.1.0",
                    instructions=soc.INSTRUCTIONS.replace(
                        " nothing here changes anything.", "") + INSTRUCTIONS_ADD,
                    cache_hints={"tools/list": CacheHint(ttl_ms=60_000, scope="private")},
                    extensions=[tasks], **options)
    mcp.middleware.append(joinable_log("soc_actions"))
    mcp.middleware.append(gateway.identity_layer)
    reads = {**cmdb, **siem, "propose_containment": soc.make_propose(cmdb, siem, ToolError)}
    for name, fn in reads.items():
        mcp.add_tool(gateway.with_policy(name, fn, gateway.READ_ONLY), name=name,
                     annotations=gateway.READ_ONLY)
    actions, prechecks = make_actions(cmdb, siem, ToolError, log_note)
    for name, fn in actions.items():
        ann = ToolAnnotations(**(STATUS if name == "action_status" else ACTION))
        tool = gateway.with_policy(name, fn, ann)
        if name in prechecks:
            tool = with_prechecks(tool, prechecks[name])
        mcp.add_tool(tool, name=name, annotations=ann)
    mcp.prompt()(soc.triage_alert)
    mcp.tasks = tasks                                  # for drills: the server's task table
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="E1: the SOC gateway, able to act.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8760)
    args = parser.parse_args()
    from common_mcp import load_env

    load_env()
    gateway = _load("unit7_gateway", EXAMPLES / "07" / "gateway_server.py")
    gateway.hold_only_my_secret()
    resource = f"http://{args.host}:{args.port}/mcp"
    # One process: tasks live in its memory (a replica set would keep them in Valkey).
    build(gateway, resource).run("streamable-http", host=args.host, port=args.port,
                                 stateless_http=True, json_response=True)


if __name__ == "__main__":
    main()
