---
title: "E2. The Local Systems Server"
linkTitle: "E2. The Local Systems Server"
weight: 15
---

**Goal:** give an agent controlled access to a host under investigation: who logged in, what
is running, what it talks to, what will start it again, and what a file is. You'll:
- place host triage on the permission dial, and see why the right rung runs no commands at
  all;
- build a server whose every tool parses a file, with path rules that keep it inside the
  host it examines and away from credentials;
- gate the one write, collecting evidence, behind a person's approval;
- run the server inside the Foundations §16–17 sandbox, because what it parses was written by
  the attacker, and probe the walls from inside;
- triage db-prod-01, the host the attacker pivoted to, and check every finding.

**Where this fits:** the second unit of the optional enterprise track. It picks up where
Unit 12's triage stopped, uses Unit 5's dial, Unit 7's ticket approval and Unit 10's
redaction, and puts Foundations
[§16](../../lessons/16-sandboxing-isolation-and-limits.md) and
[§17](../../lessons/17-sandboxing-containers-and-postgres.md) around an MCP server. No estate
and no model are needed; Docker makes it a sandbox.

## What the SIEM doesn't have

Unit 12's triage found the pivot: at 02:46:15 UTC, svc_backup, already inside jump-prod-02,
logged in to **db-prod-01**, the primary customer database. The SIEM has that one login and
nothing after it: the host's own logs were never shipped, and EDR doesn't cover it. What
happened next is on the host, in its files.

[`examples/E2/suspect_host.py`](../examples/E2/suspect_host.py) writes db-prod-01 as it was
found the next morning into a directory, the way a live-response collector or a mounted disk
image gives it to an investigator:

| where | what's there |
|---|---|
| `/var/log/auth.log` | the login from 10.20.167.107 (jump-prod-02), then three refused `sudo` passwords |
| `/home/svc_backup/.bash_history` | a download from the C2 address, a database dump staged in `/var/tmp/.cache`, a password on a command line |
| `/var/tmp/.cache/kworker` | the payload: a stand-in that does nothing |
| `/proc/2291/…`, `/proc/net/tcp` | it's still running, named like a kernel thread, connected to 198.51.100.23:443 |
| a user crontab, `/etc/systemd/system/dbus-helper.service` | two ways it starts again |
| `/etc/shadow`, a private SSH key, `.pgpass` | what a triage server must never hand to a model |
| `/home/svc_backup/etc-link` | a symlink that points out of the image: a trap for a careless path check |

The addresses and times agree with the estate: 198.51.100.23 is the C2 address the SIEM saw
from jump-prod-02, and the login is the one Unit 12 found.

## Host triage on the dial

Unit 5's dial, for a host instead of a database:

| | the model gets | here |
|---|---|---|
| **permissive** | a shell: `run_command("…")` | not built |
| **guarded** | an allowlist of programs, arguments checked | not built |
| **restrictive** | task tools whose arguments are filters and paths | the seven triage tools |
| **hybrid** | restrictive, plus one action behind a person | `collect_artifacts`, with a ticket |

A shell is the obvious tool and the wrong one. The investigator's shell is on a compromised
host: anything the model types runs next to the attacker's code, with the server's rights,
and its arguments come from text the attacker wrote (a crafted filename, a log line). Its
blast radius is the whole host.

Foundations §16 showed the guarded rung, and its own limit: an allowlist of programs is
necessary, not sufficient, because `cat`, `head` and `ls` read any file their arguments name.
Triage needs exactly those reads. Guarding them means validating every argument of every
program, and you are back to writing the restrictive tools, with a shell underneath.

So the server goes one rung lower on Foundations' isolation ladder: **don't execute at all**.
Every answer a triage needs is already in a file. Processes are `/proc/<pid>/status`,
`cmdline` and the `exe` link. Connections are `/proc/net/tcp`, joined to processes through
their socket inodes. Persistence is crontabs, unit files and `authorized_keys`. Logins are
the auth log. Parsing them takes a few lines each, needs no privileges beyond reading, and
works the same on a live host (`--root /`) and on an image (`--root /mnt/evidence`).

## The tools

[`examples/E2/local_server.py`](../examples/E2/local_server.py), standard library and the
SDK only:

| tool | reads | returns |
|---|---|---|
| `host_overview()` | `/etc/hostname`, `os-release`, `passwd` | name, OS, accounts with a login shell |
| `auth_events(user, outcome, since, until, limit)` | the auth log | sshd logins and failures, sudo, oldest first |
| `list_processes(user, suspicious_only)` | `/proc/<pid>/…` | pid, parent, user, name, command line, executable, hints |
| `network_connections(state, remote_only)` | `/proc/net/tcp{,6}` + socket inodes | local, remote, state, owning process, `external` |
| `persistence()` | crontabs, systemd units, `authorized_keys` | each entry, with hints; keys as fingerprints |
| `file_info(path)` | the file's metadata | size, mode, owner, times, SHA-256, type; a symlink's target, not followed |
| `read_file(path, max_bytes)` | a text file | at most 16 KiB, secrets redacted; credentials refused |

The **hints** are heuristics for a person: an executable in `/tmp`, `/var/tmp`, `/dev/shm` or
a hidden directory, a kernel-thread name with an executable on disk, a cron line that
downloads something. The instructions say what they are: "heuristics for a person to check,
not verdicts". A model that reads "suspicious" as "malicious" will close the wrong incident.

Everything a tool returns came from the host under investigation, which is to say from the
attacker. Command lines, log lines and file contents are evidence, never instructions; the
server says so in its instructions, and Unit 10's host-side fencing applies to its results
like any other server's.

### Paths

Paths are the host's own: `read_file("/etc/passwd")`. The server joins them to its root,
resolves the result with `realpath`, and refuses anything that resolves outside the root:

```python
real = Path(os.path.realpath(root / path.lstrip("/")))
if real != root and root not in real.parents:
    raise ToolError(f"{path} resolves outside the host being examined ...")
```

That one check covers both escapes: `/../../etc/hostname` and
`/home/svc_backup/etc-link/hostname`. The link points to `/etc`, which on the investigator's
machine is the investigator's `/etc`. Checking the *string* for `..` would miss the link;
resolving and then comparing catches both. `file_info` on the link itself reports its
target without following it, and `exe` links in `/proc` are read with `readlink`, never
opened: on a live host, opening one would open the attacker's binary.

### Credentials and secrets

Two lists, two jobs:

- **Credential files** (`/etc/shadow`, `*/.ssh/id_*`, `*.key`, `.pgpass`, `.env`,
  `/proc/*/environ`…) are never read: `read_file` refuses them, by the path asked for and by
  the path it resolves to. Their **hash** is not a secret, so `file_info` works on them: a
  responder can still prove a key file changed.
- **Secrets inside readable text** are redacted with Unit 10's table plus two shell habits,
  `NAME_PASSWORD=…` assignments and `mysql -pSECRET`. The history line
  `PGPASSWORD='…' pg_dump …` comes back as `PGPASSWORD=[REDACTED] pg_dump …`: the model
  learns that a dump was taken, and with which account, without the password.

Command lines in `list_processes` and entries in `persistence` go through the same
redaction: a password passed as an argument shows up in `/proc/<pid>/cmdline`.

## The one write: collecting evidence

A responder's next step is to take copies before anything changes: the payload, the
crontab, the unit file. `collect_artifacts(case_id, paths)` copies up to 20 files into an
evidence store, named by their hash, with a manifest of SHA-256s. That is the chain of
custody: the hash recorded at collection is the hash `file_info` reported, and anyone can
check it later.

It's the hybrid rung, and it is gated the way Unit 7 gates writes:

1. It exists only when the server is started with `--collect <dir>`. Without one, the tool
   isn't in the list.
2. Every path is checked **before** anyone is asked: credentials are refused (a person
   collects those by hand), and so is anything outside the root. As in E1, ask only for what
   will run.
3. A person approves with a ticket, through elicitation. Declining copies nothing.
4. Collecting the same files for the same case again copies nothing and adds nothing to the
   manifest.

The server never deletes, kills or changes anything on the host. Containment, such as isolating
db-prod-01, goes through E1's gateway, behind its approval and change window.

## The sandbox

The server reads files the attacker wrote. A parser is code, and attacker-controlled input to
code is how an exploit gets in. The answer is Foundations §17's: run the parser where getting
in leads nowhere. [`examples/E2/sandbox.py`](../examples/E2/sandbox.py) builds the command:

```bash
docker run --rm -i --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 64 --memory 256m --cpus 1 \
  --user 65534:65534 -v /evidence/db-prod-01:/host:ro [-v /evidence/out:/out] \
  creating-mcps-local
```

- `-i` and stdio: the MCP client starts `docker run` as the server's process, the way Unit 2's
  host starts any stdio server. No port is opened.
- The host is mounted **read-only** at `/host`; the only writable path is the evidence store,
  and only when collection is on.
- [`examples/E2/Dockerfile`](../examples/E2/Dockerfile) holds Python, the SDK and one file.
  The server runs no programs, so the image needs none.

`sandbox.walls(root)` runs probes inside the same sandbox, with the same flags, and reports
each attempt: write into the host, write next to the code, write to `/tmp`, connect to the C2
address, resolve a name, run as root, hold a capability, gain privileges, exceed 256 MiB or 64
processes. The walls hold when every attempt fails:

```bash
python examples/E2/sandbox.py walls /tmp/db-prod-01
```

The walls are what make the parsers' bugs survivable, but they don't replace care: the path
rules still keep the server inside its root, and the credential list still keeps secrets from
the model. A sandbox limits what a compromised server can do; it doesn't stop a correct
server from handing a model the wrong file. Containers share the host's kernel, so for a
harder boundary run the same image under gVisor (`--runtime=runsc`), as §17 describes.

On a live Linux host, the same command with `-v /:/host:ro` triages the machine itself; the
bind mount includes `/proc`, so processes and connections are the host's. On macOS there is
no `/proc`, so only the file tools have anything to read.

## The drill

[`examples/E2/drill.py`](../examples/E2/drill.py) plants the host, connects to the server (in
the sandbox when the image exists), makes a responder's calls, and checks each against the
planted facts:

| finding | from | expected |
|---|---|---|
| the login | `auth_events(user="svc_backup")` | 02:46:15 from 10.20.167.107, then refused sudo |
| the payload | `list_processes(suspicious_only=true)` | exactly one: pid 2291, from `/var/tmp/.cache` |
| its connection | `network_connections(remote_only=true)` | pid 2291 to 198.51.100.23:443 |
| persistence | `persistence()` | the crontab and the unit flagged; nothing else |
| the file | `file_info` | the payload's SHA-256, without its content |
| what was typed | `read_file(.bash_history)` | the download and the dump; the password redacted |
| what stays put | `read_file` on credentials, the link, `..` | five refusals; no secret in any result |
| evidence | `collect_artifacts` | credentials refused unasked; declined, nothing; approved, hashes match; again, no change |
| the walls | `sandbox.walls` | ten probes, all held |

```bash
docker build -f examples/E2/Dockerfile -t creating-mcps-local .
python examples/E2/drill.py --require-sandbox
```

## Lab: the local systems server

Open the notebook ([`labs/E2_local_systems.py`](../labs/E2_local_systems.py)) with

```bash
marimo edit labs/E2_local_systems.py
```

It has four parts:

1. The host, as found: the planted files and the facts a triage should find.
2. Triage db-prod-01: every call, what it asked, what came back, the checks, the redacted
   history and the evidence manifest.
3. Paths, your way: type a path and see what `read_file` and `file_info` do with it.
4. The walls, from inside: the probes and the docker command.

## Challenges

1. **Show deleted executables.** A payload that deletes itself after starting leaves `exe`
   reading `/var/tmp/.cache/kworker (deleted)`. Plant one and make sure the hints say so.
   *Success:* `list_processes(suspicious_only=true)` names it with both hints, and the drill
   still finds exactly the processes planted.
2. **A timeline.** Add `timeline(since, until)`: auth events, history entries (they carry
   timestamps) and file modification times under the staging directories, merged in order.
   *Success:* it shows the login, the download, the payload's mtime and the dump in that
   order, inside a 15-minute window.
3. **Climb the ladder.** Run the drill's sandbox under gVisor (`--runtime=runsc`) if you can
   install it. *Success:* the probes still hold, and you can say what changes for a kernel
   exploit.

## Recap

- **The right rung runs nothing.** Every triage answer is in a file; parse it rather than
  allowlisting programs that read files.
- **Resolve, then compare.** One `realpath` check under the root stops `..` and symlinks; read
  links, don't follow them.
- **Credentials are refused, secrets redacted.** Hashes are fine, contents aren't, and
  passwords in command lines are found where they leak.
- **The one write is gated.** Off unless configured, checked before asking, approved with a
  ticket, idempotent, hashed for the chain of custody.
- **Sandbox the parser.** What it reads was written by the attacker: no network, nothing
  writable, no capabilities, not root, bounded, and probed from inside.
- **Hints aren't verdicts.** They tell a person where to look.

## Next

**E3. Full OAuth for Remote Servers** *(coming next)*: protected-resource metadata, client
registration, audience-bound tokens, step-up scopes and token exchange. Or go back to
[E1](E1-backend-apis-with-approvals.md).
