"""
Unit 1: a frame tap -- record every JSON-RPC message between a client and a stdio server.

It sits between the two as a tiny proxy: the client starts the tap instead of the server, the
tap starts the real server, and every line that passes through is appended to a JSONL file
with a timestamp and a direction. Nothing is changed in flight. The Unit 1 lab reads that
file; later units reuse the tap whenever you need to see what really went over the wire.

    python examples/01/frame_tap.py frames.jsonl -- python examples/01/cmdb_server.py
    python examples/01/client.py --tap frames.jsonl        # what client.py does with --tap
"""

import json
import subprocess
import sys
import threading
import time


def main(argv):
    if len(argv) < 3 or argv[1] != "--":
        sys.exit("usage: frame_tap.py FRAMES.jsonl -- COMMAND [ARGS...]")
    out_path, command = argv[0], argv[2:]
    server = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              text=True, bufsize=1)          # stderr passes straight through
    lock = threading.Lock()
    t0 = time.perf_counter()

    with open(out_path, "a", encoding="utf-8") as out:
        def pump(src, dst, direction):
            for line in src:
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    frame = line.rstrip("\n")             # record it anyway: it's evidence
                with lock:
                    out.write(json.dumps({"t_ms": round((time.perf_counter() - t0) * 1000, 2),
                                          "dir": direction, "bytes": len(line.encode()),
                                          "frame": frame}) + "\n")
                    out.flush()
                dst.write(line)
                dst.flush()

        back = threading.Thread(target=pump, args=(server.stdout, sys.stdout, "server->client"),
                                daemon=True)
        back.start()
        pump(sys.stdin, server.stdin, "client->server")
        server.stdin.close()                 # client hung up: let the server see EOF and exit
        server.wait()
        back.join(timeout=5)
    return server.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
