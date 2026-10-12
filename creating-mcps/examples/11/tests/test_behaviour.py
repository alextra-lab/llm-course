"""Behaviour tests: the gateway in memory, against the estate. Each one pins a decision the
earlier units made, so a refactor that quietly undoes it fails here, not in production."""

from __future__ import annotations

import uuid

import pytest
from conftest import as_caller, run

from common_mcp import trace_meta


def _text(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


async def call(mcp, tool: str, args: dict, meta: dict | None = None):
    from mcp import Client

    async with Client(mcp) as client:
        return await client.call_tool(tool, args, meta=meta)


def test_a_tool_you_are_not_granted_looks_like_no_tool(gateway):
    as_caller(gateway, "sam", ["contractors"])
    hidden = run(call(gateway.build("cmdb", None), "get_asset", {"hostname": "jump-prod-02"}))
    as_caller(gateway, "marc", ["soc-l3"])
    missing = run(call(gateway.build("cmdb", None), "get_assets", {"hostname": "jump-prod-02"}))
    assert hidden.is_error and missing.is_error
    assert _text(hidden) == "Unknown tool: get_asset"
    assert _text(missing) == "Unknown tool: get_assets"


def test_a_restricted_hosts_logs_stay_restricted(gateway):
    as_caller(gateway, "priya", ["soc-l1"])
    r = run(call(gateway.build("siem", None), "event_histogram",
                 {"dataset": "syslog", "start": "now-7d", "filters": {"host.name": "db-prod-01"}}))
    assert r.is_error and "restricted-hosts-stay-restricted" in _text(r)


def test_l1_never_sees_contact_details(gateway):
    as_caller(gateway, "priya", ["soc-l1"])
    r = run(call(gateway.build("cmdb", None), "get_contact", {"hostname": "jump-prod-02"}))
    assert r.is_error and "@" not in _text(r)


# ---- the audit log -----------------------------------------------------------------------

def observed(gateway, writer):
    """The CMDB gateway with the audit writer where observed_server.py puts it."""
    mcp = gateway.build("cmdb", None)
    at = next(i for i, m in enumerate(mcp.middleware)
              if getattr(m, "__qualname__", "").startswith("joinable_log"))
    mcp.middleware.insert(at + 1, writer)
    return mcp


@pytest.fixture(scope="module")
def writer(gateway):
    import audit

    w = audit.AuditWriter(fail="closed")
    yield w
    w.pool.close()


def test_every_call_is_an_audit_row_joinable_by_trace(gateway, writer):
    import audit

    as_caller(gateway, "priya", ["soc-l1"])
    session, trace = f"s-test-{uuid.uuid4().hex[:6]}", uuid.uuid4().hex
    run(call(observed(gateway, writer), "get_asset", {"hostname": "jump-prod-02"},
             trace_meta(session, trace, 1)))
    run(call(observed(gateway, writer), "get_contact", {"hostname": "jump-prod-02"},
             trace_meta(session, trace, 2)))
    rows = audit.trace(trace)
    assert [(r["step"], r["caller"], r["tool"], r["verdict"]) for r in rows] == [
        (1, "priya", "get_asset", "allow"), (2, "priya", "get_contact", "deny")]


def test_no_audit_row_no_result(gateway, writer, monkeypatch):
    def broken(row):
        raise ConnectionError("audit store unavailable")

    as_caller(gateway, "marc", ["soc-l3"])
    monkeypatch.setattr(writer, "_insert", broken)
    closed = run(call(observed(gateway, writer), "get_asset", {"hostname": "jump-prod-02"}))
    assert closed.is_error and "audit log" in _text(closed)
    assert "jump-prod-02" not in _text(closed)
    monkeypatch.setattr(writer, "fail", "open")
    opened = run(call(observed(gateway, writer), "get_asset", {"hostname": "jump-prod-02"}))
    assert not opened.is_error and "jump-prod-02" in _text(opened)
