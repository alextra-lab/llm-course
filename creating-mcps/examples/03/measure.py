"""
Unit 3: what does it cost a model to learn what data exists?

Runs the raw-discovery server and the catalog server through the Unit 2 host, as one persona,
and measures what each approach puts into the model's context: the tool definitions (sent on
every model call), and the results of discovering everything once. Bytes are exact; tokens
are estimated at 4 bytes per token (eval_discovery.py reports the model's real counts).

    python examples/03/measure.py                       # l1_analyst
    python examples/03/measure.py --persona l3_responder
"""

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent))  # creating-mcps/examples/
from common_mcp import load_env, os_client  # noqa: E402

_spec = importlib.util.spec_from_file_location("unit2_host", HERE.parent / "02" / "host.py")
unit2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(unit2)

SERVERS = {"raw": HERE / "raw_server.py", "catalog": HERE / "catalog_server.py"}


def tokens(n_bytes: int) -> int:
    return round(n_bytes / 4)


async def _declined(_server, _params):
    return unit2.ElicitResult(action="decline")


async def measure(persona: str) -> list[dict]:
    """One row per (approach, item): bytes that would land in the model's context."""
    rows = []

    def add(approach, what, text, calls=1):
        rows.append({"approach": approach, "what": what, "calls": calls,
                     "bytes": len(text.encode()), "tokens": tokens(len(text.encode()))})

    async with unit2.Host(SERVERS, _declined, env={"MCP_PERSONA": persona}) as host:
        defs = {name: [t for t in host.openai_tools() if t["function"]["name"].startswith(name)]
                for name in SERVERS}
        add("raw", "tool definitions (every model call)", json.dumps(defs["raw"]), 0)
        add("curated", "tool definitions (every model call)", json.dumps(defs["catalog"]), 0)
        add("curated", "server instructions (once)", host.clients["catalog"].instructions or "", 0)

        async def call(key, args):
            text, _ = await host.call(key, args, "measure", "0" * 32, 0)
            return text

        # Raw: list, then describe everything -- one daily index per pattern, as a careful
        # model would. (Describing the pattern itself is measured separately, below.)
        tables = json.loads(await call("raw__list_tables", {}))["result"]
        add("raw", "list_tables", json.dumps(tables))
        described = [await call("raw__describe_table", {"table": f"{t['schema']}.{t['table']}"})
                     for t in tables if t["schema"] == "cmdb"]
        add("raw", f"describe_table x{len(described)}", "".join(described), len(described))
        indexes = json.loads(await call("raw__list_indexes", {}))["result"]
        add("raw", "list_indexes", json.dumps(indexes))
        newest = {}                      # sorted by name, so the last of each prefix wins
        for i in indexes:
            newest[i["index"].rsplit("-", 1)[0]] = i["index"]
        mappings = [await call("raw__describe_index", {"index": name}) for name in newest.values()]
        add("raw", f"describe_index x{len(mappings)} (newest daily index each)",
            "".join(mappings), len(mappings))

        # Curated: list, then describe every dataset this persona may use.
        listing = await call("catalog__list_datasets", {})
        add("curated", "list_datasets", listing)
        names = [d["name"] for d in json.loads(listing)["result"]]
        described = [await call("catalog__describe_dataset", {"name": n}) for n in names]
        add("curated", f"describe_dataset x{len(described)}", "".join(described), len(described))
        whole = await host.clients["catalog"].read_resource("catalog://datasets")
        add("curated (resource)", "catalog://datasets, loaded up front", whole.contents[0].text, 0)

    # What "describe the pattern" really returns, before the raw server's 100 kB cut-off.
    client = os_client(persona)
    for pattern in ("logs-*",):
        add("raw (uncapped)", f"_mapping for {pattern}",
            json.dumps(client.indices.get_mapping(index=pattern)), 0)
    return rows


def totals(rows: list[dict]) -> dict[str, dict]:
    """Discover-everything totals per approach, tool definitions included once."""
    out = {}
    for approach in ("raw", "curated"):
        mine = [r for r in rows if r["approach"] == approach]
        out[approach] = {"calls": sum(r["calls"] for r in mine),
                         "bytes": sum(r["bytes"] for r in mine)}
        out[approach]["tokens"] = tokens(out[approach]["bytes"])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="The token cost of discovery, raw vs curated.")
    parser.add_argument("--persona", default="l1_analyst")
    args = parser.parse_args()
    load_env()
    rows = asyncio.run(measure(args.persona))
    print(f"as {args.persona}:")
    for r in rows:
        print(f"  {r['approach']:<19} {r['what']:<44} {r['calls']:>3} calls "
              f"{r['bytes']:>10,} bytes  ~{r['tokens']:>8,} tokens")
    for approach, t in totals(rows).items():
        print(f"  TOTAL {approach:<13} discover everything: {t['calls']:>3} calls "
              f"{t['bytes']:>10,} bytes  ~{t['tokens']:>8,} tokens")


if __name__ == "__main__":
    main()
