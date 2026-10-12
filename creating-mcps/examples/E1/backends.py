"""
E1: two backend APIs the SOC gateway can act through, as small HTTP services. Stand-ins for a
real EDR and a real ticketing system, with the behaviours that make acting hard:

    EDR          POST /edr/hosts/{host}/isolate    202 {action_id, status: "pending"}; done
                                                   after MCP_EDR_SECONDS (default 3)
                 POST /edr/hosts/{host}/release    the undo, same shape
                 GET  /edr/actions/{action_id}     pending | done
                 GET  /edr/hosts/{host}            isolated or not, and the last action that
                                                   finished on it
    ticketing    POST /tickets                     201 {ticket_id}
                 POST /tickets/{id}/comments       201
                 GET  /tickets/{id}

Every POST needs an `Idempotency-Key` header. The same key with the same body returns the first
answer again and does nothing twice; the same key with a different body is a 409. That's what
makes a retry after a timeout safe: the client can't tell "lost on the way there" from "lost on
the way back", and with a key it doesn't need to.

Faults, for the drills (POST /_faults with JSON; GET /_ledger lists every side effect):

    {"fail_rate": 0.5}     that share of requests answer 503 before doing anything
    {"lose_reply": 0.3}    that share do the work and then answer 503 (the reply is "lost")
    {"lose_first_reply": true}   every POST's first answer is lost, after the work is done
    {"latency_ms": 800}    every request waits this long first
    {"down": true}         every request answers 503

    python examples/E1/backends.py --port 8750
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import time
import uuid

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

EDR_SECONDS = float(os.environ.get("MCP_EDR_SECONDS", 3))

FAULTS: dict = {}
LEDGER: list[dict] = []                    # every side effect, in order: what really happened
KEYS: dict[str, tuple[str, int, dict]] = {}   # idempotency key -> (body digest, status, reply)
ACTIONS: dict[str, dict] = {}
ISOLATED: dict[str, str] = {}              # host -> action_id that isolated it
LAST: dict[str, str] = {}                  # host -> the last action that finished on it
TICKETS: dict[str, dict] = {}
RNG = random.Random(int(os.environ.get("MCP_FAULT_SEED", 12)))


def _unavailable() -> JSONResponse:
    return JSONResponse({"error": "service unavailable"}, status_code=503,
                        headers={"Retry-After": "1"})


async def _faults() -> JSONResponse | None:
    if FAULTS.get("latency_ms"):
        await asyncio.sleep(FAULTS["latency_ms"] / 1000)
    if FAULTS.get("down") or (FAULTS.get("fail_rate") and RNG.random() < FAULTS["fail_rate"]):
        return _unavailable()
    return None


def idempotent(handler):
    """Run a POST at most once per Idempotency-Key; replay its answer after that."""

    async def wrapped(request: Request) -> JSONResponse:
        if (refused := await _faults()) is not None:
            return refused
        key = request.headers.get("idempotency-key")
        if not key:
            return JSONResponse({"error": "Idempotency-Key header required"}, status_code=400)
        body = await request.body()
        digest = hashlib.sha256(request.url.path.encode() + b"|" + body).hexdigest()
        if key in KEYS:
            seen, status, reply = KEYS[key]
            if seen != digest:
                return JSONResponse({"error": "this Idempotency-Key was used for a different "
                                     "request"}, status_code=409)
            return JSONResponse(reply, status_code=status, headers={"Idempotent-Replay": "true"})
        status, reply = await handler(request, json.loads(body or b"{}"))
        KEYS[key] = (digest, status, reply)
        if FAULTS.get("lose_first_reply") or (FAULTS.get("lose_reply")
                                               and RNG.random() < FAULTS["lose_reply"]):
            return _unavailable()          # the work is done; the caller doesn't know it
        return JSONResponse(reply, status_code=status)

    return wrapped


def _start(kind: str, host: str, body: dict) -> tuple[int, dict]:
    action_id = f"edr-{uuid.uuid4().hex[:10]}"
    ACTIONS[action_id] = {"action_id": action_id, "kind": kind, "host": host,
                          "status": "pending", "requested_by": body.get("requested_by"),
                          "ticket": body.get("ticket"), "done_at": time.time() + EDR_SECONDS}
    LEDGER.append({"at": time.time(), "effect": kind, "host": host, "action_id": action_id,
                   "ticket": body.get("ticket")})
    return 202, {k: v for k, v in ACTIONS[action_id].items() if k != "done_at"}


@idempotent
async def isolate(request: Request, body: dict):
    return _start("isolate", request.path_params["host"], body)


@idempotent
async def release(request: Request, body: dict):
    return _start("release", request.path_params["host"], body)


def _settle(action: dict) -> dict:
    if action["status"] == "pending" and time.time() >= action["done_at"]:
        action["status"] = "done"
        LAST[action["host"]] = action["action_id"]
        if action["kind"] == "isolate":
            ISOLATED[action["host"]] = action["action_id"]
        else:
            ISOLATED.pop(action["host"], None)
    return {k: v for k, v in action.items() if k != "done_at"}


async def action(request: Request) -> JSONResponse:
    if (refused := await _faults()) is not None:
        return refused
    found = ACTIONS.get(request.path_params["action_id"])
    if found is None:
        return JSONResponse({"error": "no such action"}, status_code=404)
    return JSONResponse(_settle(found))


async def host(request: Request) -> JSONResponse:
    if (refused := await _faults()) is not None:
        return refused
    for a in ACTIONS.values():
        _settle(a)
    name = request.path_params["host"]
    pending = [a["action_id"] for a in ACTIONS.values()
               if a["host"] == name and a["status"] == "pending"]
    return JSONResponse({"host": name, "isolated": name in ISOLATED,
                         "by_action": ISOLATED.get(name), "last_action": LAST.get(name),
                         "pending": pending})


@idempotent
async def open_ticket(request: Request, body: dict):
    ticket_id = f"CHG-{len(TICKETS) + 1001:04d}"
    TICKETS[ticket_id] = {"ticket_id": ticket_id, "title": body.get("title"),
                          "incident_id": body.get("incident_id"), "comments": [],
                          "opened_by": body.get("requested_by")}
    LEDGER.append({"at": time.time(), "effect": "open_ticket", "ticket": ticket_id})
    return 201, TICKETS[ticket_id]


@idempotent
async def comment(request: Request, body: dict):
    ticket = TICKETS.get(request.path_params["ticket_id"])
    if ticket is None:
        return 404, {"error": "no such ticket"}
    ticket["comments"].append({"by": body.get("requested_by"), "text": body.get("text")})
    LEDGER.append({"at": time.time(), "effect": "comment", "ticket": ticket["ticket_id"]})
    return 201, {"ticket_id": ticket["ticket_id"], "comments": len(ticket["comments"])}


async def get_ticket(request: Request) -> JSONResponse:
    if (refused := await _faults()) is not None:
        return refused
    ticket = TICKETS.get(request.path_params["ticket_id"])
    return JSONResponse(ticket or {"error": "no such ticket"}, status_code=200 if ticket else 404)


async def set_faults(request: Request) -> JSONResponse:
    FAULTS.clear()
    FAULTS.update(await request.json())
    return JSONResponse(FAULTS)


async def ledger(request: Request) -> JSONResponse:
    return JSONResponse(LEDGER)


async def reset(request: Request) -> JSONResponse:
    for store in (FAULTS, KEYS, ACTIONS, ISOLATED, LAST, TICKETS):
        store.clear()
    LEDGER.clear()
    return JSONResponse({"reset": True})


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


app = Starlette(routes=[
    Route("/edr/hosts/{host}/isolate", isolate, methods=["POST"]),
    Route("/edr/hosts/{host}/release", release, methods=["POST"]),
    Route("/edr/hosts/{host}", host),
    Route("/edr/actions/{action_id}", action),
    Route("/tickets", open_ticket, methods=["POST"]),
    Route("/tickets/{ticket_id}/comments", comment, methods=["POST"]),
    Route("/tickets/{ticket_id}", get_ticket),
    Route("/_faults", set_faults, methods=["POST"]),
    Route("/_ledger", ledger),
    Route("/_reset", reset, methods=["POST"]),
    Route("/healthz", health),
])


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="E1: stand-in EDR and ticketing APIs.")
    parser.add_argument("--port", type=int, default=8750)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
