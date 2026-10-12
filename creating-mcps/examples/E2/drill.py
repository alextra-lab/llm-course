"""
E2: triage db-prod-01, the host svc_backup pivoted to, through the local systems server in
its sandbox, and check every finding against what was planted (suspect_host.facts()).

    the login        auth_events: svc_backup from jump-prod-02 at 02:46:15, then sudo refused
    the payload      list_processes(suspicious_only): one process, named like a kernel thread,
                     running from /var/tmp/.cache
    its connection   network_connections(remote_only): that process, to the C2 address
    persistence      cron and a systemd unit, both restarting it; nothing else flagged
    the file         file_info: the payload's hash, without reading it
    what was typed   read_file(.bash_history): the download, the staged dump, and the password
                     on the command line redacted
    what stays put   shadow, a private key, .pgpass, a symlink out of the image and `..`:
                     refused, and no secret appears in anything any call returned
    evidence         collect_artifacts: credentials refused before anyone is asked; declined,
                     nothing copied; approved, copied with matching hashes; again, no change
    the walls        the sandbox's probes, from inside: nothing written, nothing reached

    python examples/E2/drill.py                      # sandboxed if the image exists
    python examples/E2/drill.py --require-sandbox    # fail without it (CI)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]
import sandbox  # noqa: E402
import suspect_host  # noqa: E402

SECRET_MARKERS = ("Backup-2026-TRAINING", "PRIVATE KEY", "$y$j9T", "VFJBSU5JTkc")


def plant(workdir: Path | None = None) -> tuple[Path, Path, dict]:
    """The suspect host and an empty evidence store, in a fresh directory."""
    base = Path(workdir or tempfile.mkdtemp(prefix="e2-"))
    base.chmod(0o755)
    root, evidence = base / "db-prod-01", base / "evidence"
    facts = suspect_host.build(root)
    for p in [root, *root.rglob("*")]:     # readable by nobody (65534), inside the sandbox
        if not p.is_symlink():
            p.chmod(p.stat().st_mode | 0o444 | (0o111 if p.is_dir() else 0))
    return root, evidence, facts


async def triage(root: Path, evidence: Path, sandboxed: bool | None = None) -> dict:
    """Every call the drill makes, in order, with what came back and what was asked."""
    from mcp import Client
    from mcp.types import ElicitResult

    params, boxed = sandbox.server(root, evidence, sandboxed)
    answer = {"next": "accept"}
    asked: list[str] = []

    async def on_elicit(_ctx, p):                  # the person, not the model, answers
        asked.append(p.message)
        if answer["next"] == "accept":
            return ElicitResult(action="accept", content={"ticket": "INC-0042"})
        return ElicitResult(action="decline")

    calls: list[dict] = []
    async with Client(params, elicitation_callback=on_elicit) as client:
        tools = (await client.list_tools()).tools

        async def call(tool: str, args: dict | None = None, reply: str = "accept") -> dict:
            answer["next"] = reply
            before, t0 = len(asked), time.monotonic()
            r = await client.call_tool(tool, args or {})
            text = "\n".join(c.text for c in r.content if getattr(c, "text", None))
            data = r.structured_content
            if isinstance(data, dict) and set(data) == {"result"}:
                data = data["result"]               # list results come wrapped
            row = {"tool": tool, "args": args or {}, "error": r.is_error, "text": text,
                   "data": data, "asked": len(asked) - before,
                   "ms": round((time.monotonic() - t0) * 1000)}
            calls.append(row)
            return row

        facts = suspect_host.facts()
        await call("host_overview")
        await call("auth_events", {"user": facts["user"]})
        await call("list_processes", {"suspicious_only": True})
        await call("network_connections", {"remote_only": True})
        await call("persistence")
        await call("file_info", {"path": facts["payload"]})
        await call("read_file", {"path": f"/home/{facts['user']}/.bash_history"})
        for path in [*facts["secrets"], facts["escape"], "/../../../etc/hostname"]:
            await call("read_file", {"path": path})
        await call("collect_artifacts", {"case_id": "INC-0042", "paths": ["/etc/shadow"]})
        wanted = [facts["payload"], *facts["persistence"]]
        await call("collect_artifacts", {"case_id": "INC-0042", "paths": wanted}, "decline")
        stored_after_decline = sorted(p.name for p in evidence.rglob("*"))
        await call("collect_artifacts", {"case_id": "INC-0042", "paths": wanted})
        await call("collect_artifacts", {"case_id": "INC-0042", "paths": wanted})
    return {"sandboxed": boxed, "tools": [{"name": t.name, "read_only": bool(
                t.annotations and t.annotations.read_only_hint)} for t in tools],
            "calls": calls, "facts": facts, "evidence": str(evidence),
            "stored_after_decline": stored_after_decline,
            "stored": sorted(p.name for p in evidence.rglob("*") if p.is_file()),
            "manifest": [json.loads(line) for line in (evidence / "INC-0042" /
                         "manifest.jsonl").read_text().splitlines()]
            if (evidence / "INC-0042" / "manifest.jsonl").exists() else [],
            "walls": sandbox.walls(root) if boxed else []}


def check(run: dict, require_sandbox: bool = False) -> list[tuple[str, bool, str]]:
    f, calls = run["facts"], run["calls"]
    by = {}
    for c in calls:
        by.setdefault(c["tool"], []).append(c)
    out = []

    def row(name, ok, detail):
        out.append((name, bool(ok), detail))

    tools = run["tools"]
    row("every triage tool is read-only; the one write is collection",
        all(t["read_only"] for t in tools if t["name"] != "collect_artifacts")
        and any(t["name"] == "collect_artifacts" and not t["read_only"] for t in tools),
        f"{len(tools)} tools")
    events = by["auth_events"][0]["data"]["events"]
    login = [e for e in events if e["outcome"] == "success" and e["what"].startswith("ssh")]
    row("the login: svc_backup from jump-prod-02 at 02:46:15",
        login and login[0]["at"] == f["login"] and login[0]["from"] == f["from"],
        f"{login[0]['at']} from {login[0]['from']}" if login else "none found")
    row("then a refused sudo", any(e["what"].startswith("sudo") and e["outcome"] == "failure"
                                   for e in events),
        f"{sum(e['what'].startswith('sudo') for e in events)} sudo events")
    procs = by["list_processes"][0]["data"]
    row("one suspicious process: the payload", [p["pid"] for p in procs] == [f["pid"]]
        and procs[0]["exe"] == f["payload"], "; ".join(
            f"{p['pid']} {p['name']} ({', '.join(p['hints'])})" for p in procs))
    conns = [c for c in by["network_connections"][0]["data"] if c["state"] == "ESTABLISHED"]
    row("its connection to the C2 address", len(conns) == 1 and conns[0]["remote"] == f["c2"]
        and conns[0]["pid"] == f["pid"],
        "; ".join(f"{c['remote']} by {c['pid']} {c['process']}" for c in conns))
    flagged = sorted(r["where"] for r in by["persistence"][0]["data"] if r["hints"])
    row("persistence: cron and systemd, nothing else flagged", flagged == sorted(f["persistence"]),
        ", ".join(flagged))
    info = by["file_info"][0]["data"]
    row("the payload's hash, without reading it", info["sha256"] == f["payload_sha256"],
        f"{info['sha256'][:16]}... {info['looks_like']}, {info['size']} bytes")
    hist = by["read_file"][0]
    row("history: the download and the staged dump, the password redacted",
        not hist["error"] and "198.51.100.23" in hist["data"]["content"]
        and "c.sql.gz" in hist["data"]["content"] and hist["data"]["redactions"] >= 1,
        f"{hist['data']['redactions']} redaction(s)")
    refused = by["read_file"][1:]
    row("credentials, a symlink out and `..`: all refused",
        all(c["error"] for c in refused) and len(refused) == len(f["secrets"]) + 2,
        f"{sum(c['error'] for c in refused)} of {len(refused)} refused")
    leaked = [c["tool"] for c in calls if any(m in c["text"] + json.dumps(c["data"])
                                              for m in SECRET_MARKERS)]
    row("no secret in anything any call returned", not leaked, ", ".join(leaked) or "none")
    col = by["collect_artifacts"]
    row("collecting credentials: refused before anyone is asked",
        col[0]["error"] and col[0]["asked"] == 0, col[0]["text"][:80])
    row("declined: asked once, nothing copied", col[1]["error"] and col[1]["asked"] == 1
        and not [n for n in run["stored_after_decline"] if n != "INC-0042"],
        col[1]["text"][:80])
    got = col[2]["data"] or {}
    hashes = {r["path"]: r["sha256"] for r in got.get("files", [])}
    row("approved: copied, with the hashes file_info gives",
        not col[2]["error"] and col[2]["asked"] == 1
        and hashes.get(f["payload"]) == f["payload_sha256"] and len(run["stored"]) == 4,
        f"{len(hashes)} files, ticket {got.get('ticket')}, manifest + {len(run['stored']) - 1}")
    row("collected again: nothing new", not col[3]["error"] and len(run["manifest"]) == 1,
        f"{len(run['manifest'])} manifest entry")
    if run["sandboxed"]:
        opened = [w for w in run["walls"] if not w["held"]]
        row("the sandbox's walls hold, from inside", not opened,
            f"{len(run['walls'])} probes" + (": " + ", ".join(w["tried"] for w in opened)
                                             if opened else ""))
    else:
        row("ran in the sandbox", not require_sandbox,
            f"no {sandbox.IMAGE} image: ran as a plain process, which is no sandbox")
    return out


async def run_all(sandboxed: bool | None = None) -> dict:
    root, evidence, _ = plant()
    return await triage(root, evidence, sandboxed)


def main() -> None:
    parser = argparse.ArgumentParser(description="E2: triage the pivot host, in the sandbox.")
    parser.add_argument("--require-sandbox", action="store_true")
    parser.add_argument("--no-sandbox", action="store_true", help="a plain process (no docker)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    run = asyncio.run(run_all(False if args.no_sandbox else None))
    rows = check(run, args.require_sandbox)
    if args.json:
        print(json.dumps({"run": run, "checks": rows}, indent=1, default=str))
    else:
        print(f"sandboxed: {run['sandboxed']}")
        for name, ok, detail in rows:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    sys.exit(0 if all(ok for _, ok, _ in rows) else 1)


if __name__ == "__main__":
    main()
