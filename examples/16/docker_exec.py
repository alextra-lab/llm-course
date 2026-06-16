"""
Section 16 - Sandboxing II: run untrusted code in a hardened container.

The portable limits from Section 15 cap CPU/memory but don't really isolate the
filesystem or the network. A container does. We drive the `docker` CLI (no Python
Docker library needed) and lock the container down hard:

  --rm                       throw it away when done (ephemeral)
  --network none             no network at all
  --read-only                read-only root filesystem
  --cap-drop ALL             drop every Linux capability
  --security-opt no-new-privileges
  --pids-limit / --memory    bound processes and RAM
  --user 65534:65534         run as 'nobody', never root

If Docker isn't installed/running, this prints the command and skips -- so you can
still read along (the same "degrade gracefully" habit the course uses elsewhere).

    python examples/16/docker_exec.py
"""

import os
import shutil
import subprocess

IMAGE = "python:3.12-slim"


def build_command(code: str, name: str) -> list[str]:
    """The hardened `docker run` invocation. Inspect this -- it IS the lesson."""
    return [
        "docker", "run", "--rm",
        "--name", name,                   # a handle so we can kill it on timeout
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "64",
        "--memory", "256m",
        "--cpus", "1.0",                  # bound CPU so a busy loop can't hog a core
        "--user", "65534:65534",
        IMAGE,
        "python", "-I", "-c", code,
    ]


# A per-process counter makes each container name unique without randomness.
def run_in_container(code: str, name: str, timeout: float = 60.0):
    cmd = build_command(code, name)
    if shutil.which("docker") is None:
        print("Docker not found -- skipping execution. The command would be:\n")
        print("  " + " ".join(cmd[:-1]) + f" {code!r}")
        return None
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        # CRITICAL: the timeout killed our `docker run` client, but the *container*
        # keeps running. Stop it by name, then force-remove it -- killing a `--rm`
        # container can leave a "Dead" husk, so `rm -f` guarantees no leak.
        print(f"killed: container exceeded {timeout}s")
        subprocess.run(["docker", "kill", name], capture_output=True, check=False)
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        return None
    print(f"exit={proc.returncode}")
    if proc.stdout.strip():
        print("stdout:", proc.stdout.strip())
    if proc.stderr.strip():
        print("stderr:", proc.stderr.strip().splitlines()[-1])
    return proc.returncode


if __name__ == "__main__":
    # A unique name per run lets the timeout branch kill the right container.
    base = f"sandbox-{os.getpid()}"

    print("1) normal work inside the container:")
    run_in_container("print(sum(range(1000)))", f"{base}-1")

    print("\n2) the network is gone (this should fail to resolve/connect):")
    run_in_container(
        "import urllib.request;"
        "urllib.request.urlopen('http://example.com', timeout=3)",
        f"{base}-2",
    )

    print("\nStronger tiers exist when the threat model demands it: gVisor (runsc) "
          "gives a\nuser-space kernel, and Firecracker microVMs give per-task VM "
          "isolation -- which is\nwhat hosted code-execution tools use under the hood.")
