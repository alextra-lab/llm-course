"""
Section 15 - Sandboxing I: run untrusted code in a limited subprocess.

The tool loop (Section 14) lets the model choose what to run. For a calculator we
parsed the input (no eval). But you can't "parse" arbitrary code into safety -- the
answer is *isolation*. Here we run code in a separate process with hard limits:

  - a wall-clock timeout (the process is killed if it runs too long),
  - CPU / memory / file-size / process-count limits (resource.setrlimit, POSIX),
  - a stripped environment (no secrets leak in through env vars),
  - a throwaway working directory, and never shell=True.

This is the *portable* tier: it caps how much damage runaway code can do. It does
NOT fully block the filesystem or the network -- that needs a container (Section 16).

    python examples/15/safe_exec.py
"""

import subprocess
import sys
import tempfile
from dataclasses import dataclass

try:
    import resource  # POSIX only (Linux, macOS)
except ImportError:  # pragma: no cover - Windows
    resource = None

# Hard limits applied inside the child, before it runs the untrusted code.
CPU_SECONDS = 2          # RLIMIT_CPU: CPU time, not wall clock
MEMORY_BYTES = 256 * 1024 * 1024
FILE_BYTES = 1024 * 1024  # largest file the child may write
MAX_PROCESSES = 0         # no fork/exec: the child can't spawn more processes


def _apply_limits():
    """Run in the child (preexec_fn) just before exec. POSIX only."""
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
            pass  # some limits aren't enforceable on every OS; cap what we can


@dataclass
class Result:
    ok: bool
    stdout: str
    stderr: str
    note: str


def run_untrusted(code: str, timeout: float = 5.0) -> Result:
    """Execute `code` in an isolated Python subprocess with limits."""
    with tempfile.TemporaryDirectory() as workdir:
        try:
            proc = subprocess.run(
                # -I = isolated mode: ignore env vars and the user site dir.
                [sys.executable, "-I", "-c", code],
                cwd=workdir,
                env={"PATH": "/usr/bin:/bin"},  # minimal, no secrets
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


def _show(label: str, code: str):
    r = run_untrusted(code)
    print(f"--- {label} ---")
    print(f"  ok={r.ok}  note={r.note}")
    if r.stdout.strip():
        print(f"  stdout: {r.stdout.strip()}")
    if r.stderr.strip():
        print(f"  stderr: {r.stderr.strip().splitlines()[-1]}")
    print()


if __name__ == "__main__":
    if resource is None:
        print("note: resource limits are POSIX-only; on this OS only the "
              "timeout and env/cwd isolation apply.\n")

    _show("normal work", "print(sum(range(1000)))")
    _show("CPU bomb (caught by the CPU limit or timeout)",
          "while True:\n    pass")
    _show("large allocation (memory cap enforced on Linux; macOS may ignore RLIMIT_AS)",
          "x = bytearray(1024 * 1024 * 1024)\nprint(len(x))")
    print("Each ran in its own process; the dangerous ones were stopped by limits,\n"
          "not by trusting the code. Enforcement differs by OS (Linux honors every\n"
          "limit; macOS ignores some) -- which is exactly why Section 16 reaches for\n"
          "containers when the filesystem and network truly must be sealed off.")
