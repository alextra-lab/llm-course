"""
Unit 6: does the model pick the right tool, with the right arguments? A golden set, A/B'd.

Twenty requests an analyst might make, each labelled with the tool(s) that should answer it
first and the arguments that matter. Four of them should get *no* tool call: nothing on the
menu can do them (a restart), or this persona isn't allowed the data (EDR for L1).

For each description style (styles.py) and each repeat, every request gets ONE model call
with the nine tools; the first tool call it makes is graded and then actually run:

    right_tool   the first call is one of the expected tools (or no call, when none is)
    right_args   ... and its arguments include the expected values
    call_ok      that first call ran without an error

It reports each as a mean with the min-max across repeats, plus the prompt tokens per
request. One model call per request keeps it cheap enough to run often; the multi-step
version, with errors to recover from, is recovery.py.

    python examples/06/selection.py --check              # the golden set is consistent
    python examples/06/selection.py --repeats 3          # every style
    python examples/06/selection.py --styles terse rewritten --repeats 5 --out sel.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "05"), str(HERE.parent)]
import styles  # noqa: E402
from common_mcp import llm, load_env  # noqa: E402
from grid import SYSTEM, _approve, unit2  # noqa: E402  (Unit 5)

C, S = "cmdb__", "siem__"
GOLDEN = [
    # CMDB
    {"ask": "Who owns db-prod-01?", "expect": [C + "get_asset"],
     "args": {"hostname": "db-prod-01"}},
    {"ask": "What operating system does jump-prod-02 run?", "expect": [C + "get_asset"],
     "args": {"hostname": "jump-prod-02"}},
    {"ask": "List the staging hosts that run payments-api.", "expect": [C + "find_assets"],
     "args": {"service": "payments-api", "environment": "staging"}},
    {"ask": "Which services would break if customer-db went down?",
     "expect": [C + "service_dependencies"], "args": {"service": "customer-db"}},
    {"ask": "What does order-service depend on?", "expect": [C + "service_dependencies"],
     "args": {"service": "order-service"}},
    {"ask": "Which production hosts still have CVE-2099-1068 open?",
     "expect": [C + "hosts_with_vulnerability"],
     "args": {"cve_id": "CVE-2099-1068", "environment": "prod"}},
    {"ask": "Has CVE-2099-1068 been mitigated on any host?",
     "expect": [C + "hosts_with_vulnerability"],
     "args": {"cve_id": "CVE-2099-1068", "status": "mitigated"}},
    {"ask": "How many restricted hosts are there in production?",
     "expect": [C + "count_assets"],
     "args": {"environment": "prod", "classification": "restricted"}},
    {"ask": "How many business-critical (tier 1) hosts do we have in production?",
     "expect": [C + "count_assets"], "args": {"environment": "prod", "tier": 1}},
    # SIEM
    {"ask": "Which incidents are still open?", "expect": [S + "list_incidents"], "args": {}},
    {"ask": "Show me incident INC-0042.", "expect": [S + "get_incident"],
     "args": {"incident_id": "INC-0042"}},
    {"ask": "Are there any high-severity incidents about jump-prod-02?",
     "expect": [S + "list_incidents"], "args": {"severity": "high", "host": "jump-prod-02"}},
    {"ask": "When did failed logins spike over the last three days?",
     "expect": [S + "event_histogram"], "args": {"dataset": "auth"}},
    {"ask": "How many HTTP 5xx errors did the web servers return per hour over the last day?",
     "expect": [S + "event_histogram"], "args": {"dataset": "web"}},
    {"ask": "Which source IPs had the most failed logins in the last 24 hours?",
     "expect": [S + "top_values"], "args": {"dataset": "auth", "field": "source.ip"}},
    {"ask": "Show me the syslog messages from db-prod-01 in the last hour.",
     "expect": [S + "search_events"], "args": {"dataset": "syslog"}},
    # nothing on the menu, or not this persona's data
    {"ask": "Restart the backup service on db-prod-01.", "expect": [], "args": {}},
    {"ask": "Isolate jump-prod-02 from the network now.", "expect": [], "args": {}},
    {"ask": "Show me the EDR process events on jump-prod-02 from the last hour.",
     "expect": [], "args": {}, "persona": "l1_analyst"},
    {"ask": "What will the weather be in Paris tomorrow?", "expect": [], "args": {}},
]


def items_for(persona: str) -> list[dict]:
    """The golden set for one persona: some items only make sense for one of them."""
    return [i for i in GOLDEN if i.get("persona", persona) == persona]


def matches(expected: dict, got: dict) -> bool:
    def norm(v):
        return str(v).strip().lower()
    return all(norm(got.get(k)) == norm(v) for k, v in expected.items())


def check(tools: dict) -> list[str]:
    """Problems with the golden set against one style's tool table (empty = consistent)."""
    problems = []
    for item in GOLDEN:
        for key in item["expect"]:
            if key not in tools:
                problems.append(f"{item['ask']!r}: no tool {key}")
                continue
            params = tools[key][1].input_schema.get("properties", {})
            problems += [f"{item['ask']!r}: {key} has no parameter {a!r}"
                         for a in item["args"] if a not in params]
        if not item["expect"] and item["args"]:
            problems.append(f"{item['ask']!r}: expects no call but lists arguments")
    return problems


async def ask_once(host, client, model, item) -> dict:
    """One model call; grade and run the first tool call it makes."""
    t0 = time.time()
    response = await asyncio.to_thread(
        client.chat.completions.create, model=model, tools=host.openai_tools(),
        tool_choice="auto", messages=[{"role": "system", "content": SYSTEM},
                                      {"role": "user", "content": item["ask"]}])
    msg = response.choices[0].message
    usage = response.usage.prompt_tokens if response.usage else 0
    first = msg.tool_calls[0] if msg.tool_calls else None
    out = {"ask": item["ask"], "expected": ", ".join(item["expect"]) or "(no call)",
           "called": first.function.name if first else "(no call)",
           "prompt_tokens": usage, "seconds": 0.0}
    if first is None:
        out.update(right_tool=not item["expect"], right_args=not item["expect"], call_ok=True)
    else:
        try:
            args = json.loads(first.function.arguments or "{}")
        except json.JSONDecodeError:
            args = None
        out["right_tool"] = first.function.name in item["expect"]
        out["right_args"] = out["right_tool"] and args is not None and matches(item["args"],
                                                                               args)
        if args is None:
            out["call_ok"] = False
        else:
            _text, ev = await host.call(first.function.name, args, "selection", "0" * 32, 1)
            out["call_ok"] = ev.outcome == "ok"
        out["arguments"] = first.function.arguments
    out["seconds"] = round(time.time() - t0, 1)
    return out


async def run(persona: str, which: list[str], repeats: int, on_result=None) -> list[dict]:
    client, model = llm()
    results = []
    for style in which:
        async with unit2.Host(styles.server_table(style, persona), _approve) as host:
            for repeat in range(1, repeats + 1):
                for item in items_for(persona):
                    r = {"style": style, "repeat": repeat,
                         **await ask_once(host, client, model, item)}
                    results.append(r)
                    if on_result:
                        on_result(r)
    return results


def summarise(results: list[dict]) -> list[dict]:
    out = []
    for style in dict.fromkeys(r["style"] for r in results):
        mine = [r for r in results if r["style"] == style]
        row = {"style": style, "repeats": len({r["repeat"] for r in mine}),
               "prompt_tokens": round(statistics.mean(r["prompt_tokens"] for r in mine))}
        for key in ("right_tool", "right_args", "call_ok"):
            per = [statistics.mean(r[key] for r in mine if r["repeat"] == k)
                   for k in sorted({r["repeat"] for r in mine})]
            row[key] = round(statistics.mean(per), 3)
            row[key + "_range"] = f"{min(per):.0%}-{max(per):.0%}"
        out.append(row)
    return out


async def check_all(persona: str) -> dict[str, list[str]]:
    out = {}
    for style in styles.STYLES:
        async with unit2.Host(styles.server_table(style, persona), _approve) as host:
            out[style] = check(host.tools)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Tool-selection accuracy per style.")
    parser.add_argument("--persona", default="l1_analyst")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--styles", nargs="+", choices=styles.STYLES, default=list(styles.STYLES))
    parser.add_argument("--check", action="store_true", help="only check the golden set")
    parser.add_argument("--out", help="also write every result as JSONL here")
    args = parser.parse_args()
    load_env()
    problems = asyncio.run(check_all(args.persona))
    for style, found in problems.items():
        print(f"golden set vs {style}: " + ("; ".join(found) if found else "consistent"))
    if any(problems.values()):
        raise SystemExit(1)
    if args.check:
        return
    if llm() is None:
        print("OPENAI_BASE_URL / OPENAI_API_KEY not set -- the eval needs the model. "
              "--check, toolcost.py, lint.py and errors.py work without it.")
        return

    def show(r):
        mark = "ok " if r["right_args"] else ("~  " if r["right_tool"] else "BAD")
        print(f"{r['style']:<10} #{r['repeat']} {mark} {r['ask'][:48]:<48} -> {r['called']}"
              + ("" if r["call_ok"] else "  (call failed)"))
    results = asyncio.run(run(args.persona, args.styles, args.repeats, show))
    if args.out:
        Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in results))
    print()
    for s in summarise(results):
        print(f"{s['style']:<10} right tool {s['right_tool']:.0%} ({s['right_tool_range']})  "
              f"right args {s['right_args']:.0%} ({s['right_args_range']})  "
              f"call ok {s['call_ok']:.0%} ({s['call_ok_range']})  "
              f"~{s['prompt_tokens']:,} prompt tokens  over {s['repeats']} runs")


if __name__ == "__main__":
    main()
