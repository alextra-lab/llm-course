"""
Section 17 - Sandboxing II: audit every sandboxed execution.

Isolation stops damage; an audit log lets you *see* what was attempted. Emit one
structured record per execution -- what ran, the decision (allow/deny), the exit
code, how long it took -- as a single JSON line. JSON lines ship cleanly to the
Elastic stack (Filebeat tails the file, or POST to Elasticsearch's _bulk API),
which ties back to Observability (Section 10).

This is just the emitter; wiring it to your log pipeline is one line of config.

    python examples/17/audit_log.py
"""

import hashlib
import json

PREVIEW_CHARS = 80


def audit_record(*, tool, command, decision, exit_code, duration_ms):
    """Build one audit event.

    A command string can contain secrets (a token passed as an argument, a password in
    a connection string). Logging it raw would leak them into your log store, which is
    widely readable (Section 10). So we keep a short, truncated PREVIEW for humans plus a
    stable SHA-256 fingerprint for correlation -- enough to recognize and group repeated
    commands without storing the full, possibly-sensitive payload.
    """
    digest = hashlib.sha256(command.encode("utf-8", "replace")).hexdigest()
    preview = command[:PREVIEW_CHARS] + ("…" if len(command) > PREVIEW_CHARS else "")
    return {
        "event": "sandbox.exec",
        "tool": tool,
        "command_preview": preview,     # truncated; never the full payload
        "command_sha256": digest[:16],  # fingerprint for correlation
        "decision": decision,           # "allow" or "deny"
        "exit_code": exit_code,
        "duration_ms": duration_ms,
    }


def emit(record: dict):
    """One JSON object per line -- the format Filebeat / Logstash expect."""
    print(json.dumps(record, separators=(",", ":")))


if __name__ == "__main__":
    emit(audit_record(tool="bash", command="echo hello",
                      decision="allow", exit_code=0, duration_ms=12))
    emit(audit_record(tool="bash", command="rm -rf /",
                      decision="deny", exit_code=None, duration_ms=0))
    emit(audit_record(tool="python", command="while True: pass",
                      decision="allow", exit_code=137, duration_ms=2010))
    # To ship to Elasticsearch you'd POST these lines to the _bulk endpoint, or let
    # Filebeat tail the log file. Note the records carry a truncated preview + a hash,
    # never the full command -- so a secret in an argument can't leak into the logs.
