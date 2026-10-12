"""
E2: run the local systems server inside the Foundations §16-17 sandbox, and prove the walls.

The server parses files an attacker wrote: log lines, command lines, crontabs, a binary's
first bytes. A parser bug is the attacker's way in, so the parser runs where a way in leads
nowhere. Every flag below is §17's, plus two mounts:

    --network none                   no network: nothing to exfiltrate to, no C2 to reach
    --read-only                      the container's own filesystem can't be written
    --cap-drop ALL                   no Linux capabilities, not even the ones root keeps
    --security-opt no-new-privileges no setuid way back up
    --pids-limit 64 --memory 256m --cpus 1   a parser stuck on a hostile file can't take the
                                     machine with it
    --user 65534:65534               nobody, never root
    -v <host>:/host:ro               the host to examine, read-only (a live host's "/" or an
                                     image of it)
    -v <evidence>:/out               only with collection on: the one place it can write

    python examples/E2/sandbox.py walls /tmp/db-prod-01     # the probes, from inside
    python examples/E2/sandbox.py argv /tmp/db-prod-01      # the docker command, printed

`server(root, collect)` gives the MCP client its StdioServerParameters: `docker run -i ...`
when the image is there, else the same server as a plain process (say so: that's no sandbox).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGE = "creating-mcps-local"
LIMITS = {"pids": 64, "memory": "256m", "cpus": "1"}


def argv(root: Path, collect: Path | None = None, name: str | None = None,
         command: list[str] | None = None) -> list[str]:
    """The `docker run` command for the server (or `command`, for probes) in the sandbox."""
    cmd = ["docker", "run", "--rm", "-i", "--name", name or f"e2-{uuid.uuid4().hex[:8]}",
           "--network", "none", "--read-only", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges", "--pids-limit", str(LIMITS["pids"]),
           "--memory", LIMITS["memory"], "--cpus", LIMITS["cpus"], "--user", "65534:65534",
           "-v", f"{Path(root).resolve()}:/host:ro"]
    if collect is not None:
        cmd += ["-v", f"{Path(collect).resolve()}:/out"]
    if command:
        cmd += ["--entrypoint", command[0], IMAGE, *command[1:]]
    else:
        cmd += [IMAGE] + (["--collect", "/out"] if collect is not None else [])
    return cmd


def available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True,
                          check=False).returncode == 0


def server(root: Path, collect: Path | None = None, sandboxed: bool | None = None):
    """(StdioServerParameters, sandboxed?) for an MCP client. sandboxed=None: if possible."""
    from mcp import StdioServerParameters

    if sandboxed is None:
        sandboxed = available()
    if collect is not None:
        Path(collect).mkdir(parents=True, exist_ok=True)
        Path(collect).chmod(0o777)        # nobody (65534) writes there, and only there
    if sandboxed:
        cmd = argv(root, collect)
        return StdioServerParameters(command=cmd[0], args=cmd[1:]), True
    args = [str(HERE / "local_server.py"), "--root", str(root)]
    if collect is not None:
        args += ["--collect", str(collect)]
    return StdioServerParameters(command=sys.executable, args=args), False


# What the server could do if a hostile file took it over, tried from inside. Each probe says
# what it tried and what happened; the walls hold when every attempt is refused.
PROBE = r'''
import json, os, socket
out = []
def attempt(wall, what, fn):
    try:
        got = fn()
        out.append({"wall": wall, "tried": what, "held": False, "got": str(got)[:80]})
    except Exception as e:
        out.append({"wall": wall, "tried": what, "held": True,
                    "got": f"{type(e).__name__}: {e}"[:80]})
def write(path):
    with open(path, "w") as f:
        f.write("x")
    return "written"
def connect(addr):
    socket.create_connection(addr, timeout=2).close()
    return "connected"
def check(wall, what, ok, got):
    out.append({"wall": wall, "tried": what, "held": ok, "got": got})
status = dict(l.split(":\t", 1) for l in open("/proc/self/status").read().splitlines() if ":\t" in l)
attempt("read-only image", "write into the host being examined", lambda: write("/host/etc/hostname"))
attempt("read-only container", "write next to the server's own code", lambda: write("/app/x"))
attempt("read-only container", "drop a file in /tmp", lambda: write("/tmp/x"))
attempt("no network", "connect to the C2 address", lambda: connect(("198.51.100.23", 443)))
attempt("no network", "resolve a name", lambda: socket.getaddrinfo("example.org", 443))
check("not root", "run as root", os.getuid() == 65534, f"uid {os.getuid()}")
check("no capabilities", "hold any capability", int(status["CapEff"], 16) == 0, f"CapEff {status['CapEff'].strip()}")
check("no new privileges", "gain privileges through setuid", status["NoNewPrivs"].strip() == "1", f"NoNewPrivs {status['NoNewPrivs'].strip()}")
def cg(*names):  # cgroup v2, else v1
    for name in names:
        try:
            return open(f"/sys/fs/cgroup/{name}").read().strip()
        except OSError:
            pass
    return "?"
mem, pids = cg("memory.max", "memory/memory.limit_in_bytes"), cg("pids.max", "pids/pids.max")
check("bounded", "use more than 256 MiB", mem == str(256 * 1024 * 1024), f"memory limit {mem}")
check("bounded", "start more than 64 processes", pids == "64", f"process limit {pids}")
print(json.dumps(out))
'''


def walls(root: Path) -> list[dict]:
    """Run the probes inside the sandbox; each row: wall, tried, held, got."""
    proc = subprocess.run(argv(root, command=["python", "-I", "-c", PROBE]),
                          capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"the probe didn't run: {proc.stderr[-500:]}")
    return json.loads(proc.stdout)


def main() -> None:
    what, root = (sys.argv[1:3] + [None, None])[:2]
    if what not in ("walls", "argv") or root is None:
        sys.exit("usage: sandbox.py walls|argv <host root>")
    if what == "argv":
        print(" ".join(argv(Path(root))))
        return
    if not available():
        sys.exit(f"no {IMAGE} image: docker build -f examples/E2/Dockerfile -t {IMAGE} .")
    rows = walls(Path(root))
    for r in rows:
        print(f"  [{'HELD' if r['held'] else 'OPEN'}] {r['wall']}: {r['tried']} -> {r['got']}")
    sys.exit(0 if all(r["held"] for r in rows) else 1)


if __name__ == "__main__":
    main()
