"""
Run the Creating MCPs test estate: the CMDB (Postgres) and the SIEM / log store (OpenSearch).

    python env/env.py up [--ui]          start the containers and wait until they are healthy
    python env/env.py seed [--size S]    create personas, schema, indices; load the data
    python env/env.py check [--no-llm]   preflight: what works, and which units each gap affects
    python env/env.py reset              drop everything and reseed (same data every time)
    python env/env.py down [--volumes]   stop the containers (--volumes also deletes the data)
    python env/env.py status             show the containers

Run it from the creating-mcps/ folder. Configuration is env/.env (copy env/.env.example).

Laptop: run up, seed, check. Shared lab server: the instructor runs up and seed once on the
server; learners point PG_HOST / OS_HOST at it in their own env/.env and only run check.

`seed --size small|default|large` sets how many log documents to generate (20k / 200k / 1M);
`seed --anchor 2026-10-01` freezes the "now" the data is built around (default: today 00:00
UTC). The same size and anchor always produce exactly the same estate.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

ENV_DIR = Path(__file__).resolve().parent
COURSE_DIR = ENV_DIR.parent
sys.path.insert(0, str(ENV_DIR / "seed"))
sys.path.insert(0, str(COURSE_DIR / "examples"))

import common_mcp  # noqa: E402  (loads env/.env into os.environ)
import generate  # noqa: E402

COMPOSE_FILE = ENV_DIR / "compose.yaml"
ENV_FILE = ENV_DIR / ".env"

# Persona settings that make the database itself enforce limits (Units 7 and 8).
PG_PERSONAS = {
    #  role            inherit  connection limit  statement_timeout
    "l1_analyst":   (True, 10, "5s"),
    "l3_responder": (True, 20, "15s"),
    "auditor":      (True, 5, "15s"),
    # The gateway's own login. NOINHERIT: it holds no privileges of its own and must
    # SET ROLE to the caller it is acting for -- the least-privilege pattern of Unit 7.
    "mcp_service":  (False, 50, "15s"),
}

OS_ROLES = {
    "soc_l1": {
        "cluster_permissions": ["cluster_composite_ops_ro", "cluster_monitor"],
        "index_permissions": [{
            "index_patterns": ["logs-*", "incidents"],
            # Field-level security: an L1 analyst never sees personal data or secrets.
            "fls": ["~user.email", "~labels.api_token"],
            "allowed_actions": ["read", "indices_monitor", "indices:admin/mappings/get"],
        }],
    },
    "soc_l3": {
        "cluster_permissions": ["cluster_composite_ops_ro", "cluster_monitor"],
        "index_permissions": [{
            "index_patterns": ["logs-*", "edr-events-*", "incidents"],
            "fls": ["~labels.api_token"],
            # Field masking: L3 can correlate on e-mail (same value -> same hash) without
            # reading it.
            "masked_fields": ["user.email"],
            "allowed_actions": ["read", "indices_monitor", "indices:admin/mappings/get"],
        }],
    },
    "soc_auditor": {
        "cluster_permissions": ["cluster_composite_ops_ro"],
        "index_permissions": [{
            "index_patterns": ["mcp-telemetry-*"],
            "allowed_actions": ["read", "indices_monitor"],
        }],
    },
    "mcp_gateway": {
        "cluster_permissions": ["cluster_composite_ops_ro", "cluster_monitor"],
        "index_permissions": [
            {"index_patterns": ["logs-*", "edr-events-*", "incidents"],
             "allowed_actions": ["read", "indices_monitor", "indices:admin/mappings/get"]},
            {"index_patterns": ["mcp-telemetry-*"],
             "allowed_actions": ["crud", "create_index", "indices_monitor"]},
        ],
    },
}
OS_USERS = {  # internal user -> role
    "l1_analyst": "soc_l1",
    "l3_responder": "soc_l3",
    "auditor": "soc_auditor",
    "mcp_service": "mcp_gateway",
}


# =========================================================================================
# helpers
# =========================================================================================

def need_env_file() -> None:
    if not ENV_FILE.exists():
        sys.exit(f"Missing {ENV_FILE.relative_to(COURSE_DIR)} -- copy env/.env.example to "
                 "env/.env and set the passwords first.")


def compose(*args: str) -> int:
    if not shutil.which("docker"):
        sys.exit("docker is not on your PATH. Install Docker Desktop, Podman (with "
                 "podman-docker), or Rancher Desktop -- or use the shared lab server.")
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), "--env-file", str(ENV_FILE), *args]
    return subprocess.call(cmd)


def admin_pg():
    import psycopg

    return psycopg.connect(
        host=os.environ.get("PG_HOST", "127.0.0.1"), port=int(os.environ.get("PG_PORT", "5433")),
        dbname=os.environ.get("PG_DATABASE", "cmdb"),
        user=os.environ.get("PG_ADMIN_USER", "postgres"),
        password=os.environ["PG_ADMIN_PASSWORD"], autocommit=True, connect_timeout=5)


def admin_os():
    from opensearchpy import OpenSearch

    verify = os.environ.get("OS_VERIFY_CERTS", "0").lower() in ("1", "true", "yes")
    return OpenSearch(
        hosts=[{"host": os.environ.get("OS_HOST", "127.0.0.1"),
                "port": int(os.environ.get("OS_PORT", "9200"))}],
        http_auth=("admin", os.environ["OS_ADMIN_PASSWORD"]), use_ssl=True,
        verify_certs=verify, ca_certs=os.environ.get("OS_CA_CERTS") if verify else None,
        ssl_show_warn=False, timeout=60)


# =========================================================================================
# up / down / status
# =========================================================================================

def cmd_up(args) -> int:
    need_env_file()
    extra = ["--profile", "ui"] if args.ui else []
    print("Starting Postgres and OpenSearch (first run pulls ~1.5 GB of images)...")
    code = compose(*extra, "up", "-d", "--wait")
    if code == 0:
        print("\nUp. Next: python env/env.py seed")
    else:
        print("\nSomething did not become healthy. `python env/env.py status` shows which; "
              "`docker compose -f env/compose.yaml logs opensearch` shows why. On Linux, "
              "OpenSearch may need: sudo sysctl -w vm.max_map_count=262144")
    return code


def cmd_down(args) -> int:
    need_env_file()
    return compose("--profile", "ui", "down", *(["--volumes"] if args.volumes else []))


def cmd_status(_args) -> int:
    need_env_file()
    return compose("--profile", "ui", "ps")


# =========================================================================================
# seed
# =========================================================================================

def seed_postgres(rows: dict) -> None:
    from psycopg import sql

    with admin_pg() as conn:
        for role, (inherit, conn_limit, timeout) in PG_PERSONAS.items():
            password = os.environ[common_mcp.PERSONAS[role]]
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(sql.SQL("{} ROLE {} LOGIN {} CONNECTION LIMIT {} PASSWORD {}").format(
                sql.SQL(verb), sql.Identifier(role),
                sql.SQL("INHERIT" if inherit else "NOINHERIT"),
                sql.Literal(conn_limit), sql.Literal(password)))
            conn.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = {}").format(
                sql.Identifier(role), sql.Literal(timeout)))
        # The gateway may act AS a caller (SET ROLE), never as itself.
        conn.execute("GRANT l1_analyst, l3_responder, auditor TO mcp_service")

        conn.execute((ENV_DIR / "seed" / "cmdb.sql").read_text(encoding="utf-8"))
        with conn.cursor() as cur:
            for table in ("teams", "people", "services", "service_dependencies", "assets",
                          "vulnerabilities", "asset_vulnerabilities"):
                data = rows[table]
                marks = ", ".join(["%s"] * len(data[0]))
                cur.executemany(f"INSERT INTO cmdb.{table} VALUES ({marks})", data)
                print(f"  cmdb.{table:<22} {len(data):>6} rows")
        conn.execute("ANALYZE")


def seed_opensearch(rng, anchor, assets, total: int) -> None:
    from opensearchpy import helpers

    client = admin_os()
    for name, body in OS_ROLES.items():
        client.transport.perform_request("PUT", f"/_plugins/_security/api/roles/{name}", body=body)
    for user, role in OS_USERS.items():
        client.transport.perform_request(
            "PUT", f"/_plugins/_security/api/internalusers/{user}",
            body={"password": os.environ[common_mcp.PERSONAS[user]], "backend_roles": []})
        client.transport.perform_request(
            "PUT", f"/_plugins/_security/api/rolesmapping/{role}", body={"users": [user]})
    print(f"  security: {len(OS_ROLES)} roles, {len(OS_USERS)} users")

    for pattern in ("logs-*", "edr-events-*", "incidents"):
        client.indices.delete(index=pattern, ignore_unavailable=True, expand_wildcards="all")
    client.indices.put_index_template(name="course-logs", body=generate.log_template())
    client.indices.create(index=generate.INCIDENTS_INDEX, body=generate.incidents_mapping())
    print(f"  template course-logs: {generate.mapped_field_count()} mapped fields")

    def actions():
        for index, doc_id, doc in generate.log_documents(rng, anchor, assets, total):
            yield {"_index": index, "_id": doc_id, "_source": doc}
        for doc_id, doc in generate.incidents(rng, anchor, assets):
            yield {"_index": generate.INCIDENTS_INDEX, "_id": doc_id, "_source": doc}

    started, ok, failed = time.time(), 0, 0
    for success, info in helpers.streaming_bulk(client, actions(), chunk_size=2000,
                                                raise_on_error=False, max_retries=3):
        if success:
            ok += 1
        else:
            failed += 1
            if failed <= 3:
                print("  bulk error:", info)
        if ok and ok % 50_000 == 0:
            print(f"  ...{ok} documents")
    client.indices.refresh(index="logs-*,edr-events-*,incidents")
    print(f"  opensearch: {ok} documents in {time.time() - started:.0f}s"
          + (f", {failed} FAILED" if failed else ""))
    if failed:
        raise SystemExit("Some documents were rejected; see the errors above.")


def cmd_seed(args) -> int:
    import random

    need_env_file()
    anchor = generate.parse_anchor(args.anchor)
    total = generate.SIZES[args.size]
    print(f"Seeding the estate: size={args.size} ({total} log documents), "
          f"anchor={anchor:%Y-%m-%d} UTC, seed={generate.SEED}")
    rng = random.Random(generate.SEED)
    rows = generate.cmdb(rng, anchor)
    print("Postgres (CMDB):")
    seed_postgres(rows)
    print("OpenSearch (SIEM / logs):")
    seed_opensearch(rng, anchor, rows["_assets"], total)
    print("\nSeeded. Next: python env/env.py check")
    return 0


def cmd_reset(args) -> int:
    need_env_file()
    return cmd_seed(args)


# =========================================================================================
# check
# =========================================================================================

class Report:
    def __init__(self):
        self.counts = {"PASS": 0, "WARN": 0, "FAIL": 0}

    def line(self, tag: str, msg: str, units: str = "") -> None:
        self.counts[tag] += 1
        print(f"  [{tag}] {msg}" + (f"  (affects {units})" if units and tag != "PASS" else ""))


def check_python(r: Report) -> None:
    print("\nPython and packages")
    v = sys.version_info
    if (3, 13) <= v[:2] < (4, 0):
        r.line("PASS", f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        r.line("FAIL", f"Python {v.major}.{v.minor} -- this course needs >= 3.13, < 4", "all units")
    for dist, want, units in (("mcp", "2", "all units"), ("marimo", "", "the labs"),
                              ("psycopg", "", "Units 0, 3+"), ("opensearch-py", "", "Units 0, 3+"),
                              ("openai", "", "Units 2-6, 10, 12")):
        try:
            got = metadata.version(dist)
        except metadata.PackageNotFoundError:
            r.line("FAIL", f"{dist} is not installed -- pip install -e .", units)
            continue
        if want and not got.startswith(want + "."):
            r.line("FAIL", f"{dist} {got} -- the course needs {want}.x", units)
        else:
            r.line("PASS", f"{dist} {got}")


def check_postgres(r: Report) -> None:
    print(f"\nPostgres (CMDB) at {os.environ.get('PG_HOST')}:{os.environ.get('PG_PORT')}")
    try:
        with common_mcp.pg_connect("l1_analyst") as l1, common_mcp.pg_connect("l3_responder") as l3:
            version = l3.execute("SHOW server_version").fetchone()[0]
            n1 = l1.execute("SELECT count(*) FROM cmdb.assets").fetchone()[0]
            n3 = l3.execute("SELECT count(*) FROM cmdb.assets").fetchone()[0]
            r.line("PASS", f"Postgres {version}; personas can log in")
            if n3 == 0:
                r.line("FAIL", "the CMDB is empty -- run: python env/env.py seed", "Units 0, 3+")
                return
            if n1 < n3:
                r.line("PASS", f"row-level security: L1 sees {n1} assets, L3 sees {n3}")
            else:
                r.line("FAIL", "row-level security is not hiding restricted assets from L1", "Unit 7")
            try:
                l1.execute("SELECT email FROM cmdb.people LIMIT 1")
                r.line("FAIL", "L1 can read people.email (column privilege missing)", "Unit 7")
            except Exception:
                l1.rollback()
                r.line("PASS", "column privileges: L1 cannot read people.email")
    except Exception as e:  # noqa: BLE001 -- the report is the point
        r.line("FAIL", f"cannot connect as a persona: {type(e).__name__}: {e}".strip(), "Units 0, 3+")


def check_opensearch(r: Report) -> None:
    print(f"\nOpenSearch (SIEM) at {os.environ.get('OS_HOST')}:{os.environ.get('OS_PORT')}")
    try:
        l1, l3 = common_mcp.os_client("l1_analyst"), common_mcp.os_client("l3_responder")
        info = l3.info()
        r.line("PASS", f"OpenSearch {info['version']['number']}; personas can log in")
        logs = l1.count(index="logs-*")["count"]
        if logs == 0:
            r.line("FAIL", "no log documents -- run: python env/env.py seed", "Units 0, 3+")
            return
        r.line("PASS", f"{logs} log documents visible to L1")
        hit = l1.search(index="logs-web-*", body={"size": 1, "query": {"exists": {"field": "user.name"}}})
        src = hit["hits"]["hits"][0]["_source"] if hit["hits"]["hits"] else {}
        if "email" in src.get("user", {}):
            r.line("FAIL", "field-level security: L1 can see user.email", "Unit 7")
        else:
            r.line("PASS", "field-level security: user.email hidden from L1")
        try:
            l1.count(index="edr-events-*")
            r.line("FAIL", "index permissions: L1 can read edr-events-*", "Unit 7")
        except Exception:
            r.line("PASS", "index permissions: L1 cannot read edr-events-*")
        incident = l3.get(index=generate.INCIDENTS_INDEX, id=generate.INCIDENT_ID)["_source"]
        r.line("PASS", f"planted incident {generate.INCIDENT_ID} on "
                       f"{incident['host']['name']} ({incident['incident']['created'][:10]})")
    except Exception as e:  # noqa: BLE001
        r.line("FAIL", f"cannot query as a persona: {type(e).__name__}: {e}".strip(), "Units 0, 3+")


def check_llm(r: Report) -> None:
    print("\nLLM endpoint (Foundations .env)")
    pair = common_mcp.llm()
    if pair is None:
        r.line("WARN", "OPENAI_BASE_URL / OPENAI_API_KEY not set -- load your Foundations "
                       ".env (set -a; source ../.env; set +a)", "Units 2-6, 10, 12")
        return
    client, model = pair
    tool = {"type": "function", "function": {
        "name": "get_asset_owner", "description": "Return the owner of a host.",
        "parameters": {"type": "object", "properties": {"hostname": {"type": "string"}},
                       "required": ["hostname"]}}}
    try:
        resp = client.chat.completions.create(
            model=model, tools=[tool], tool_choice="auto", max_tokens=400,
            messages=[{"role": "user", "content": "Who owns jump-prod-02? Use the tool."}])
        calls = resp.choices[0].message.tool_calls or []
        if calls and calls[0].function.name == "get_asset_owner":
            r.line("PASS", f"{model} answers and calls tools")
        else:
            r.line("WARN", f"{model} answered but did not call the tool", "Units 2-6, 10, 12")
    except Exception as e:  # noqa: BLE001
        r.line("WARN", f"tool-calling probe failed: {type(e).__name__}: {e}", "Units 2-6, 10, 12")


def cmd_check(args) -> int:
    if not ENV_FILE.exists():
        print("[WARN] env/.env not found -- using defaults and your shell environment.")
    r = Report()
    check_python(r)
    check_postgres(r)
    check_opensearch(r)
    if not args.no_llm:
        check_llm(r)
    c = r.counts
    print(f"\n{c['PASS']} passed, {c['WARN']} warnings, {c['FAIL']} failed.")
    if c["FAIL"]:
        print("Fix the FAIL lines first: Unit 0's tour and Units 3+ need the estate.")
    return 1 if c["FAIL"] else 0


# =========================================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("up")
    p.add_argument("--ui", action="store_true", help="also start OpenSearch Dashboards")
    p = sub.add_parser("down")
    p.add_argument("--volumes", action="store_true", help="also delete the data volumes")
    sub.add_parser("status")
    for name in ("seed", "reset"):
        p = sub.add_parser(name)
        p.add_argument("--size", choices=sorted(generate.SIZES), default="default")
        p.add_argument("--anchor", help="YYYY-MM-DD the data is built around (default: today)")
    p = sub.add_parser("check")
    p.add_argument("--no-llm", action="store_true", help="skip the LLM endpoint probe")
    args = parser.parse_args()
    return {"up": cmd_up, "down": cmd_down, "status": cmd_status, "seed": cmd_seed,
            "reset": cmd_reset, "check": cmd_check}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
