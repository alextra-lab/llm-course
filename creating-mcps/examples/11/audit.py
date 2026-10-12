"""
Unit 11: the audit log. One row per tools/call in the CMDB's `audit.tool_calls`, written by the
gateway's own login, which may insert there and do nothing else, and read by the auditor,
who may select and do nothing else.

Telemetry (telemetry.py) is for operators: it may be sampled, dropped under load, rolled over
after a few weeks. The audit log is for the question "who saw what, as whom, and why was it
allowed?", asked months later. So it's a different store with different rules:

    every call      allowed, denied, asked for approval, refused as busy: all of them
    as whom         the verified caller, their groups, the persona the backend saw, the token
    why             the policy's verdict and the rule that made it, and the approval ticket
    which records   the arguments (a hostname, an incident id), never the results
    joinable        session_id, trace_id and step, the same as the telemetry record

The middleware sits just inside the joinable log, outside the identity layer, so it sees
every call that reaches the server, including the ones identity refuses before the policy.
It reads what the inner layers decided from the request's log notes (`current_notes()`).

    MCP_AUDIT=1               write audit rows (the observed server turns it on)
    MCP_AUDIT_FAIL=closed     when the row can't be written, withhold the result (open: return
                              it anyway and say so in the log)

    python examples/11/audit.py --host jump-prod-02     # who looked at a host, as the auditor
    python examples/11/audit.py --trace <trace_id>      # one investigation, step by step
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # creating-mcps/examples/
from common_mcp import (GATEWAY, _pg_params, current_notes, current_record,  # noqa: E402
                        load_env, log_note, pg_connect)

# What goes into `detail`, from the request's log notes. Results never do.
DETAIL_KEYS = ("persona", "groups", "token_id", "rule", "approval", "approved_with", "backend",
               "busy", "gate", "tools_visible")
MAX_ARG = 200        # characters kept per argument value


def _outcome(result) -> str:
    get = result.get if isinstance(result, dict) else lambda k: getattr(
        result, {"isError": "is_error", "resultType": "result_type"}[k], None)
    if get("resultType") == "input_required":
        return "input_required"
    return "tool_error" if get("isError") else "ok"


def row_for(record: dict, notes: dict, arguments: dict, outcome: str) -> dict:
    """The audit row for one call, from the joinable record and what the layers noted."""
    verdict = notes.get("verdict") or ("busy" if notes.get("busy") or notes.get("gate")
                                       else "none")
    return {
        "session_id": record.get("session_id") or "-",     # a caller that sent none: still
        "trace_id": record["trace_id"],                     # recorded, and visibly so
        "step": record.get("step") or 0,
        "caller": notes.get("caller") or "-",
        "tool": record.get("name") or "-",
        "verdict": verdict,
        "detail": {"arguments": {k: str(v)[:MAX_ARG] for k, v in arguments.items()},
                   "outcome": outcome,
                   **{k: notes[k] for k in DETAIL_KEYS if k in notes}},
    }


class AuditWriter:
    """MCP middleware: after each tools/call, one row in audit.tool_calls."""

    def __init__(self, fail: str = "closed", pool_size: int = 2):
        from psycopg_pool import ConnectionPool

        self.fail = fail
        self.written = self.failed = 0
        # Its own small pool, as the gateway itself (no SET ROLE: only mcp_service may write
        # here, and only INSERT). Count it in Unit 9's connection budget.
        self.pool = ConnectionPool(kwargs=_pg_params(GATEWAY, autocommit=True),
                                   min_size=1, max_size=pool_size, timeout=2,
                                   name="audit", open=True)

    def _insert(self, row: dict) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO audit.tool_calls (session_id, trace_id, step, caller, tool, "
                "verdict, detail) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (row["session_id"], row["trace_id"], row["step"], row["caller"], row["tool"],
                 row["verdict"], json.dumps(row["detail"])))

    async def _record(self, params: dict, outcome: str) -> bool:
        row = row_for(current_record() or {"trace_id": "-"}, current_notes() or {},
                      params.get("arguments") or {}, outcome)
        try:
            await asyncio.to_thread(self._insert, row)
        except Exception as e:  # noqa: BLE001
            self.failed += 1
            log_note(audit="failed", audit_error=type(e).__name__)
            return False
        self.written += 1
        log_note(audit="written")
        return True

    async def __call__(self, ctx, call_next):
        if ctx.method != "tools/call":
            return await call_next(ctx)
        params = ctx.params or {}
        try:
            result = await call_next(ctx)
        except Exception:
            await self._record(params, "error")      # the error goes back as it was
            raise
        if await self._record(params, _outcome(result)) or self.fail == "open":
            return result
        # Fail closed: no row, no result.
        return {"resultType": "complete", "isError": True, "content": [{
            "type": "text", "text": "This call couldn't be recorded in the audit log, so its "
            "result is withheld. Try again shortly, or tell the user the audit log is "
            "unavailable."}]}


def from_env() -> AuditWriter | None:
    if os.environ.get("MCP_AUDIT", "0").lower() in ("0", "false", "no", "off"):
        return None
    return AuditWriter(fail=os.environ.get("MCP_AUDIT_FAIL", "closed"))


# =========================================================================================
# The auditor's side: SELECT only
# =========================================================================================

def _rows(sql: str, args: tuple) -> list[dict]:
    with pg_connect("auditor") as conn:
        cur = conn.execute(sql, args)
        cols = [c.name for c in cur.description]
        return [{c: (v.isoformat(timespec="seconds") if hasattr(v, "isoformat") else v)
                 for c, v in zip(cols, r)} for r in cur.fetchall()]


def who_looked_at(host: str, since: str = "7 days") -> list[dict]:
    """Every call whose arguments named this host: who, as whom, allowed or not."""
    return _rows(
        "SELECT at, caller, detail->>'persona' AS persona, tool, verdict, "
        "detail->>'rule' AS rule, detail->>'outcome' AS outcome, session_id "
        "FROM audit.tool_calls WHERE detail->'arguments'->>'hostname' = %s "
        "AND at > now() - %s::interval ORDER BY at", (host, since))


def trace(trace_id: str) -> list[dict]:
    """One investigation, in the order its steps ran."""
    return _rows(
        "SELECT at, step, caller, tool, verdict, detail->>'outcome' AS outcome, "
        "detail->'arguments' AS arguments FROM audit.tool_calls WHERE trace_id = %s "
        "ORDER BY step, at", (trace_id,))


def verdicts(since: str = "1 hour") -> list[dict]:
    """Calls per caller and verdict: the shape of a period, for a first look."""
    return _rows(
        "SELECT caller, verdict, count(*) AS calls FROM audit.tool_calls "
        "WHERE at > now() - %s::interval GROUP BY caller, verdict ORDER BY caller, verdict",
        (since,))


def main() -> None:
    parser = argparse.ArgumentParser(description="Read the audit log, as the auditor.")
    parser.add_argument("--host", help="who looked at this host")
    parser.add_argument("--trace", help="one trace, step by step")
    parser.add_argument("--since", default="7 days")
    args = parser.parse_args()
    load_env()
    rows = (who_looked_at(args.host, args.since) if args.host else
            trace(args.trace) if args.trace else verdicts(args.since))
    for r in rows:
        print(json.dumps(r, default=str))


if __name__ == "__main__":
    main()
