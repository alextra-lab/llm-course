"""The records themselves: no estate needed."""

from __future__ import annotations

from datetime import UTC, datetime

from audit import row_for
from telemetry import stamp, to_span

RECORD = {"session_id": "s-1", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
          "parent_span_id": "00f067aa0ba902b7", "step": 3, "method": "tools/call",
          "name": "get_asset", "protocol_version": "2026-07-28", "caller": "priya",
          "outcome": "ok", "ms": 250.0}


def test_stamp_is_when_the_request_started():
    end = datetime(2026, 10, 12, 9, 0, 0, tzinfo=UTC)
    assert stamp(RECORD, now=end)["@timestamp"] == "2026-10-12T08:59:59.750+00:00"


def test_span_is_named_and_parented_as_the_sdk_does():
    span = to_span(RECORD)
    assert span["name"] == "tools/call get_asset" and span["kind"] == "SERVER"
    assert span["trace_id"] == RECORD["trace_id"]
    assert span["parent_span_id"] == RECORD["parent_span_id"]
    assert span["attributes"]["gen_ai.tool.name"] == "get_asset"
    assert span["status"] == "OK" and "error.type" not in span["attributes"]


def test_error_type_follows_the_sdk():
    assert to_span({**RECORD, "outcome": "tool_error"})["attributes"]["error.type"] == "tool_error"
    assert to_span({**RECORD, "outcome": "error", "code": -32602})["attributes"][
        "error.type"] == "-32602"
    assert to_span({**RECORD, "outcome": "crash", "error": "TimeoutError"})["attributes"][
        "error.type"] == "TimeoutError"
    assert to_span({**RECORD, "outcome": "input_required"})["status"] == "OK"


def test_audit_row_keeps_arguments_not_results():
    row = row_for(RECORD, {"caller": "priya", "persona": "l1_analyst", "verdict": "allow",
                           "rule": "granted", "result_tokens": 91},
                  {"hostname": "jump-prod-02", "note": "x" * 500}, "ok")
    assert (row["caller"], row["tool"], row["verdict"], row["step"]) == (
        "priya", "get_asset", "allow", 3)
    assert row["detail"]["arguments"]["hostname"] == "jump-prod-02"
    assert len(row["detail"]["arguments"]["note"]) == 200
    assert "result_tokens" not in row["detail"]


def test_audit_verdict_when_the_policy_never_ran():
    assert row_for(RECORD, {"busy": "rate"}, {}, "ok")["verdict"] == "busy"
    assert row_for(RECORD, {"gate": "scan"}, {}, "ok")["verdict"] == "busy"
    unjoined = row_for({"trace_id": "t"}, {}, {}, "ok")
    assert (unjoined["session_id"], unjoined["step"], unjoined["caller"],
            unjoined["verdict"]) == ("-", 0, "-", "none")
