"""
Unit 7: the policy layer -- enterprise rules as data (policy.toml), evaluated on every call.

    grants   group -> tools it may use at all (drives tools/list too: no grant, no tool)
    rules    conditions on a granted call; the first match decides deny or approve;
             no match allows

A verdict is data too: which rule decided, and what to tell the caller. The server logs every
verdict, allow and deny alike, so "why was this refused?" and "who was allowed what?" are both
answerable from the log.

Conditions (`when`) a rule may use:

    filters_any = [fields]          the call filters on any of these fields
    host_hidden_from_caller = true  the call is about a host the caller can't see in the CMDB
    read_only = false               the tool isn't annotated read-only
    args = { name = value }         an argument equals a value (or is in a list of them)

`host_hidden_from_caller` needs a fact from the CMDB. The server supplies it, looked up AS
the caller, so Postgres's row-level security answers the question.

    python examples/07/policy.py --test       # the rules against a table of cases
    python examples/07/policy.py --explain    # what each group may see and what can stop it
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
POLICY_FILE = HERE / "policy.toml"
EFFECTS = ("allow", "deny", "approve")


@dataclass(frozen=True)
class Caller:
    """Who is calling, from a verified token. Never the token itself."""
    sub: str
    groups: tuple[str, ...]
    persona: str | None          # the backend identity the gateway acts as (None: no access)
    client_id: str = ""
    jti: str = ""                # the token's id: logs name the token without containing it


@dataclass(frozen=True)
class Verdict:
    effect: str                  # allow | deny | approve
    rule: str                    # the rule that decided ("granted" when none matched)
    message: str = ""


@dataclass
class Policy:
    personas: dict[str, str]
    grants: dict[str, list[str]]
    rules: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path = POLICY_FILE) -> Policy:
        return cls.loads(path.read_text(encoding="utf-8"))

    @classmethod
    def loads(cls, text: str) -> Policy:
        data = tomllib.loads(text)
        policy = cls(data.get("personas", {}), data.get("grants", {}), data.get("rule", []))
        policy.validate()
        return policy

    def validate(self) -> None:
        """Refuse to start on a policy that doesn't mean what it says."""
        known = {"filters_any", "host_hidden_from_caller", "read_only", "args"}
        seen = set()
        for r in self.rules:
            rid = r.get("id")
            if not rid or rid in seen:
                raise ValueError(f"every rule needs a unique id (got {rid!r})")
            seen.add(rid)
            if r.get("effect") not in ("deny", "approve"):
                raise ValueError(f"rule {rid}: effect must be deny or approve")
            unknown = set(r.get("when", {})) - known
            if unknown:
                raise ValueError(f"rule {rid}: unknown condition(s) {sorted(unknown)}")
        for group in self.grants:
            if group not in self.personas:
                raise ValueError(f"group {group!r} is granted tools but maps to no persona")

    # ---- who ---------------------------------------------------------------------------

    def caller(self, sub: str, groups, client_id: str = "", jti: str = "") -> Caller:
        groups = tuple(groups or ())
        persona = next((self.personas[g] for g in groups if g in self.personas), None)
        return Caller(sub, groups, persona, client_id, jti)

    # ---- step 1: grants ----------------------------------------------------------------

    def granted(self, caller: Caller, tool: str) -> bool:
        return caller.persona is not None and any(
            fnmatch.fnmatchcase(tool, pattern)
            for g in caller.groups for pattern in self.grants.get(g, ()))

    # ---- step 2: rules -----------------------------------------------------------------

    def evaluate(self, caller: Caller, tool: str, args: dict, annotations: dict | None = None,
                 host_visible: Callable[[str], bool] | None = None) -> Verdict:
        if not self.granted(caller, tool):
            return Verdict("deny", "not-granted", f"Unknown tool: {tool}")
        facts = Facts(args, annotations or {}, host_visible)
        for r in self.rules:
            if r.get("groups") and not set(r["groups"]) & set(caller.groups):
                continue
            if r.get("tools") and tool not in r["tools"]:
                continue
            if all(facts.holds(name, want) for name, want in r.get("when", {}).items()):
                return Verdict(r["effect"], r["id"], facts.fill(r.get("message", "")))
        return Verdict("allow", "granted")

    def explain(self) -> list[dict]:
        out = []
        for group, persona in self.personas.items():
            tools = self.grants.get(group, [])
            stops = [r["id"] for r in self.rules
                     if not r.get("groups") or group in r["groups"]]
            out.append({"group": group, "acts as": persona, "tools": ", ".join(tools) or "-",
                        "rules that apply": ", ".join(stops)})
        return out


class Facts:
    """What a rule's conditions are evaluated against: the call, and the server's facts."""

    def __init__(self, args: dict, annotations: dict, host_visible):
        self.args, self.annotations, self.host_visible = args, annotations, host_visible

    def filters(self) -> dict:
        f = self.args.get("filters") or {}
        return f if isinstance(f, dict) else {}

    def host(self) -> str | None:
        """The one host this call is about, if it names one."""
        for value in (self.args.get("host"), self.args.get("hostname"),
                      self.filters().get("host.name")):
            if isinstance(value, str) and value:
                return value
        return None

    def holds(self, name: str, want: Any) -> bool:
        if name == "filters_any":
            return any(f in self.filters() for f in want)
        if name == "host_hidden_from_caller":
            host = self.host()
            hidden = host is not None and self.host_visible is not None and not (
                self.host_visible(host))
            return hidden == want
        if name == "read_only":
            return bool(self.annotations.get("readOnlyHint", False)) == want
        if name == "args":
            return all(self.args.get(k) == v or (isinstance(v, list) and self.args.get(k) in v)
                       for k, v in want.items())
        raise ValueError(f"unknown condition {name!r}")

    def fill(self, message: str) -> str:
        values = {**{k: v for k, v in self.args.items() if isinstance(v, (str, int))},
                  "host": self.host() or "that host", "user": self.filters().get("user.name", "")}
        return " ".join(message.format_map(_Default(values)).split())


class _Default(dict):
    def __missing__(self, key):
        return "{" + key + "}"


# =========================================================================================
# The table of cases: what the rules must decide (run in CI)
# =========================================================================================

RO = {"readOnlyHint": True}
CASES = [
    # who,     tool,            args,                                     annotations, expect
    ("priya", "get_asset", {"hostname": "db-prod-01"}, RO, ("allow", "granted")),
    ("priya", "get_contact", {"hostname": "jump-prod-02"}, RO, ("deny", "not-granted")),
    ("marc", "get_contact", {"hostname": "jump-prod-02"}, RO,
     ("approve", "contacts-need-a-ticket")),
    ("zoe", "search_events", {"dataset": "auth"}, RO, ("deny", "not-granted")),
    ("zoe", "count_assets", {"environment": "prod"}, RO, ("allow", "granted")),
    ("sam", "get_asset", {"hostname": "jump-prod-02"}, RO, ("deny", "not-granted")),
    ("priya", "search_events", {"dataset": "auth", "filters": {"host.name": "db-prod-01"}}, RO,
     ("deny", "restricted-hosts-stay-restricted")),
    ("priya", "list_incidents", {"host": "db-prod-01"}, RO,
     ("deny", "restricted-hosts-stay-restricted")),
    ("priya", "search_events", {"dataset": "auth", "filters": {"host.name": "jump-prod-02"}},
     RO, ("allow", "granted")),
    ("marc", "search_events", {"dataset": "auth", "filters": {"host.name": "db-prod-01"}}, RO,
     ("allow", "granted")),
    ("priya", "top_values", {"dataset": "auth", "field": "source.ip",
                             "filters": {"user.name": "svc_backup"}}, RO,
     ("deny", "no-person-lookups-at-l1")),
    ("marc", "search_events", {"dataset": "auth", "filters": {"user.name": "svc_backup"}}, RO,
     ("approve", "person-lookups-need-a-ticket")),
    ("marc", "isolate_host", {"hostname": "jump-prod-02"}, {"readOnlyHint": False},
     ("approve", "writes-need-approval")),
    ("marc", "isolate_host", {"hostname": "jump-prod-02"}, {}, ("approve", "writes-need-approval")),
]
VISIBLE_TO_PRIYA = {"jump-prod-02"}          # db-prod-01 is restricted: RLS hides it from L1


def run_cases(policy: Policy) -> list[dict]:
    from issuer import DIRECTORY

    rows = []
    for who, tool, args, hints, (effect, rule) in CASES:
        caller = policy.caller(who, DIRECTORY[who])
        visible = (lambda h: h in VISIBLE_TO_PRIYA) if caller.persona == "l1_analyst" else (
            lambda h: True)
        v = policy.evaluate(caller, tool, args, hints, visible)
        rows.append({"who": who, "tool": tool, "args": args, "verdict": v.effect,
                     "rule": v.rule, "ok": (v.effect, v.rule) == (effect, rule),
                     "expected": f"{effect} ({rule})"})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Check or explain the Unit 7 policy.")
    parser.add_argument("--policy", type=Path, default=POLICY_FILE)
    parser.add_argument("--test", action="store_true", help="run the table of cases")
    parser.add_argument("--explain", action="store_true", help="what each group may do")
    args = parser.parse_args()
    sys.path.insert(0, str(HERE))
    policy = Policy.load(args.policy)
    print(f"{args.policy.name}: {len(policy.grants)} grants, {len(policy.rules)} rules -- valid")
    if args.explain or not args.test:
        for row in policy.explain():
            print(f"\n  {row['group']} (acts as {row['acts as']})\n    tools: {row['tools']}\n"
                  f"    rules: {row['rules that apply']}")
    if args.test:
        rows = run_cases(policy)
        for r in rows:
            print((f"  {'ok ' if r['ok'] else 'BAD'} {r['who']:<6} {r['tool']:<16} -> "
                   f"{r['verdict']:<7} {r['rule']}"
                   + ("" if r["ok"] else f" (expected {r['expected']})")).rstrip())
        bad = sum(not r["ok"] for r in rows)
        print(f"\n{len(rows) - bad}/{len(rows)} cases decided as expected.")
        if bad:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
