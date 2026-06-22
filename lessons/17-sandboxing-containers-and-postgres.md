---
title: 'Sandboxing II: Containers, Postgres & Production Isolation'
linkTitle: '17. Sandboxing II: Containers, Postgres & Production Isolation'
weight: 17
---

**Goal:** climb the isolation ladder from Section 16. Process limits cap CPU and memory but
leave the filesystem and network open. A **container** closes that gap; a locked-down
**Postgres** role does the same for SQL; and an **audit log** records every execution. You'll
also learn where the stronger tiers — gVisor and Firecracker — fit.

**Where this fits:** this is the "production" tier of the sandboxing you started in Section
16. Tools (Section 14–15) act; portable limits (Section 16) contain runaway resource use;
here we contain *what code can touch*. Everything an agent (Section 23) runs unattended
should sit behind one of these.

> **Mindset:** match the box to the threat. Untrusted *arithmetic* needs a parser; untrusted
> *code* needs a container; untrusted *SQL* needs a read-only, time-boxed role. Over-building
> wastes effort; under-building is the breach.

---

## Run untrusted code in a hardened container

The portable sandbox couldn't really block the network or the filesystem. A container can.
We drive the `docker` CLI directly (no Python Docker library) and lock it down hard.

The whole lesson is in the flags. `work/docker_exec.py`:

```python
import shutil
import subprocess

IMAGE = "python:3.12-slim"


def build_command(code: str, name: str) -> list[str]:
    return [
        "docker", "run", "--rm",          # ephemeral: deleted when it exits
        "--name", name,                   # a handle so we can kill it on timeout
        "--network", "none",              # no network at all
        "--read-only",                    # read-only root filesystem
        "--cap-drop", "ALL",              # drop every Linux capability
        "--security-opt", "no-new-privileges",
        "--pids-limit", "64",             # bound the number of processes
        "--memory", "256m",               # bound RAM
        "--cpus", "1.0",                  # bound CPU so a busy loop can't hog a core
        "--user", "65534:65534",          # run as 'nobody', never root
        IMAGE,
        "python", "-I", "-c", code,
    ]


def run_in_container(code: str, name: str, timeout: float = 60.0):
    cmd = build_command(code, name)
    if shutil.which("docker") is None:
        print("Docker not found -- skipping. Command would be:")
        print("  " + " ".join(cmd[:-1]) + f" {code!r}")
        return None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        # The timeout killed our `docker run` client -- but the CONTAINER keeps
        # running. Stop it by name, then force-remove it (killing a --rm container
        # can leave a "Dead" husk), or it leaks past our process.
        subprocess.run(["docker", "kill", name], capture_output=True, check=False)
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        return None
    return proc.returncode
```

Run normal code and it works; run code that opens a URL and it fails with a
name-resolution error — because `--network none` means there is no network to reach.
Each defense is one flag, and they stack: no network, no writable disk, no capabilities,
no root, bounded CPU/RAM/processes, and the whole thing vanishes on exit. The timeout
branch matters: killing the `docker run` client doesn't stop the container, so you must
`docker kill` (then `docker rm -f`) it by name or it outlives your program.

> **The model can't escape what isn't there.** `--read-only` + `--network none` +
> `--cap-drop ALL` removes the *capability* to exfiltrate or persist, so even a perfect
> prompt-injection has nowhere to go. Isolation beats detection.

## When you need more: gVisor and Firecracker

Containers share the host **kernel** — a kernel bug can be an escape. When the threat model
demands a harder boundary:

- **gVisor (`runsc`)** — a user-space kernel that intercepts syscalls, so untrusted code
  never talks to the host kernel directly. Drop-in as a Docker runtime
  (`docker run --runtime=runsc …`). This is what OpenAI's and Google's hosted code
  execution use.
- **Firecracker** — lightweight **microVMs** with a real (but minimal) guest kernel, booting
  in ~100 ms. Per-task VM isolation; it's what AWS Lambda runs on.

You rarely build these yourself — you reach for a host or runtime that provides them. The
point for this course: know the names and the trade-off (stronger isolation, more
moving parts) so you can choose deliberately.

## When the sandbox *does* need the network (safely)

`--network none` is the right default, but some tasks genuinely need to reach out — fetch a live
page, call a company API, query a database, or talk to another internal service. The rule is not
"network on or off"; it is **open the smallest hole that does the job, and watch it.**

Work outward from deny:

- **Scope the destination, not just "the internet."** Don't trade `--network none` for full
  egress. Put the sandbox on a network that can reach *only* what it needs — an **egress
  allowlist** enforced by a proxy (the sandbox's one route out permits a fixed set of hosts), or
  a private Docker network wired to a single internal service. One API, one host — not the whole web.
- **Least-privilege credentials.** The sandbox gets a **scoped, short-lived, revocable** token —
  read-only where possible, rate-limited, and never your master key. This is the network version
  of the Postgres `sandbox` role below.
- **Outbound only.** Let it *call out* to known endpoints; never expose it to inbound connections.
- **Treat every response as untrusted input.** A fetched page or API result flows back into the
  model — a prime injection vector (Section 21). Bound it (size cap, timeout) like any tool result.
- **Log every request.** Egress is where exfiltration happens, so record *where* the sandbox
  connected, the method, and the decision — with payload **size and a redacted summary**, never
  raw bodies (those carry the secrets). An allowlist you can't audit is a hope, not a control.

So: allow the network when the task's value needs live data or a remote system **and** you can
scope the destination, scope the credential, and log the traffic. If you can't do all three, stay
at `--network none`. The locked-down Postgres below is this same principle applied to one specific
system — least privilege, read-only, bounded, logged.

## Untrusted SQL: a locked-down Postgres (opt-in)

The team runs Postgres, so here's the database version of the sandbox: let SQL run without
letting it read everything or change anything. Two halves —

**The role** (an admin creates it once). It can do almost nothing by default:

```sql
CREATE ROLE sandbox NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT LOGIN;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM sandbox;
GRANT SELECT ON public.products TO sandbox;   -- only what it truly needs
```

**The session guards** any app applies on *every* untrusted query — a `READ ONLY`
transaction, a short `statement_timeout`, **bound parameters** (never string-formatted
SQL), and a row cap. `work/pg_sandbox.py`:

```python
import os


def run_guarded(conn, sql: str, params: tuple):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")     # no writes, no DDL
            cur.execute("SET LOCAL statement_timeout = '2s'")  # can't hog the server
            cur.execute(sql, params)                     # params are bound, not formatted
            return cur.fetchmany(100)                     # cap rows into memory
```

Inside `READ ONLY`, any `INSERT`/`UPDATE`/`CREATE` is rejected by Postgres itself — defense
that doesn't depend on you remembering to check. String-formatting a value into the SQL
(`f"… WHERE id = {x}"`) throws all of this away: that's SQL injection, the database cousin
of the prompt injection in Section 21. Always pass parameters.

> **Opt-in:** the runnable [`examples/17/pg_sandbox.py`](../examples/17/pg_sandbox.py) only
> does anything if you set `DATABASE_URL` to a **throwaway dev database you control** (and
> have `psycopg` installed). Skip this part otherwise — without `DATABASE_URL` the script
> prints a notice and exits cleanly, and the rest of the section stands on its own.

## Audit every execution

Isolation stops damage; an **audit log** lets you see what was attempted. Emit one
structured JSON line per execution — what ran, the allow/deny decision, the exit code, the
duration. But a command can carry secrets (a token in an argument, a password in a
connection string), so don't log it raw: store a **truncated preview** plus a **hash**, not
the full payload.

```python
import hashlib
import json


def emit_audit(*, tool, command, decision, exit_code, duration_ms):
    print(json.dumps({
        "event": "sandbox.exec",
        "tool": tool,
        "command_preview": command[:80],                          # truncated, not full
        "command_sha256": hashlib.sha256(command.encode()).hexdigest()[:16],  # fingerprint
        "decision": decision, "exit_code": exit_code, "duration_ms": duration_ms,
    }, separators=(",", ":")))
```

The preview is enough for a human to recognize a command; the hash lets you group repeats —
without ever writing the secret to a log store that's widely readable. JSON lines ship
straight into the **Elastic stack** — Filebeat tails the file, or you POST to
Elasticsearch's `_bulk` endpoint — the same observability discipline from Section 10, now
pointed at security events. The full emitter is
[`examples/17/audit_log.py`](../examples/17/audit_log.py).

## Challenges

1. **Prove the network is gone.** Run code in the container that fetches a URL; confirm it
   fails. Then drop `--network none` and watch it succeed — so you *see* what the flag buys.
   *Success:* you can toggle the network on and off with one flag.
2. **Break out of read-only.** Against a dev Postgres, run an `INSERT` through `run_guarded`
   and confirm Postgres rejects it. Then try a string-formatted value and a parameterized
   one with input like `1; DROP TABLE products` — show only the string-formatted path is
   dangerous. *Success:* you can demonstrate the injection and the fix.
3. **Wire the audit log.** Make `run_command` (Section 16) and `run_in_container` both emit
   an audit record, then `grep`/`jq` the output for every `"decision":"deny"`. *Success:* a
   one-command view of everything that was blocked.

## Recap

- **Containers** add the filesystem/network isolation process limits can't: `--network none`,
  `--read-only`, `--cap-drop ALL`, non-root, bounded RAM/PIDs, ephemeral.
- **gVisor** (user-space kernel) and **Firecracker** (microVMs) are the stronger tiers hosted
  tools use — reach for them when a shared kernel isn't good enough.
- Untrusted **SQL**: a least-privilege role + `READ ONLY` + `statement_timeout` + **bound
  parameters** + row caps. String-formatted SQL is injection.
- **Audit** every execution as metadata-only JSON; ship it to the Elastic stack (Section 10).

## Next

**[Section 18 — Model Context Protocol (MCP)](18-model-context-protocol.md):** with tools defined (13–14) and their execution
isolated (15–16), the next question is how to *expose and consume* tools as a standard — so a
server of tools can be shared across apps. That's MCP.
