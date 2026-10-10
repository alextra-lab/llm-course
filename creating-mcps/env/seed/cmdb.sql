-- The CMDB / IT referential for the Creating MCPs course.
--
-- Run by `python env/env.py seed` as the Postgres admin, AFTER the four persona roles exist
-- (env.py creates them first, with the passwords from env/.env). Re-running is safe: the
-- schemas are dropped and rebuilt, so the data is always exactly what generate.py produces.
--
-- What it sets up, and which unit leans on it:
--   * the referential itself: teams, people, services, assets, CVEs   (Units 1-6)
--   * read-only grants per persona, column privileges on PII           (Unit 7)
--   * row-level security: L1 never sees `restricted` assets            (Unit 7)
--   * per-role statement_timeout and connection limits                 (Unit 8)
--   * an empty audit schema the gateway writes to later                (Unit 11)

DROP SCHEMA IF EXISTS cmdb CASCADE;
DROP SCHEMA IF EXISTS audit CASCADE;
CREATE SCHEMA cmdb;
CREATE SCHEMA audit;

CREATE TABLE cmdb.teams (
    id          integer PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    email       text NOT NULL,              -- a team mailbox, not personal data
    oncall_slack text NOT NULL
);

CREATE TABLE cmdb.people (
    id          integer PRIMARY KEY,
    full_name   text NOT NULL,
    email       text NOT NULL UNIQUE,       -- PII: L1 analysts cannot select this column
    phone       text NOT NULL,              -- PII: L1 analysts cannot select this column
    team_id     integer NOT NULL REFERENCES cmdb.teams(id),
    title       text NOT NULL
);

CREATE TABLE cmdb.services (
    id            integer PRIMARY KEY,
    name          text NOT NULL UNIQUE,
    tier          smallint NOT NULL CHECK (tier BETWEEN 1 AND 3),   -- 1 = business critical
    owner_team_id integer NOT NULL REFERENCES cmdb.teams(id),
    description   text NOT NULL
);

CREATE TABLE cmdb.service_dependencies (
    service_id    integer NOT NULL REFERENCES cmdb.services(id),
    depends_on_id integer NOT NULL REFERENCES cmdb.services(id),
    PRIMARY KEY (service_id, depends_on_id)
);

CREATE TABLE cmdb.assets (
    id             integer PRIMARY KEY,
    hostname       text NOT NULL UNIQUE,
    ip             inet NOT NULL,
    os             text NOT NULL,
    environment    text NOT NULL CHECK (environment IN ('prod', 'staging', 'dev')),
    classification text NOT NULL CHECK (classification IN ('public', 'internal', 'confidential', 'restricted')),
    service_id     integer NOT NULL REFERENCES cmdb.services(id),
    owner_id       integer NOT NULL REFERENCES cmdb.people(id),
    location       text NOT NULL,
    -- Free text written by humans. Free text is where hostile instructions hide (Unit 10).
    description    text NOT NULL,
    commissioned   date NOT NULL
);
CREATE INDEX ON cmdb.assets (environment);
CREATE INDEX ON cmdb.assets (service_id);

CREATE TABLE cmdb.vulnerabilities (
    cve_id   text PRIMARY KEY,             -- fictional ids (CVE-2099-*), never real ones
    severity text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    cvss     numeric(3, 1) NOT NULL,
    summary  text NOT NULL
);

CREATE TABLE cmdb.asset_vulnerabilities (
    asset_id    integer NOT NULL REFERENCES cmdb.assets(id),
    cve_id      text NOT NULL REFERENCES cmdb.vulnerabilities(cve_id),
    detected_on date NOT NULL,
    status      text NOT NULL CHECK (status IN ('open', 'mitigated', 'accepted')),
    PRIMARY KEY (asset_id, cve_id)
);

-- Filled by the gateway from Unit 11 on: who called which tool, as whom, under which verdict.
CREATE TABLE audit.tool_calls (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    session_id text NOT NULL,
    trace_id   text NOT NULL,
    step       integer NOT NULL,
    caller     text NOT NULL,
    tool       text NOT NULL,
    verdict    text NOT NULL,
    detail     jsonb NOT NULL DEFAULT '{}'
);

-- ---------------------------------------------------------------------------------------
-- Grants: read-only, per persona. Nobody but the admin can write the referential.
-- ---------------------------------------------------------------------------------------
GRANT USAGE ON SCHEMA cmdb TO l1_analyst, l3_responder, auditor;
GRANT USAGE ON SCHEMA audit TO auditor, mcp_service;

-- L3 responders and auditors read the whole referential.
GRANT SELECT ON ALL TABLES IN SCHEMA cmdb TO l3_responder, auditor;

-- L1 analysts read everything EXCEPT the personal columns of `people` (column privileges).
GRANT SELECT ON cmdb.teams, cmdb.services, cmdb.service_dependencies, cmdb.assets,
                cmdb.vulnerabilities, cmdb.asset_vulnerabilities TO l1_analyst;
GRANT SELECT (id, full_name, team_id, title) ON cmdb.people TO l1_analyst;

-- The gateway's own login may append to the audit log, and nothing else. To read the CMDB
-- it must act AS a caller (SET ROLE), which is the least-privilege pattern of Unit 7.
GRANT INSERT ON audit.tool_calls TO mcp_service;
GRANT USAGE ON SEQUENCE audit.tool_calls_id_seq TO mcp_service;
GRANT SELECT ON audit.tool_calls TO auditor;

-- ---------------------------------------------------------------------------------------
-- Row-level security: an L1 analyst never sees `restricted` assets, nor their CVEs.
-- ---------------------------------------------------------------------------------------
ALTER TABLE cmdb.assets ENABLE ROW LEVEL SECURITY;
CREATE POLICY l1_unrestricted ON cmdb.assets FOR SELECT TO l1_analyst
    USING (classification <> 'restricted');
CREATE POLICY see_all ON cmdb.assets FOR SELECT TO l3_responder, auditor
    USING (true);

ALTER TABLE cmdb.asset_vulnerabilities ENABLE ROW LEVEL SECURITY;
-- The sub-select runs under the caller's own RLS, so this follows the assets policy.
CREATE POLICY follows_assets ON cmdb.asset_vulnerabilities FOR SELECT TO l1_analyst
    USING (asset_id IN (SELECT id FROM cmdb.assets));
CREATE POLICY see_all ON cmdb.asset_vulnerabilities FOR SELECT TO l3_responder, auditor
    USING (true);
