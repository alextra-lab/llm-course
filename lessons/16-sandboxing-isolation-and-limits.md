---
title: 'Sandboxing I: Why Isolate, and Portable Limits'
linkTitle: '16. Sandboxing I: Why Isolate, and Portable Limits'
weight: 16
---

**Goal:** make *executing untrusted actions* safe. In Sections 14–15 the model chose
which tools to run; for a calculator we stayed safe by **parsing** the input instead of
`eval`-ing it. But you can't parse arbitrary code, a shell command, or SQL into safety.
The real answer is **isolation** — run the action in a box that limits what it can do.
Here you build the *portable* tier: a subprocess with hard resource limits and an
allow-listed shell tool, runnable on any machine with no extra software.

**Where this fits:** this is the missing half of tool use. Section 15 gave the model the
power to *act*; this section makes acting *contained*, so that by the time you reach
Agents (Section 23) — which take many steps unattended — every step runs inside a limit.

> **Mindset:** safety comes from what your *code allows to happen*, not from trusting the
> input. A sandbox is that limit made concrete: even if the code is hostile, it can only
> burn the CPU/RAM you granted, for as long as you allowed, with no secrets in reach.

---

## The isolation ladder

There's no single "sandbox." There's a ladder, and you climb only as far as your threat
model needs:

1. **Don't execute at all** — parse/validate instead (the Section 14–15 calculator). Best
   when you can.
2. **Portable process limits** *(this section)* — a separate process, a timeout, and
   `setrlimit` caps on CPU, memory, file size, and child processes. Stops *runaway* code.
3. **Containers** *(Section 17)* — add real filesystem and network isolation.
4. **microVMs / user-space kernels** *(Section 17, pointers)* — gVisor, Firecracker; what
   hosted "code interpreter" tools use.

Each rung costs more to set up and isolates more. Start low, climb when the input gets
more dangerous.

## Build a portable code sandbox

We run untrusted Python in a **child process** so a crash, a hang, or a memory grab can't
take down our program. Before the child runs the code, it caps its own resources.

Start `work/safe_exec.py` — the limits, applied inside the child just before it executes:

```python
import subprocess
import sys
import tempfile
from dataclasses import dataclass

try:
    import resource  # POSIX only (Linux, macOS)
except ImportError:
    resource = None

CPU_SECONDS = 2
MEMORY_BYTES = 256 * 1024 * 1024
FILE_BYTES = 1024 * 1024
MAX_PROCESSES = 0  # no fork/exec: the child can't spawn more processes


def _apply_limits():
    if resource is None:
        return
    for what, soft in (
        (resource.RLIMIT_CPU, CPU_SECONDS),
        (resource.RLIMIT_AS, MEMORY_BYTES),
        (resource.RLIMIT_FSIZE, FILE_BYTES),
        (resource.RLIMIT_NPROC, MAX_PROCESSES),
    ):
        try:
            resource.setrlimit(what, (soft, soft))
        except (ValueError, OSError):
            pass  # not every limit is enforceable on every OS; cap what we can
```

Now run the code. Four habits do the work: a separate process, a **wall-clock timeout**, a
**stripped environment** (so secrets in *your* env never reach the child), a **throwaway
working directory**, and **never `shell=True`**:

```python
@dataclass
class Result:
    ok: bool
    stdout: str
    stderr: str
    note: str


def run_untrusted(code: str, timeout: float = 5.0) -> Result:
    with tempfile.TemporaryDirectory() as workdir:
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-c", code],  # -I = isolated interpreter
                cwd=workdir,
                env={"PATH": "/usr/bin:/bin"},        # minimal, no secrets
                preexec_fn=_apply_limits if resource else None,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return Result(False, "", "", f"killed: exceeded {timeout}s wall clock")
    note = "ok" if proc.returncode == 0 else f"exited with code {proc.returncode}"
    return Result(proc.returncode == 0, proc.stdout, proc.stderr, note)
```

Run a CPU bomb through it and the process dies from the CPU limit (signal `SIGXCPU`); a
hang dies from the timeout. The caller stays alive and gets a clean `Result`.

> **The honest limit:** `setrlimit` is **POSIX-only**, and even there enforcement varies —
> Linux honors `RLIMIT_AS` (memory), macOS often ignores it. Pure process limits also do
> **not** seal off the filesystem or the network. That gap is exactly what containers
> (Section 17) close. Know what your tier does *not* protect.

## A shell tool the model can't abuse

The same idea for "run a shell command." The command string is untrusted — the model (or
an attacker through it) picked it. The safe pattern is an **allowlist** (fail closed:
deny anything not explicitly permitted), `shlex` parsing, and **no shell**.

`work/bash_allowlist.py`:

```python
import shlex
import subprocess

# Only programs that are harmless with ANY arguments. Note what's NOT here:
# cat/head/ls/wc all read the filesystem.
ALLOWED = {"echo", "date"}


def run_command(command: str, timeout: float = 5.0):
    argv = shlex.split(command)            # split on real shell rules, once
    if not argv or argv[0] not in ALLOWED:
        return False, f"denied: {argv[:1] or 'empty'} not on the allowlist"
    proc = subprocess.run(                 # a list, never shell=True
        argv, capture_output=True, text=True, timeout=timeout, check=False,
    )
    return True, proc.stdout.strip()
```

Because we pass a **list** and never invoke a shell, `"echo hi; rm -rf /"` parses to a
single `echo` whose arguments include a literal `;` — the `rm` never runs. The allowlist
is the primary defense; no-shell execution is the backstop. A denylist ("block `rm`,
`curl`, …") is the trap: you'll always forget one.

But notice what the allowlist *doesn't* fix: a permitted program can still be dangerous
through its **arguments**. `cat ~/.env`, `head ~/.ssh/id_rsa`, `wc /etc/passwd` all read
files you never meant to expose — which is why those commands are deliberately **off** the
list. An allowlist of programs is necessary, not sufficient: the moment a tool must touch
the filesystem or network, validate its arguments too, or run it in the container from
Section 17 (which removes the files and network entirely).

> **Reference:** the complete, runnable versions are
> [`examples/16/safe_exec.py`](../examples/16/safe_exec.py) and
> [`examples/16/bash_allowlist.py`](../examples/16/bash_allowlist.py). Run them and watch
> the dangerous cases get stopped by *limits*, not by trusting the input.

## Challenges

1. **Make the timeout bite.** Feed `run_untrusted` an infinite loop and confirm you get a
   clean `Result` (not a hung program). Then lower `CPU_SECONDS` to 1 and a busy loop;
   see which limit fires first. *Success:* your program stays responsive and reports the
   kill.
2. **Prove the env is stripped.** Put a fake secret in your environment
   (`export SECRET=hunter2`) and run code through the sandbox that tries to read it
   (`import os; print(os.environ.get("SECRET"))`). *Success:* it prints `None`.
3. **Allowlist vs denylist.** Add a `run_command_denylist` that blocks a set of "bad"
   programs instead, then find an allowed-but-dangerous command it misses (hint: an
   interpreter like `python`, or `find … -exec`). *Success:* you can articulate why the
   allowlist is safer.

## Recap

- You can't *parse* arbitrary code/shell/SQL into safety — you **isolate** it.
- The portable tier: a **separate process**, a **timeout**, `setrlimit` caps
  (CPU/memory/file-size/processes), a **stripped env**, a temp `cwd`, and **never a shell**.
- Process limits stop *runaway* code but don't seal the filesystem or network — know the gap.
- For shell tools, **allowlist + no shell**; denylists leak.

## Next

**Section 17 — Sandboxing II:** we climb the ladder. Containers add the filesystem and
network isolation process limits can't, we lock SQL down against a real Postgres, and we
audit every execution — with pointers to gVisor and Firecracker for when you need more.
