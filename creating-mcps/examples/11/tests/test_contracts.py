"""Contract tests: what each caller's tools/list says, compared with a reviewed snapshot.

A tool's name, description and schemas are the server's API, and the model is its client. A
renamed argument, a reworded description or a tool that appears for the wrong group changes
what agents do, without failing a single unit test. So the listing each group sees is
snapshotted in contracts/, reviewed like code, and any difference fails here with what changed.

When the change is intended, update the snapshots and commit them with it:

    UPDATE_CONTRACTS=1 pytest examples/11/tests/test_contracts.py
"""

from __future__ import annotations

import json
import os

import pytest
from conftest import UNIT, as_caller, run

CONTRACTS = UNIT / "contracts"
KEYS = ("name", "description", "inputSchema", "outputSchema", "annotations")
CASES = [("cmdb", "soc-l1"), ("cmdb", "soc-l3"), ("cmdb", "audit"), ("cmdb", "contractors"),
         ("siem", "soc-l1"), ("siem", "soc-l3")]


async def listing(gateway, kind: str, group: str) -> dict:
    from mcp import Client

    as_caller(gateway, f"contract-{group}", [group])
    async with Client(gateway.build(kind, None)) as client:
        tools = (await client.list_tools()).tools
        instructions = client.instructions
    return {"instructions": instructions, "tools": sorted(
        ({k: v for k, v in t.model_dump(by_alias=True, exclude_none=True).items() if k in KEYS}
         for t in tools), key=lambda t: t["name"])}


def differences(old: dict, new: dict) -> list[str]:
    """What changed, in words a reviewer can act on."""
    out = []
    if old.get("instructions") != new.get("instructions"):
        out.append("the server's instructions changed")
    before = {t["name"]: t for t in old["tools"]}
    after = {t["name"]: t for t in new["tools"]}
    out += [f"tool removed: {n}" for n in sorted(before.keys() - after.keys())]
    out += [f"tool added: {n}" for n in sorted(after.keys() - before.keys())]
    for name in sorted(before.keys() & after.keys()):
        for key in KEYS[1:]:
            a, b = before[name].get(key), after[name].get(key)
            if a == b:
                continue
            if key == "inputSchema":
                pa, pb = (a or {}).get("properties", {}), (b or {}).get("properties", {})
                for p in sorted(pa.keys() - pb.keys()):
                    out.append(f"{name}: argument removed: {p}")
                for p in sorted(pb.keys() - pa.keys()):
                    out.append(f"{name}: argument added: {p}")
                for p in sorted(pa.keys() & pb.keys()):
                    if pa[p] != pb[p]:
                        out.append(f"{name}: argument changed: {p}")
                if (a or {}).get("required") != (b or {}).get("required"):
                    out.append(f"{name}: required arguments changed")
            else:
                out.append(f"{name}: {key} changed")
    return out


@pytest.mark.parametrize(("kind", "group"), CASES)
def test_contract(gateway, kind, group):
    snapshot = CONTRACTS / f"{kind}-{group}.json"
    now = run(listing(gateway, kind, group))
    if os.environ.get("UPDATE_CONTRACTS"):
        CONTRACTS.mkdir(exist_ok=True)
        snapshot.write_text(json.dumps(now, indent=2, sort_keys=True) + "\n")
        pytest.skip(f"wrote {snapshot.name}")
    assert snapshot.exists(), f"no snapshot yet: run with UPDATE_CONTRACTS=1 ({snapshot.name})"
    changed = differences(json.loads(snapshot.read_text()), now)
    assert not changed, f"{kind} as {group}:\n  " + "\n  ".join(changed)


def test_differences_names_what_changed():
    old = {"instructions": "x", "tools": [
        {"name": "get_asset", "description": "One asset.",
         "inputSchema": {"properties": {"hostname": {"type": "string"}},
                         "required": ["hostname"]}},
        {"name": "count_assets", "description": "Count."}]}
    new = {"instructions": "x", "tools": [
        {"name": "get_asset", "description": "One asset by hostname.",
         "inputSchema": {"properties": {"host": {"type": "string"}}, "required": ["host"]}},
        {"name": "get_contact", "description": "Contact."}]}
    assert differences(old, new) == [
        "tool removed: count_assets", "tool added: get_contact",
        "get_asset: description changed", "get_asset: argument removed: hostname",
        "get_asset: argument added: host", "get_asset: required arguments changed"]
