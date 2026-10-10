# The estate: the course's test environment

Every unit of Creating MCPs works against the same small enterprise estate:

| Service | What it plays | Image | Default port |
|---|---|---|---|
| Postgres | the **CMDB / IT referential**: teams, people, services, assets, CVEs | `postgres:17.11` | `5433` |
| OpenSearch | the **SIEM / log store**: auth, web and syslog logs, EDR events, incidents | `opensearchproject/opensearch:3.9.0` | `9200` (HTTPS) |
| OpenSearch Dashboards | optional UI for poking at the logs (`up --ui`) | `opensearch-dashboards:3.9.0` | `5601` |

The data is **generated and deterministic**: the same size and anchor date always produce exactly
the same estate, so your counts match the lessons and a reset puts everything back. It uses
documentation IP ranges and fictional `CVE-2099-*` ids, and it contains a planted intrusion
(incident `INC-0042`) plus a few deliberately hostile records that Unit 10 defends against.

You need **Docker** (Docker Desktop, Colima, Podman with the compose plugin, or a Linux engine)
and **Python 3.13+**. Plan on about **2 GB of RAM** for one estate.

## Laptop (the default)

From `creating-mcps/`:

```bash
pip install -e .                      # the course's Python dependencies
cp env/.env.example env/.env          # the defaults work on a laptop as they are
python env/env.py up                  # start Postgres + OpenSearch and wait until healthy
python env/env.py seed                # personas, schema, data (about a minute)
python env/env.py check               # PASS / WARN / FAIL, with the units each gap affects
```

Everything binds to `127.0.0.1`, so nothing is exposed to your network. When you're done for the
day, `python env/env.py down` stops the containers and keeps the data; `down --volumes` deletes
it. `python env/env.py reset` rebuilds the data from scratch at any point.

| Command | Does |
|---|---|
| `up [--ui]` | start the containers (and Dashboards with `--ui`), wait for health |
| `seed [--size small\|default\|large] [--anchor YYYY-MM-DD]` | create personas and roles, load the CMDB and the logs |
| `reset` | same options as `seed`; drops and rebuilds everything |
| `check [--no-llm]` | preflight the estate, the Python packages and the model endpoint |
| `status` | show the containers |
| `down [--volumes]` | stop the containers (and delete the data) |

**Sizes.** `small` is 20k log documents (seconds to load; what CI uses), `default` is 200k (about
30 seconds), `large` is 1M (for Unit 9's performance work; a few minutes, and give OpenSearch
`OS_HEAP=2g`). The CMDB is the same size at every setting.

**Anchor.** The logs cover the 21 days up to the anchor, which defaults to today at 00:00 UTC.
The incident happens two days before it. Pass `--anchor` to freeze the dates, for example so a
recorded walkthrough keeps matching.

## Shared lab server

For a class, an instructor can run the estate once on a server and have learners connect to it.

**On the server** (instructor):

1. Copy `env/.env.example` to `env/.env` and **change every password**: the two admin passwords
   and the four persona passwords.
2. Set `PG_BIND=0.0.0.0` and `OS_BIND=0.0.0.0`, and put the server behind a firewall that only
   admits the learners. The admin accounts are on the same ports.
3. Give OpenSearch a real certificate if you can (or distribute its CA), so learners can set
   `OS_VERIFY_CERTS=1`.
4. `python env/env.py up && python env/env.py seed`.
5. Hand out the server's address and the **persona** passwords only. Learners never need the
   admin credentials.

**On each learner's machine:**

1. `cp env/.env.example env/.env`, then set `PG_HOST` and `OS_HOST` to the server, and paste in
   the persona passwords.
2. Run only `python env/env.py check`. Never `up`, `seed` or `reset`: those are the
   instructor's.

**One estate, or one per learner?** A single shared estate is fine for Units 0–7 and most labs
after that. Two things don't share well:

- **Unit 8 is about limits, and the limits are per persona.** Every learner who connects as
  `l1_analyst` shares that role's 10 Postgres connections. A class running load generators against
  one estate will measure each other.
- **`reset` affects everyone.**

So for Unit 8 onwards, either take the load-testing labs back to laptops, or run one estate per
learner on the server. Each needs its own project name and ports in its own copy of the `.env`
(for example `COMPOSE_PROJECT_NAME=mcp-alice`, `PG_PORT=5501`, `OS_PORT=9301`,
`DASHBOARDS_PORT=5701`), and about 2 GB of RAM.

## The personas

`seed` creates the same four personas in Postgres and OpenSearch. Who you connect as decides what
comes back; none of it is enforced by course code.

| Persona | Who | CMDB (Postgres) | Logs (OpenSearch) | Limits |
|---|---|---|---|---|
| `l1_analyst` | tier-1 SOC analyst | all tables; **no `restricted` assets** (row-level security); **no `people.email` / `phone`** (column privileges) | `logs-*` and `incidents`; **no EDR**; `user.email` and `labels.api_token` hidden (field-level security) | 10 connections, 5 s per statement |
| `l3_responder` | tier-3 incident responder | everything | logs, incidents **and** `edr-events-*`; `user.email` **masked** to a stable hash; `labels.api_token` hidden | 20 connections, 15 s |
| `auditor` | compliance / audit | everything, plus the `audit` schema | only the gateway's own telemetry (`mcp-telemetry-*`) | 5 connections, 15 s |
| `mcp_service` | the gateway's own login | **nothing directly**: it must `SET ROLE` to the caller it acts for, and may append to `audit.tool_calls` | read logs, EDR and incidents; write its own telemetry (`mcp-telemetry-*`) | 50 connections, 15 s |

The admin accounts (`postgres`, `admin`) are for `up`, `seed` and `reset` only. No lesson
connects with them, and no MCP server you build should either.

## Troubleshooting

`python env/env.py check` is the first thing to run: each failure names the units it affects.

- **OpenSearch exits during startup with `max virtual memory areas vm.max_map_count [65530] is
  too low`.** The single-node setting in `compose.yaml` normally avoids this. If you still see it
  on Linux: `sudo sysctl -w vm.max_map_count=262144`. On Docker Desktop or Colima, set it inside
  the VM.
- **OpenSearch exits with a password error.** The security plugin scores `OS_ADMIN_PASSWORD` and
  rejects weak ones, and any that resemble the user name. Use a long mixed password like the
  example's, then `python env/env.py down --volumes && python env/env.py up`, because the admin
  password is fixed when the data volume is first created.
- **A persona can't log in to OpenSearch, or `seed` reports a password error.** The same rules
  apply to the persona passwords. Change the password in `env/.env` and run `seed` again.
- **`up` times out waiting for OpenSearch.** It needs about 1 GB of heap plus overhead. Give
  Docker more memory, or lower `OS_HEAP` to `512m` for the small data size.
- **Port already in use.** Change `PG_PORT`, `OS_PORT` or `DASHBOARDS_PORT` in `env/.env`. The
  Postgres default is 5433 so it doesn't collide with a local Postgres on 5432.
- **TLS errors talking to OpenSearch.** On a laptop, `OS_VERIFY_CERTS=0` accepts the demo
  certificate for that one local connection. On a shared server, set `OS_VERIFY_CERTS=1` and
  `OS_CA_CERTS` to the CA bundle your instructor gives you. Behind a corporate proxy, make sure
  the estate's host is in `NO_PROXY`.
- **Counts don't match the lesson.** Lessons show the `default` size. Check which size you seeded,
  and whether your anchor date differs (`reset --anchor` with the lesson's date fixes that).
